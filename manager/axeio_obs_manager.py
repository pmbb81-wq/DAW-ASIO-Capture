#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AXE I/O ONE -> OBS / DAW Manager (single GUI)

  Sekcja 1: MOST - przesyla dzwiek z ONE do wirtualnego kabla (VB-CABLE),
            ktory OBS nagrywa jako "CABLE Output" (osobna sciezka).
  Sekcja 2: FLEXASIO (wirtualny sterownik ASIO) - wykrywanie, instalacja,
            profile "JAM VOX" / "OBS" zapisujace %USERPROFILE%\\FlexASIO.toml,
            zeby DAW-y (lub JAM VOX) widzialy nasze urzadzenie jako "FlexASIO".
"""
import os
import sys
import re
import json
import time
import array
import queue
import ctypes
import shutil
import argparse
import threading
import datetime
import subprocess
import wave
from ctypes import wintypes

import winreg
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

import sounddevice as sd

FLX_INSTALLER = "FlexASIO-1.10b.exe"
FLX_CONFIG = os.path.join(os.environ.get("USERPROFILE", ""), "FlexASIO.toml")

ASIO_PROXY_DLL = "asio-proxy.dll"
ASIO_PROXY_X86_DLL = "asio-proxy-x86.dll"
ASIO_CAPTURE_DLL = "obs-daw-capture.dll"

REC_CONFIG = "recorder_config.json"
MON_CONFIG = "monitor_config.json"
AC_CHOICE = "asio_choice.txt"

# ---------------------------------------------------------------------------
#  Nagrywarka (DAW) - odczyt wspolnej pamieci proxy.
#  Layout MUSI byc identyczny z shared/shared-memory.hpp (DAW-ASIO-Capture):
#      @0  uint32 sampleRate
#      @4  uint32 numChannels
#      @8  uint32 bufferFrames
#      @12 int64  writePos   (klatka, ktora proxy zapisze jako nastepna)
#      @20 int64  readPos    (uzywana przez zrodlo OBS - my jej NIE ruszamy,
#                             dzieki temu nagranie dziala rownoczesnie)
#      @28 int32  active     (1 gdy streamuje)
#      @32 int32  _pad
#      @36 float32 data[RING_FRAMES * DAW_MAX_CHANNELS], interleaved
# ---------------------------------------------------------------------------
RING_FRAMES = 131072            # 2^17
DAW_MAX_CHANNELS = 8
REC_CHUNK = 256                 # klatek na odczyt (~5.8 ms @ 44.1 kHz)
SHM_NAME = r"Local\OBSDAWCapture_Shm"
FILE_MAP_READ = 0x0004


def exe_dir():
    """Katalog .exe (nie _MEIPASS - tam konfig bylyby tymczasowe)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def load_config(name, defaults):
    p = os.path.join(exe_dir(), name)
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in defaults.items():
                data.setdefault(k, v)
            return data
    except Exception:
        pass
    return dict(defaults)


def save_config(name, data):
    p = os.path.join(exe_dir(), name)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def mon_log(msg):
    """Krotki ślad zdarzeń odsłuchu do pliku - błędy podpięcia bywają
    niewidoczne w GUI, a bez nich diagnoza staje w miejscu."""
    try:
        p = os.path.join(exe_dir(), "monitor.log")
        with open(p, "a", encoding="utf-8") as f:
            f.write("%s pid=%d %s\n"
                    % (time.strftime("%H:%M:%S"), os.getpid(), msg))
    except Exception:
        pass


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    for c in (r"C:\ffmpeg\bin\ffmpeg.exe",
              os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                           "ffmpeg", "bin", "ffmpeg.exe")):
        if os.path.exists(c):
            return c
    return None


def interleave_s16(l, r):
    """float32 -> int16 interleaved stereo, z clampem."""
    a = array.array("h", bytes(4 * len(l)))
    for i in range(len(l)):
        sl = l[i] * 32767.0
        sr = r[i] * 32767.0
        if sl > 32767.0:
            sl = 32767.0
        elif sl < -32768.0:
            sl = -32768.0
        if sr > 32767.0:
            sr = 32767.0
        elif sr < -32768.0:
            sr = -32768.0
        a[2 * i] = int(sl)
        a[2 * i + 1] = int(sr)
    return a.tobytes()


class _ShmMap(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("sampleRate",   ctypes.c_uint32),
        ("numChannels",  ctypes.c_uint32),
        ("bufferFrames", ctypes.c_uint32),
        ("writePos",     ctypes.c_int64),
        ("readPos",      ctypes.c_int64),
        ("active",       ctypes.c_int32),
        ("_pad",         ctypes.c_int32),
        ("data",         ctypes.c_float * (RING_FRAMES * DAW_MAX_CHANNELS)),
    ]


class ShmReader:
    """Mapuje wspolna pamiec proxy TYLKO do odczytu, wiec nie moze zaklocic
    ani proxy, ani zrodla OBS. readPos celowo nie jest zapisywany."""

    def __init__(self):
        self.view = None
        self.hmap = None
        self.map = None
        self.floats = None
        self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    def open(self):
        k32 = self.k32
        k32.OpenFileMappingW.restype = wintypes.HANDLE
        k32.MapViewOfFile.restype = ctypes.c_void_p
        h = k32.OpenFileMappingW(FILE_MAP_READ, False, SHM_NAME)
        if not h:
            raise OSError(ctypes.get_last_error(),
                          "brak aktywnego proxy (wspolna pamiec nie istnieje)")
        view = k32.MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
        if not view:
            k32.CloseHandle(h)
            raise OSError(ctypes.get_last_error(), "MapViewOfFile")
        self.hmap, self.view = h, view
        self.map = ctypes.cast(view, ctypes.POINTER(_ShmMap)).contents
        self.floats = ctypes.cast(ctypes.addressof(self.map.data),
                                   ctypes.POINTER(ctypes.c_float))
        return True

    def close(self):
        if self.view:
            self.k32.UnmapViewOfFile(ctypes.c_void_p(self.view))
            self.view = None
        if self.hmap:
            self.k32.CloseHandle(self.hmap)
            self.hmap = None
        self.map = None
        self.floats = None

    def header(self):
        m = self.map
        return (int(m.sampleRate), int(m.numChannels), int(m.bufferFrames),
                int(m.writePos), int(m.active))

    def chunk(self, frame_idx, ch_l, ch_r):
        """(lewy[], prawy[]) dla REC_CHUNK klatek zaczynajac od frame_idx.
        Ring ma 2^17 klatek, wiec chunk moze przejsc przez zawiniecie."""
        fl = self.floats
        base = frame_idx & (RING_FRAMES - 1)
        l = [0.0] * REC_CHUNK
        r = [0.0] * REC_CHUNK
        first = min(REC_CHUNK, RING_FRAMES - base)
        off = base * DAW_MAX_CHANNELS
        for i in range(first):
            o = off + i * DAW_MAX_CHANNELS
            l[i] = fl[o + ch_l]
            r[i] = fl[o + ch_r]
        if first < REC_CHUNK:
            for i in range(first, REC_CHUNK):
                o = (i - first) * DAW_MAX_CHANNELS
                l[i] = fl[o + ch_l]
                r[i] = fl[o + ch_r]
        return l, r


    def read_pair(self, frame_idx, ch_l, ch_r, n):
        """(array('f') lewy, prawy) n klatek od frame_idx - jednym memcpy
        zamiast petli po klatkach. Ring ma 2^17 klatek, wiec okno moze
        przejsc przez zawiniecie. Uzywane przez monitoring (wazne dla
        dzialania w czasie rzeczywistym)."""
        cbuf = (ctypes.c_float * (n * DAW_MAX_CHANNELS))()
        dst = ctypes.addressof(cbuf)
        base = frame_idx & (RING_FRAMES - 1)
        first = min(n, RING_FRAMES - base)
        src = ctypes.addressof(self.map.data)
        ctypes.memmove(dst, src + base * DAW_MAX_CHANNELS * 4,
                       first * DAW_MAX_CHANNELS * 4)
        if first < n:
            ctypes.memmove(dst + first * DAW_MAX_CHANNELS * 4, src,
                           (n - first) * DAW_MAX_CHANNELS * 4)
        flat = array.array("f")
        flat.frombytes(memoryview(cbuf).cast("B"))
        return flat[ch_l::DAW_MAX_CHANNELS], flat[ch_r::DAW_MAX_CHANNELS]


# ---------------------------------------------------------------------------
# Direct monitoring: Manager prosi proxy, zeby TO ONO gralo do sluchawek.
# Wzor: DAW-ASIO-Capture/shared/direct-monitor.hpp - te same pola i offsety.
# ---------------------------------------------------------------------------

DIRECT_CTL_NAME = r"Local\OBSDAWCapture_DirectCtl"
DIRECT_CTL_MAGIC = 0x54435844

DIRECT_ST = {
    0: "wylaczony",
    1: "startuje...",
    2: "GRA bezposrednio (proxy)",
    3: "brak urzadzenia o tej nazwie w Windows",
    4: "blad WASAPI - szczegoly w %TEMP%\\obs-asio-proxy.log",
}


class DirectCtl:
    """Blok sterujacy Manager <-> proxy.

    Piszemy 'direct' i nazwe urzadzenia, proxy donosi 'active' i 'status'.
    Bez blokad: kazde pole to pojedyncza liczba, jedna strona ja zapisuje,
    druga tylko czyta. Mapping tworzy kazda ze stron, wiec kolejnosc startu
    nie ma znaczenia.
    """

    class _S(ctypes.Structure):
        _pack_ = 1
        _fields_ = [
            ("magic", ctypes.c_uint32),
            ("direct", ctypes.c_int32),       # 1 = proxy ma grac
            ("active", ctypes.c_int32),       # 1 = proxy trzyma device
            ("status", ctypes.c_int32),       # DIRECT_ST
            ("underruns", ctypes.c_int32),
            ("lagFrames", ctypes.c_int32),
            ("periodFrames", ctypes.c_int32),
            ("rate", ctypes.c_int32),
            ("deviceName", ctypes.c_wchar * 128),
        ]

    def __init__(self):
        self._h = None
        self._view = None
        self._s = None
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileMappingW.restype = wintypes.HANDLE
        k32.CreateFileMappingW.argtypes = [
            wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR]
        h = k32.CreateFileMappingW(ctypes.c_void_p(-1), None, 0x04, 0,
                                   ctypes.sizeof(self._S), DIRECT_CTL_NAME)
        if not h:
            return
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.MapViewOfFile.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, ctypes.c_size_t]
        v = k32.MapViewOfFile(h, 0x000F, 0, 0, ctypes.sizeof(self._S))
        if not v:
            k32.CloseHandle(h)
            return
        self._k32 = k32
        self._h = h
        self._view = v
        self._s = ctypes.cast(v, ctypes.POINTER(self._S)).contents
        if self._s.magic != DIRECT_CTL_MAGIC:
            self._s.magic = DIRECT_CTL_MAGIC

    @property
    def ok(self):
        return self._s is not None

    def write(self, direct, devname):
        if self._s is None:
            return
        self._s.direct = 1 if direct else 0
        try:
            self._s.deviceName = (devname or "")[:127]
        except Exception:
            pass

    def snapshot(self):
        """(active, status, lag, period, rate, przerwy). Zera gdy brak mapy."""
        s = self._s
        if s is None:
            return (0, 0, 0, 0, 0, 0)
        return (s.active, s.status, s.lagFrames, s.periodFrames,
                s.rate, s.underruns)


class AsioMonitor:
    """Odsluch strumienia ASIO bez OBS - tylko odczyt wspolnej pamieci.

    Proxy jest DOKLADNIE takie samo jak dla OBS: ten sam plik, ten sam
    redirect, ta sama wspolna pamiec. Jedyna roznica to czytnik - ten
    proces. readPos celowo nie zapisujemy, wiec proxy i zrodlo OBS moga
    dzialac rownoczesnie z monitoringiem.

    Nie ma tu konwertera ani regulatora. Wyjscie otwieramy w ZEGARZE ASIO
    (samplerate = in_rate), wiec klatki przenoszymy 1:1, zero arytmetyki.
    Pokretlo "bufor" to po prostu odleglosc, jakaja czytnik trzyma sie za
    pisarzem: im mniejsza, tym blizej zywej krawedzi (mniejsze
    opoznienie, wieksze ryzyko przerwy), wieksza = bezpieczniej.
    """

    BLOCK = 128           # klatki na callback (~2.9 ms @44.1k; nizsza
                          # ziarnistosc odczytu = mniejsza latencja odsluchu)

    def __init__(self, dev, ch_l, ch_r, gain_db=0.0, lag_ms=20.0,
                 out_rate=0):
        self.dev = dev
        self.ch_l = ch_l
        self.ch_r = ch_r
        self.gain_db = gain_db
        self.gain = 10.0 ** (gain_db / 20.0)
        self.lag_ms = max(4.0, min(250.0, float(lag_ms)))
        self.lat_mode = "low"      # "low" albo None (latencja systemowa)
        self.out_rate_pref = int(out_rate or 0)   # 0 = zegar ASIO
        self._rdr = None
        self._stream = None
        self._src = 0        # nastepna klatka SHM do odczytania
        self._synced = False  # pierwsze zablokowanie pozycji (jak daw-source)
        self._held = (0.0, 0.0)  # ostatnia probka do zasklepiania przerw
        self.error = ""
        self.in_rate = 0
        self.out_rate = 0
        self.nch = 0
        self.dev_latency = 0.0
        self.shm_lag_ms = 0.0
        self.skips = 0
        self.underruns = 0
        self.subbed_dev = None    # urzadzenie zastapione innym API
        # stan konwersji zegara (gdy wybrany rate != zegar ASIO)
        self._ratio = 1.0
        self._rs_pos = 0.0
        self._rs_hist = [(0.0, 0.0)] * 3
        self._rs_resync = True

    # --- pomiar ------------------------------------------------------------
    def latency_breakdown(self):
        """(odleglosc_ms, blok_ms, urzadzenie_ms, razem_ms) - do GUI."""
        blk = (self.BLOCK / float(self.in_rate) * 1000.0
               if self.in_rate else 0.0)
        dev = self.dev_latency * 1000.0
        return self.shm_lag_ms, blk, dev, self.shm_lag_ms + blk + dev

    @staticmethod
    def _is_rate_err(e):
        """Czy blad dotyczy ZEGARU/FORMATU (a nie zajetosci urzadzenia).

        Rozroznienie jest kluczowe: przy "device unavailable" nie wolno
        przeskoczyc na inne API - to szla by inna droga (MME = mikser
        Windows, czyli inne urzadzenie) i uzytkownik dostalby cisze
        zamiast informacji, ze urzadzenie jest zajete.
        """
        s = str(e).lower()
        return ("sample rate" in s or "format" in s or "unsupported" in s
                or "-9997" in s)

    def _try_open(self, dev, r, latency="low", block=None):
        """Jedna proba otwarcia; przy bledzie zamyka strumien.

        Uwaga: sounddevice NIE przyjmuje latency='default' (to nie liczba
        i wybucha TypeError) - 'systemowa' to po prostu pominięcie
        parametru, czyli latency=None.
        """
        kw = dict(device=dev, channels=2, dtype="float32", samplerate=r,
                  callback=self._cb)
        if latency is not None:
            kw["latency"] = latency
        if block:
            kw["blocksize"] = block
        st = None
        try:
            st = sd.OutputStream(**kw)
            st.start()
            return st
        except Exception:
            if st is not None:
                try:
                    st.stop()
                except Exception:
                    pass
                try:
                    st.close()
                except Exception:
                    pass
            raise

    def _open_started(self, idx, rates):
        """Otwiera wyjscie i startuje je.  rates: rate albo lista rate.

        Kolejnosc: wybrane urzadzenie + kolejne zegary, dopiero potem to
        samo urzadzenie w innym API.  Auto stawia zegar wlasny urzadzenia
        przed zegarem ASIO, zeby przy 48 kHz w ASIO i 44.1 kHz na
        sluchawkach leciec konwersja czasu do prawdziwego endpointu
        (22 ms) zamiast MME (96 ms, i do innego urzadzenia).
        Zwraca (strumien, podmieniono, idx, uzyty_rate).
        """
        if isinstance(rates, int):
            rates = [rates]
        devs = sd.query_devices()
        name = (devs[idx]["name"] or "").strip().lower()
        cands = [idx]
        for j in range(len(devs)):
            if j == idx or devs[j]["max_output_channels"] < 1:
                continue
            if (devs[j]["name"] or "").strip().lower() == name:
                cands.append(j)
        first = None
        busy = None
        for dev in cands:
            safe = False
            for r in rates:
                try:
                    st = self._try_open(dev, r, self.lat_mode, self.BLOCK)
                    return st, dev != idx, dev, r
                except Exception as e:
                    if first is None:
                        first = e
                    if self._is_rate_err(e):
                        continue
                    if busy is None:
                        busy = e
                    if dev == idx and not safe and self.BLOCK:
                        # sterowniki bezprzewodowych sluchawek (Jabra)
                        # potrafia odrzucic WDM przy zadanej latencji
                        # ("WdmSyncIoctl ... 0x490") - wchodzimy w tryb
                        # bezpieczny: latencja systemowa, blok urzadzenia
                        safe = True
                        try:
                            st = self._try_open(dev, r, "default", 0)
                            mon_log("urzadzenie %d otwarte w trybie "
                                    "bezpiecznym (latencja systemowa)"
                                    % dev)
                            return st, dev != idx, dev, r
                        except Exception as e2:
                            if first is None:
                                first = e2
            if busy is not None and dev == idx:
                # wybrane urzadzenie jest zajete/niedostepne - nie
                # podmieniaj go na inne API, niech router sprobuje pozniej
                raise busy
        raise first

    def start(self):
        rdr = ShmReader()
        rdr.open()
        in_rate, nch, bfr, wp, active = rdr.header()
        if not in_rate or not nch:
            rdr.close()
            raise OSError("pusta wspolna pamiec (proxy nie zainicjalizowane)")
        nch = min(int(nch), DAW_MAX_CHANNELS)
        self.in_rate = int(in_rate)
        self.nch = nch
        self.ch_l = min(self.ch_l, nch - 1)
        self.ch_r = min(self.ch_r, nch - 1)
        self._rdr = rdr
        # start: pozycja NA RAZ przyklejona do zadanej odleglosci za
        # pisarzem; Uwaga: w tym pierwszym zablokowaniu NIE sprawdzamy
        # przeskoku - inaczej przy malym writePos bylby on ogromny i
        # system utknalby miedzy "zablokowalem do write" a nastepnym
        # ujemnym przeskokiem (patrz synced logic w _cb).
        lag = int(self.lag_ms * self.in_rate / 1000.0)
        self._src = self._rdr.map.writePos - lag
        self._synced = False
        self.skips = self.underruns = 0
        want = self.dev
        try:
            native = int(round(sd.query_devices(want)["default_samplerate"]))
        except Exception:
            native = 0
        # Kolejnosc zegarow: to, o co prosimy (pref albo zegar ASIO), potem
        # zegar wlasny urzadzenia (konwersja czasu), potem zegar ASIO.
        want_rates = [self.out_rate_pref or self.in_rate]
        if native:
            want_rates.append(native)
        want_rates.append(self.in_rate)
        seen = []
        for r in want_rates:
            if r and r not in seen:
                seen.append(r)
        try:
            self._stream, subbed, self.dev, used = self._open_started(want, seen)
        except Exception as e:
            # pelny kontekst - bez niego nie wiadomo czy padlo wlasne
            # urzadzenie, inny API, czy zly zegar
            try:
                di = sd.query_devices(want)
                dname, host = di["name"], sd.query_hostapis(di["hostapi"])["name"]
            except Exception:
                dname, host = "?", "?"
            mon_log("otwarcie dev=%d (%s / %s) zegary=%s blok=%d "
                    "NIEUDANE: %r" % (want, dname, host, seen, self.BLOCK, e))
            raise
        if subbed:
            self.subbed_dev = self.dev
            mon_log("wyjscie %s nie przyjmuje %s Hz - podmieniono to samo "
                    "urzadzenie w innym API (dev %s -> %s)"
                    % (sd.query_devices(want)["name"],
                       "/".join(str(x) for x in seen), want, self.dev))
        self.out_rate = int(round(self._stream.samplerate))
        self._ratio = float(self.in_rate) / float(self.out_rate)
        self._rs_resync = True
        if self._ratio != 1.0:
            mon_log("konwersja zegara %d -> %d Hz (katmull-rom), dev=%s"
                    % (self.in_rate, self.out_rate,
                       sd.query_devices(self.dev)["name"]))
        try:
            self.dev_latency = float(self._stream.latency)
        except Exception:
            self.dev_latency = 0.0
        return True

    def stop(self):
        if self._stream:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        if self._rdr:
            self._rdr.close()
            self._rdr = None

    def set_gain(self, db):
        self.gain_db = db
        self.gain = 10.0 ** (db / 20.0)

    def _reclock(self, rate):
        """Host zmienil zegar ASIO - przelaczamy czytnik bez odpinania."""
        old = self.in_rate
        self.in_rate = int(rate)
        self._ratio = (float(self.in_rate) / float(self.out_rate)
                       if self.out_rate else 1.0)
        lag = int(self.lag_ms * self.in_rate / 1000.0)
        try:
            self._src = int(self._rdr.map.writePos) - lag
        except Exception:
            self._src = 0
        self._synced = False
        self._rs_resync = True
        mon_log("host zmienil zegar %d -> %d Hz - czytnik przelaczony"
                % (old, self.in_rate))

    def _cb(self, outdata, frames, time_info, status):
        """Callback PortAudio: dokladnie jak czytnik w daw-source.cpp.

        ZEGAR wyjscia = zegar ASIO (samplerate = in_rate), wiec klatki
        ida 1:1, bez konwersji, bez numpy, bez petli regulujacych celowo.
        Bufor to odleglosc za pisarzem: reader jest przyklejony
        TARGET_LAG klatek za writePos, a jak pisarz przeskoczy daleko
        (nowa sesja / pauza / restart), to glowka skacze za nim zamiast
        budowac opoznienie.
        """
        n = frames
        outdata[:] = 0.0
        try:
            write = int(self._rdr.map.writePos)
        except Exception:
            self.underruns += 1
            return
        # Host ASIO potrafi zmienic zegar w trakcie sesji (TH-U po
        # "Reset device", zmiana sample rate w programie).  Stary
        # in_rate = czytnik czytalby szybciej niz pisarz i wygodzilby
        # sie na probce holdowanej - czyli cisza.  Przelaczamy sie
        # w locie, bez odpinania.
        try:
            rate_now = int(self._rdr.map.sampleRate)
        except Exception:
            rate_now = 0
        if rate_now and rate_now != self.in_rate:
            self._reclock(rate_now)
        lag = int(self.lag_ms * self.in_rate / 1000.0)
        target_head = write - lag

        # pinning lag tak jak w daw-source.cpp: przeskok wiekszy niz prog
        # -> wyrzuc dane lub poczekaj na pisarza (nowa sesja/pauza). Prog to
        # osiem blokow = 1024 klatki, dokladnie tyle ile dawalo BLOCK*4 przy
        # starym bloku 256 - zmniejszenie bloku do 128 nie zmienia wiec
        # czulosci na dryf.  Pierwszy raz (synced=False) pozycje ustalamy
        # BEZ sprawdzania przeskoku, bo na starcie writePos jest maly
        # i roznica zawsze nie mieszczylaby sie w limicie.
        snap = self.BLOCK * 8
        if not self._synced:
            self._src = target_head
            self._synced = True
        else:
            skip = target_head - self._src
            if skip > snap:
                self._src = target_head
                self.skips += 1
                self._rs_resync = True
            elif skip < -snap:
                self._src = write
                self.skips += 1
                self._rs_resync = True

        if self._ratio == 1.0:
            avail = target_head - self._src
            take = n if avail >= n else (avail if avail > 0 else 0)
            if take > 0:
                wl, wr = self._rdr.read_pair(self._src, self.ch_l,
                                             self.ch_r, take)
                if self.gain == 1.0:
                    outdata[:take, 0] = wl[:take]
                    outdata[:take, 1] = wr[:take]
                else:
                    g = self.gain
                    for i in range(take):
                        a = wl[i] * g
                        if a > 1.0:
                            a = 1.0
                        elif a < -1.0:
                            a = -1.0
                        b = wr[i] * g
                        if b > 1.0:
                            b = 1.0
                        elif b < -1.0:
                            b = -1.0
                        outdata[i, 0] = a
                        outdata[i, 1] = b
                self._src += take
                self._held = (float(outdata[take - 1, 0]),
                              float(outdata[take - 1, 1]))
            if take < n:
                # zrodlo nie nadalo calej klatki - zasklepiamy OSTATNIA
                # probka (nie cisza), zeby uniknac trzasku
                if take > 0:
                    outdata[take:n, :] = outdata[take - 1, :]
                else:
                    outdata[:, 0] = self._held[0]
                    outdata[:, 1] = self._held[1]
                self.underruns += 1
            self.shm_lag_ms = (write - self._src) / self.in_rate * 1000.0
            return

        # --- wybrany zegar != zegar ASIO: konwersja CZASU, nie materialu --
        # Te same klatki, tylko inny odstep czasowy (katmull-rom, 3
        # probki historii trzymane miedzy callbackami).
        step = self._ratio
        need = int(n * step) + 3
        avail = target_head - self._src
        take = need if avail >= need else (avail if avail > 0 else 0)
        if take > 0:
            start = self._src
            if self._rs_resync:
                self._rs_pos = float(start)
                self._rs_hist = [(0.0, 0.0)] * 3
                self._rs_resync = False
            wl, wr = self._rdr.read_pair(start, self.ch_l, self.ch_r, take)
            self._src = start + take
            ol, orr = self._resample(wl, wr, start, n)
            outdata[:n, 0] = ol
            outdata[:n, 1] = orr
            self._held = (ol[-1], orr[-1])
        else:
            outdata[:, 0] = self._held[0]
            outdata[:, 1] = self._held[1]
            self.underruns += 1
        self.shm_lag_ms = (write - self._src) / self.in_rate * 1000.0

    def _resample(self, wl, wr, start, n):
        """n probek wyjsciowych (Catmull-Rom) z klatek SHM.

        Historia 3 probek z poprzedniego callbacku jest doklejana z przodu,
        wiec interpolacja ma zawsze cztery punkty - takze na brzegach
        bloku.  Pozycja _rs_pos to absolutny numer klatki SHM (z czastka
        ulamkowa), dzieki czemu nie traci sie fazy miedzy callbackami.
        """
        buf = self._rs_hist + list(zip(wl, wr))
        last = len(buf) - 1
        step = self._ratio
        g = self.gain
        pos = self._rs_pos
        ol = [0.0] * n
        orr = [0.0] * n
        for k in range(n):
            p = pos + k * step
            i = int(p)
            f = p - i
            # pozycja w buforze: buf[0] == klatka (start-3)
            j = i - start + 3
            if j < 1:
                j = 1
            elif j > last - 2:
                j = last - 2
            a0l, a0r = buf[j - 1]
            a1l, a1r = buf[j]
            a2l, a2r = buf[j + 1]
            a3l, a3r = buf[j + 2]
            f2 = f * f
            f3 = f2 * f
            c0 = -0.5 * f3 + f2 - 0.5 * f
            c1 = 1.5 * f3 - 2.5 * f2 + 1.0
            c2 = -1.5 * f3 + 2.0 * f2 + 0.5 * f
            c3 = 0.5 * f3 - 0.5 * f2
            v = c0 * a0l + c1 * a1l + c2 * a2l + c3 * a3l
            w = c0 * a0r + c1 * a1r + c2 * a2r + c3 * a3r
            if g != 1.0:
                v *= g
                w *= g
            if v > 1.0:
                v = 1.0
            elif v < -1.0:
                v = -1.0
            if w > 1.0:
                w = 1.0
            elif w < -1.0:
                w = -1.0
            ol[k] = v
            orr[k] = w
        self._rs_pos = pos + n * step
        self._rs_hist = buf[-3:]
        return ol, orr


def out_devices():
    """[(etykieta, indeks)] wyjsciowe urzadzenia - WASAPI przed innymi,
    bo MME/DirectSound maja tu 100+ ms opoznienia."""
    api = {}
    for i in range(len(sd.query_hostapis())):
        h = sd.query_hostapis(i)
        for idx in h["devices"]:
            api[idx] = h["name"]
    rank = {"Windows WASAPI": 0, "Windows WDM-KS": 1,
            "Windows DirectSound": 2, "MME": 3}
    res = []
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] < 1:
            continue
        a = api.get(i, "?")
        res.append(((rank.get(a, 9), i),
                    "%s  [%s]" % (d["name"], a), i))
    res.sort()
    return [(lbl, idx) for _, lbl, idx in res]


def obs_dir():
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\OBS Studio") as k:
            p = winreg.QueryValueEx(k, "InstallDir")[0]
            if p and os.path.isdir(p):
                return p
    except OSError:
        pass
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),
               os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        p = os.path.join(pf, "obs-studio")
        if os.path.isdir(p):
            return p
    return None


def obs_plugins_dir():
    d = obs_dir()
    if not d:
        return None
    return os.path.join(d, "obs-plugins", "64bit")


def asio_drivers():
    """Lista sterownikow ASIO z HKLM\\SOFTWARE\\ASIO + stan proxy wtyczki."""
    res = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\ASIO") as k:
            i = 0
            while True:
                try:
                    name = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                clsid = None
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        r"SOFTWARE\ASIO\%s" % name) as dk:
                        clsid = winreg.QueryValueEx(dk, "CLSID")[0]
                except OSError:
                    pass
                dll = ""
                original = None
                if clsid:
                    key = r"SOFTWARE\Classes\CLSID\%s\InprocServer32" % clsid
                    try:
                        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key) as sk:
                            try:
                                dll = winreg.QueryValueEx(sk, None)[0] or ""
                            except OSError:
                                dll = ""
                            try:
                                original = winreg.QueryValueEx(sk,
                                                               "OriginalServer")[0]
                            except OSError:
                                original = None
                    except OSError:
                        pass
                res.append({"name": name, "clsid": clsid, "dll": dll,
                            "original": original})
    except OSError:
        pass
    return res


# ---------------------------------------------------------------------------
#  JAM VOX - konfiguracja wyjscia audio (%APPDATA%\VOX\JamVOX\Preferences3.xml)
#
#  To jest ostatni element ukladanki: proxy widzi tylko strumien ASIO.
#  Dopoki JAM VOX gra przez "Windows Audio" (domyslnie), w OBS jest cisza,
#  chociaz redirecty sa poprawnie ustawione.
# ---------------------------------------------------------------------------
VOX_PROCS = ("JamVOX.exe", "InitJam.exe")


def vox_pref_path():
    base = os.environ.get("APPDATA", "")
    return os.path.join(base, "VOX", "JamVOX", "Preferences3.xml") if base else ""


def vox_device():
    """DEVICESETUP z Preferences3.xml: dict atrybutow albo None."""
    p = vox_pref_path()
    if not p or not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read()
    except OSError:
        return None
    m = re.search(r"<DEVICESETUP\b([^>]*?)/>", txt, re.S)
    if not m:
        return None
    return dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))


def jamvox_running():
    """Czy JAM VOX (lub jego helper 64-bitowy InitJam.exe) jest uruchomiony."""
    found = []
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True,
                             timeout=PROC_TIMEOUT,
                             creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    for line in out.splitlines():
        low = line.lower()
        for p in VOX_PROCS:
            if low.startswith('"%s"' % p.lower()):
                found.append(p)
    return sorted(set(found))


def write_vox_asio(driver_name, rate="44100.0"):
    """Przelacza JAM VOX na ASIO. Zwraca (ok, komunikat).

    Robi kopie bezpieczenstwa Preferences3.xml.axebak.<znacznik> i poprawia
    TYLKO atrybuty w elemencie DEVICESETUP - reszta pliku (patche, biblioteka,
    miksery) zostaje nietknieta, bo JAM VOX nadpisuje plik przy wyjsciu.
    """
    if not driver_name:
        return False, "Najpierw zaznacz sterownik ASIO na liscie powyzej."
    procs = jamvox_running()
    if procs:
        return False, ("JAM VOX jest uruchomiony (%s). Zamknij go CALKOWICIE "
                       "(takze InitJam.exe) i sprobuj ponownie."
                       % ", ".join(procs))
    p = vox_pref_path()
    if not p or not os.path.isfile(p):
        return False, ("Nie znaleziono %s. JAM VOX musial juz raz byc "
                       "uruchomiony." % (p or "Preferences3.xml"))
    try:
        with open(p, "r", encoding="utf-8") as f:
            txt = f.read()
    except OSError as e:
        return False, "Nie moge odczytac Preferences3.xml: %s" % e

    m = re.search(r"<DEVICESETUP\b([^>]*?)/>", txt, re.S)
    if not m:
        return False, "W Preferences3.xml nie ma elementu DEVICESETUP."

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = "%s.axebak.%s" % (p, stamp)
    try:
        shutil.copyfile(p, bak)
    except OSError as e:
        return False, "Nie moge zrobic kopii bezpieczenstwa: %s" % e

    attrs = dict(re.findall(r'([\w:]+)="([^"]*)"', m.group(1)))
    attrs["deviceType"] = "ASIO"
    attrs["audioOutputDeviceName"] = driver_name
    attrs["audioDeviceRate"] = rate
    new_tag = "<DEVICESETUP " + " ".join(
        '%s="%s"' % (k, attrs[k]) for k in
        ("deviceType", "audioOutputDeviceName", "audioInputDeviceName",
         "audioDeviceRate") if k in attrs) + " />"
    # zachowujemy atrybuty, ktorych nie znamy (np. przyszly format JAM VOX)
    if len(attrs) > 4:
        known = {"deviceType", "audioOutputDeviceName", "audioInputDeviceName",
                 "audioDeviceRate"}
        extra = " ".join('%s="%s"' % (k, v) for k, v in attrs.items()
                         if k not in known)
        if extra:
            new_tag = new_tag[:-3] + extra + " />"
    out = txt[:m.start()] + new_tag + txt[m.end():]
    try:
        with open(p, "w", encoding="utf-8", newline="") as f:
            f.write(out)
    except OSError as e:
        return False, "Zapis Preferences3.xml nieudany: %s" % e
    return True, ("JAM VOX ustawiony na ASIO (%s, %s Hz).\nKopia: %s\n"
                  "Teraz w JAM VOX: Sterowniki audio > ASIO > %s - ten sam "
                  "sterownik co w OBS."
                  % (driver_name, rate, os.path.basename(bak), driver_name))


def redirect32_info(clsid):
    """Stan redirectu w widoku 32-bit (WOW6432Node): (dll, original)."""
    if not clsid:
        return None, None
    key = r"SOFTWARE\Classes\CLSID\%s\InprocServer32" % clsid
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key, 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_32KEY) as k:
            dll = (winreg.QueryValueEx(k, None)[0] or "")
            try:
                original = winreg.QueryValueEx(k, "OriginalServer")[0]
            except OSError:
                original = None
            return dll, original
    except OSError:
        return None, None


# ---------------------------------------------------------------------------
#  Diagnostyka monitoringu: czy proxy jest w ogole "podpiete" pod program.
#  Wspolna pamiec istnieje DOPIERO gdy jakis program ASIO otworzyl
#  przekierowany sterownik i wywolal createBuffers. Te trzy sondy pokazuja,
#  ktorego z warunkow brakuje.
# ---------------------------------------------------------------------------
ASIO_APP_MARKS = (
    ("jamvox",   "JAM VOX"),
    ("initjam",  "JAM VOX (helper)"),
    ("th-u-64",  "Overloud TH-U"),
    ("th-u-32",  "Overloud TH-U"),
    ("th3",      "Overloud TH3"),
    ("reaper",   "Reaper"),
    ("studioone", "Studio One"),
    ("ableton",  "Ableton Live"),
    ("fl studio", "FL Studio"),
    ("fl64",     "FL Studio"),
    ("cakewalk", "Cakewalk"),
    ("bitwig",   "Bitwig"),
    ("cubase",   "Cubase"),
    ("nuendo",   "Nuendo"),
)


PROC_TIMEOUT = 5  # sekund na tasklist - nigdy nie puscimy tego z watku GUI
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # zero mrygajacych cmd


def asio_apps_running():
    """Uruchomione programy (nazwy procesow) z listy znanych hostow ASIO."""
    try:
        out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True,
                             timeout=PROC_TIMEOUT,
                             creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    res = []
    for line in out.splitlines():
        m = re.match(r'"([^"]+)"', line)
        if not m:
            continue
        exe = m.group(1).lower()
        for mark, label in ASIO_APP_MARKS:
            if mark in exe and label not in res:
                res.append(label)
    res.sort()
    return res


def proxy_log_state():
    """(mtime, status) logu proxy %TEMP%\\obs-asio-proxy.log.
    Proxy dopisuje go przy KAZDYM ladowaniu (LoadLibrary/DllGetClassObject),
    wiec treść mowi, czy jakis program w ogole uzywa naszej DLL."""
    p = os.path.join(os.environ.get("TEMP", "."), "obs-asio-proxy.log")
    if not os.path.isfile(p):
        return None, ""
    mtime = os.path.getmtime(p)
    txt = ""
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            txt = f.read().strip()
    except OSError:
        pass
    t = txt.lower()
    last = txt.splitlines()[-1] if txt else ""
    if "fail:" in t:
        status = "ostatnio: BLAD proxy (%s)" % last
    elif "createbuffers" in t:
        status = "ostatnio: proxy ZALADOWANE, streaming (createBuffers)"
    elif "success: proxy active" in t:
        status = "ostatnio: proxy zaladowane, czeka na buffery"
    elif "dllgetclassobject called" in t:
        status = "ostatnio: proxy ruszylo, host otwiera sterownik"
    else:
        status = "ostatnio: %s" % last if last else txt[:60]
    return mtime, status


def shm_probe():
    """(istnieje, sr, nch, active, wp, przyrost) - dwa odczyty writePos
    rozdzielone 0.5 s, zeby wiedziec czy proxy naprawde streamuje."""
    r = ShmReader()
    try:
        r.open()
    except OSError:
        return False, 0, 0, 0, 0, 0
    sr, nch, _, wp, active = r.header()
    try:
        time.sleep(0.5)
        wp2 = int(r.map.writePos)
    except Exception:
        wp2 = wp
    out = (True, sr, nch, active, wp, wp2 - wp)
    r.close()
    return out


def shm_quick():
    """Lekka sonda SHM bez bezczeszczenia w czasie: (istnieje, sr, nch, active, wp)."""
    r = ShmReader()
    try:
        r.open()
    except OSError:
        return False, 0, 0, 0, 0
    sr, nch, _, wp, active = r.header()
    r.close()
    return True, sr, nch, active, wp


#  Cache do _mon_diag_text (panel diagnostyczny odswiezany co 0.6 s nie moze
#  ani czytac tasklista co kazdy tik, ani spawac logu na dysku czesciej niz 1 s).
_diag_cache = {"apps": None, "apps_t": 0.0, "log": None, "log_t": 0.0,
               "wp": None, "wp_t": 0.0, "rate": None, "rate_ok": False}


def _mon_diag_text(full_scan=True, mon_state="-"):
    """Panel diagnostyczny zakladki Monitoring - ktore ogniwo lancucha gra.

    Lancuch: [redirect ASIO] -> [program gra na ASIO] -> [wspolna pamiec].
    Każde ogniwo ma wlasna linie, zebys od razu widzial, czego brakuje.
    NIE wolno tego wolac z watku GUI z tasklist=true - patrol robi to w tle.
    """
    global _diag_cache
    now = time.time()

    # -- ogniwo 1: redirect (x64 i 32-bit) w rejestrze ---------------------
    drivers = asio_drivers()
    any64 = any(d["dll"] and "asio-proxy" in d["dll"].lower() for d in drivers)
    any32 = False
    if drivers:
        for d in drivers:
            d32, _ = redirect32_info(d["clsid"])
            if d32 and "asio-proxy" in d32.lower():
                any32 = True
                break

    # -- ogniwo 2: uruchomione hosty ASIO -----------------------------------
    apps = []
    if full_scan or not _diag_cache["apps"]:
        apps = asio_apps_running()
        _diag_cache["apps"] = apps
        _diag_cache["apps_t"] = now
    else:
        apps = _diag_cache["apps"]
    apps_joined = ", ".join(apps) if apps else "zaden"
    host_ok = bool(apps)

    # -- ogniwo 3: wspolna pamiec (istnieje? aktivna? writePos grzoze?) ----
    ok, sr, nch, active, wp = shm_quick()
    if (not ok) or (wp != _diag_cache["wp"]):
        if _diag_cache["wp_t"] and ok and _diag_cache["wp"] is not None:
            dt = now - _diag_cache["wp_t"]
            if dt > 0:
                _diag_cache["rate"] = (wp - _diag_cache["wp"]) / dt
                _diag_cache["rate_ok"] = True
        _diag_cache["wp"] = wp if ok else None
        _diag_cache["wp_t"] = now
        if not ok:
            _diag_cache["rate"] = None
            _diag_cache["rate_ok"] = False
    rate = _diag_cache["rate"]

    # -- ogniwo bonus: JAM VOX ustawiony na ASIO? ---------------------------
    jv = any("JAM VOX" in a for a in apps)
    dev = vox_device() if jv else None
    vox_line = ""
    if dev:
        dt_ = dev.get("deviceType", "?")
        out_ = dev.get("audioOutputDeviceName", "?")
        if str(dt_).upper().startswith("ASIO"):
            vox_line = "JAM VOX wyjscie audio: ASIO (%s) - ok si padlo, prosto" % out_
        else:
            pick = next((d["name"] for d in drivers
                         if (redirect32_info(d["clsid"])[0] or "").lower()
                         .find("asio-proxy") >= 0), None)
            pick = "nieznany (brak 32-bit redirectu!)" if not pick else pick
            vox_line = ("JAM VOX wyjscie audio: %s / %s [KROK:2]!\n"
                        "        Wybierz w JAM VOX: Sterowniki audio > ASIO > "
                        "%s (ma redirect 32-bit)"
                        % (dt_, out_, pick))

    # -- ogniwo bonus: log proxy -------------------------------------------
    mt, log_status = proxy_log_state()
    log_line = "Log proxy: %s" % (log_status or "nie istnieje (nikt nie doladowal proxy DLL)")

    # ----------------------------------------------------------------------
    state = []
    state.append("Lancuch [redirect -> ASIO program -> pamiec]:")
    state.append("  redirect x64/32-bit : %s / %s" % (
        "TAK" if any64 else "NIE", "TAK" if any32 else "NIE"))
    state.append("  program ASIO        : %s" % (host_ok and apps_joined or "brak"))
    if ok:
        r = ("writePos grzoze ~%d kl/s" % rate) if (rate and rate > 0) else \
            ("active=%s, writePos STOI (nie gra)" % active)
        state.append("  wspolna pamiec      : istnieje  %d Hz %d ch  %s"
                     % (sr, nch, r))
    else:
        state.append("  wspolna pamiec      : NIE ISTNIEJE")
    state.append("  routing             : %s" % mon_state)
    state.append("")
    state.append("Sterowniki ASIO + redirect (x64/32-bit):")
    if not drivers:
        state.append("  BRAK sterownikow ASIO w rejestrze")
    else:
        for d in drivers:
            d64 = d["dll"] or ""
            p64 = "PROXY" if "asio-proxy" in d64.lower() else "bez proxy"
            d32, _ = redirect32_info(d["clsid"])
            p32 = ("PROXY" if (d32 and "asio-proxy" in d32.lower())
                   else ("-" if not d32 else "realny DLL!"))
            state.append("  %-20s  x64:%s  32:%s"
                         % (d["name"][:20], p64, p32))
    if apps:
        state.append("Uruchomione hosty ASIO: %s" % apps_joined)
    if vox_line:
        state.append(vox_line)
    state.append(log_line)

    if not (any64 or any32) and not ok:
        state.append("")
        state.append("[KROK:1] brak redirectu -> zakladka ASIO Capture:")
        state.append("        zaznacz sterownik danego programu, kliknij")
        state.append("        \"Zainstaluj redirect\" (x64) i/lub "
                     "\"redirect 32-bit (JAM VOX)\".")
    if host_ok and not ok:
        state.append("")
        state.append("[KROK:2/3] redirect moze byc ok, ale SHM nie istnieje.")
        state.append("        Program GRAC przez ASIO (nie Windows Audio)%s"
                     % ("" if vox_line else ", np. JAM VOX: Sterowniki audio > ASIO"))
        state.append("        i miec lecacy dzwiek - dopiero wtedy powstanie")
        state.append("        wspolna pamiec, routing automatyczny podepnie sie sam.")
    return "\n".join(state)


def report(args, kod, devname, szczegoly=""):
    """Dopisuje wynik testu do pliku raportu.

    Robi to sam Manager, a nie skrypt .bat: przekierowanie wyjscia
    programu onefile przez bat potrafi dac pusty wynik i kod -1
    (kolizja z rozpakowywaniem do katalogu _MEIxxx w TEMP), a skrypt
    nie ma jak odroznic "blad" od "nic nie wypisano".
    """
    path = getattr(args, "raport", "")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                   args.tytul or "test"))
            f.write("    urzadzenie : %s\n" % devname)
            f.write("    zegar       : %s   bufor: %d ms   blok: %d\n"
                    % ("%d Hz" % args.rate if args.rate else "auto (jak ASIO)",
                       args.lag, args.blok or 256))
            if szczegoly:
                f.write("    %s\n" % szczegoly)
            f.write("    kod wyjscia : %d  %s\n\n"
                    % (kod, "OK" if kod == 0 else "PROBLEM"))
    except OSError:
        pass


def tone_test(idx, args):
    """Ton testowy wprost na urzadzenie (bez SHM) - sprawdza sam
    sprzet: zgloszenia, trzaski i opoznienie.  Do sluchu i do oceny
    jakości, bo idzie identycznym callbackiem co odsluch.
    """
    import math

    rate = args.rate or int(round(sd.query_devices(idx)["default_samplerate"]))
    hz = float(args.ton)
    amp = 0.2
    st_ = {"n": 0, "under": 0, "peak": 0.0}
    blk = args.blok or 256

    def cb(outdata, frames, t, status):
        if status:
            st_["under"] += 1
        n = frames
        base = st_["n"]
        pk = 0.0
        w = 2.0 * math.pi * hz / rate
        for i in range(n):
            v = amp * math.sin(w * (base + i))
            outdata[i, 0] = v
            outdata[i, 1] = v
            av = v if v >= 0.0 else -v
            if av > pk:
                pk = av
        st_["n"] = base + n
        st_["peak"] = pk

    print("TON %.0f Hz, %.0f%% amplitudy, %d Hz, blok %d, %.0f s"
          % (hz, amp * 100, rate, blk, args.dluznosc))
    try:
        kw = dict(device=idx, channels=2, dtype="float32", samplerate=rate,
                  blocksize=blk, callback=cb)
        if args.latencja == "low":
            kw["latency"] = "low"
        st = sd.OutputStream(**kw)
        st.start()
    except Exception as e:
        print("OTWARCIE NIEUDANE: %r" % (e,))
        report(args, 1, sd.query_devices(idx)["name"])
        return 1
    print("OTWARTE latencja=%.1fms - sluchaj ton" % (st.latency * 1000))
    t0 = time.time()
    while time.time() - t0 < args.dluznosc:
        time.sleep(0.1)
    st.stop()
    st.close()
    print("przerwy=%d  amplituda=%.2f" % (st_["under"], st_["peak"]))
    print("ZAMKNIETE OK")
    report(args, 0, sd.query_devices(idx)["name"],
           "ton %.0f Hz  przerwy=%d  amplituda=%.2f" % (hz, st_["under"],
                                                         st_["peak"]))
    return 0


def run_elevated(script_body, timeout=60):
    """Wykonuje skrypt PowerShell jako administrator (UAC), czeka, zwraca (ok, out)."""
    path = os.path.join(os.environ.get("TEMP", "."),
                        "axeman_elevated_%d.ps1" % os.getpid())
    out_path = path + ".log"
    with open(path, "w", encoding="utf-8") as f:
        f.write('$out = "%s"\n%s\n$out | Out-File -Encoding utf8 $out\n' % (out_path, script_body))
    outer = ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
    try:
        p = subprocess.Popen(["powershell.exe"] + outer,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             creationflags=NO_WINDOW)
        _, _ = p.communicate(timeout=timeout)
        ok = p.returncode == 0
    except Exception as e:
        ok = False
        out_path = None
    txt = ""
    if out_path and os.path.exists(out_path):
        with open(out_path, "r", encoding="utf-8") as f:
            txt = f.read()
    try:
        os.remove(path)
        if out_path and os.path.exists(out_path):
            os.remove(out_path)
    except OSError:
        pass
    return ok, txt


def resource_path(name):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), name)


def flexasio_status():
    reg_ok = False
    try:
        r = subprocess.run(["reg", "query", r"HKLM\SOFTWARE\ASIO\FlexASIO"],
                           capture_output=True, timeout=10,
                           creationflags=NO_WINDOW)
        reg_ok = r.returncode == 0
    except Exception:
        pass
    pads = []
    for pf in (os.environ.get("ProgramFiles", r"C:\Program Files"),
               os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        p = os.path.join(pf, "FlexASIO", "x64", "PortAudioDevices.exe")
        if os.path.exists(p):
            pads.append(p)
    return reg_ok, pads


def clean_names(raw):
    """Usuwa duplikaty/przyciete zapisy (krotszy bedacy prefiksem dluzszego)."""
    uniq = list(dict.fromkeys(raw))
    long = sorted(uniq, key=len, reverse=True)
    res = []
    for n in long:
        if all(not (m != n and m.startswith(n)) for m in long):
            res.append(n)
    return res


def flexasio_devices(pads):
    devs = []
    for pad in pads:
        try:
            r = subprocess.run([pad], capture_output=True, timeout=8,
                           creationflags=NO_WINDOW)
            out = (r.stdout or b"").decode("utf-8", "replace")
        except Exception:
            continue
        for line in out.splitlines():
            m = re.search(r'WASAPI:\d+\| name\[(.*)\]', line)
            if m and not m.group(1).endswith("[Loopback]"):
                devs.append(m.group(1))
        if devs:
            break
    if not devs:
        for d in sd.query_devices():
            devs.append(d["name"])
    return clean_names(devs)


def pick(devs, hints):
    for h in hints:
        for d in devs:
            if h.casefold() in d.casefold():
                return d
    return None


def write_toml(inp, outp, buf_ms, channels, path, extra=None):
    sr = 48000
    buf = max(32, int(sr * buf_ms / 1000))
    lines = ["# Wygenerowane przez AXE I/O ONE -> OBS/DAW Manager",
             'backend = "Windows WASAPI"',
             "bufferSizeSamples = %d" % buf,
             "", "[input]",
             "device = %s" % json.dumps(inp or "", ensure_ascii=False),
             "channels = %d" % channels]
    if outp:
        lines += ["", "[output]",
                  "device = %s" % json.dumps(outp, ensure_ascii=False),
                  "channels = %d" % channels]
    if extra:
        lines += ["", "# " + extra]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


class App:
    def __init__(self, root):
        self.root = root
        self.stream = None
        self.running = False
        self.last_level = 0.0
        self.pads = []
        self.fx_devices = []
        self.installing = False
        self._gui_q = queue.Queue()
        # nagrywarka (DAW)
        self.rec_thread = None
        self.rec_stop_flag = threading.Event()
        self.rec_error = None
        self.rec_info = ""
        self.rec_level = 0.0
        self.rec_path_wav = None
        self.rec_path_out = None

        root.title("AXE I/O ONE -> OBS / DAW Manager")
        root.geometry("540x640")
        root.resizable(False, False)

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self._build_bridge(nb)
        self._build_flexasio(nb)
        self._build_asiocap(nb)
        self._build_recorder(nb)
        self._build_monitor(nb)

        self.lb_bottom = ttk.Label(root, text="", anchor="w")
        self.lb_bottom.pack(fill="x", padx=12, pady=(0, 8))

        self.root.after(100, self._pump_gui)
        self.refresh_bridge_devices()
        self.check_flexasio()
        self.check_asiocap()
        self.check_recorder()
        self.root.after(120, self._tick_vu)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- Sekcja 1: MOST ----------
    def _build_bridge(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=" MOST (ONE -> kabel) ")
        pad = {"padx": 8, "pady": 4}
        ttk.Label(f, text="Wejscie (AXE I/O ONE):").grid(row=0, column=0, sticky="w", **pad)
        self.cb_bi = ttk.Combobox(f, state="readonly", width=48)
        self.cb_bi.grid(row=1, column=0, columnspan=3, sticky="we", **pad)
        ttk.Label(f, text="Wyjscie (kabel wirtualny):").grid(row=2, column=0, sticky="w", **pad)
        self.cb_bo = ttk.Combobox(f, state="readonly", width=48)
        self.cb_bo.grid(row=3, column=0, columnspan=2, sticky="we", **pad)
        ttk.Button(f, text="Odswiez", command=self.refresh_bridge_devices).grid(row=3, column=2, **pad)

        self.gain = tk.DoubleVar(value=0.0)
        ttk.Label(f, text="Wzmocnienie (dB):").grid(row=4, column=0, sticky="w", **pad)
        self.lb_gain = ttk.Label(f, text="0 dB")
        self.sc_gain = ttk.Scale(f, from_=-24.0, to=12.0, variable=self.gain,
                                 command=lambda v: self.lb_gain.config(text="%.0f dB" % float(v)))
        self.sc_gain.grid(row=5, column=0, sticky="we", **pad)
        self.lb_gain.grid(row=5, column=1, sticky="w")

        self.fs = ttk.Combobox(f, state="readonly", values=["auto", "44100", "48000", "96000"], width=8)
        self.fs.current(0)
        ttk.Label(f, text="fs:").grid(row=5, column=2, sticky="e", **pad)
        self.fs.grid(row=5, column=3, sticky="w", **pad)

        self.btn = tk.Button(f, text="WLACZ ROUTING", font=("Segoe UI", 11, "bold"),
                             bg="#2e7d32", fg="white", activebackground="#1b5e20",
                             relief="raised", bd=4, command=self.toggle)
        self.btn.grid(row=6, column=0, columnspan=4, sticky="we", pady=(16, 6))

        self.canvas = tk.Canvas(f, height=18, bg="#111")
        self.canvas.grid(row=7, column=0, columnspan=4, sticky="we")
        self.bar = self.canvas.create_rectangle(0, 0, 0, 18, fill="#4caf50")

        self.lb_bridge = ttk.Label(f, text="Wybierz urzadzenia i wcisnij WLACZ ROUTING.")
        self.lb_bridge.grid(row=8, column=0, columnspan=4, sticky="w", **pad)
        ttk.Label(f, text="OBS: 'Audio Input Capture' = CABLE Output (Track 2).").grid(
            row=9, column=0, columnspan=4, sticky="w", **pad)

    def refresh_bridge_devices(self):
        self._async(self._bridge_dev_list, self._ui_bridge_dev_list)

    def _bridge_dev_list(self):
        ins, outs = [], []
        for i, d in enumerate(sd.query_devices()):
            host = sd.query_hostapis()[d["hostapi"]]["name"]
            tag = "WASAPI" if "wasapi" in host.casefold() else host
            if d["max_input_channels"] >= 1:
                ins.append("%02d  %s [%s]" % (i, d["name"], tag))
            if d["max_output_channels"] >= 1:
                outs.append("%02d  %s [%s]" % (i, d["name"], tag))
        return ins, outs

    def _ui_bridge_dev_list(self, r):
        if isinstance(r, tuple) and r and r[0] == "__ERR__":
            self.lb_bridge.config(text="Blad listy urzadzen: %s" % (r[1],))
            return
        ins, outs = r
        self.cb_bi["values"] = ins
        self.cb_bo["values"] = outs
        self._preselect(self.cb_bi, ins, "axe io one")
        self._preselect(self.cb_bo, outs, "cable input")
        self.lb_bridge.config(
            text="Urzadzenia odswiezone (in=%d, out=%d)."
                 % (len(ins), len(outs)))

    def _preselect(self, cb, values, hint):
        if values:
            for v in values:
                if hint.casefold() in v.casefold():
                    cb.current(values.index(v))
                    return
            cb.current(0)

    def _dev_index(self, text):
        try:
            return int(text.split(" ", 1)[0])
        except Exception:
            return None

    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        di = self._dev_index(self.cb_bi.get())
        do = self._dev_index(self.cb_bo.get())
        if di is None or do is None:
            self.lb_bridge.config(text="Najpierw wybierz Wejscie i Wyjscie (Odswiez).")
            return
        fs_sel = self.fs.get()
        gain = 10 ** (self.gain.get() / 20.0)
        self.lb_bridge.config(text="Uruchamiam routing...")
        self._async(lambda: self._bridge_open(di, do, fs_sel, gain),
                    self._ui_bridge_open)

    def _bridge_open(self, di, do, fs_sel, gain):
        in_dev = sd.query_devices(di)
        out_dev = sd.query_devices(do)
        fs = int(in_dev["default_samplerate"]) if fs_sel == "auto" \
            else int(fs_sel)
        in_ch = max(1, in_dev["max_input_channels"])
        out_ch = max(1, min(2, out_dev["max_output_channels"]))

        state = {"stream": None}

        def cb(indata, outdata, frames, t, status):
            x = indata * gain
            ch_in = x.shape[1]
            if ch_in != out_ch:
                if ch_in > out_ch:
                    x = x[:, :out_ch]
                else:
                    cols = ([0] * out_ch if ch_in == 1
                            else list(range(ch_in))
                                 + [ch_in - 1] * (out_ch - ch_in))
                    x = x[:, cols]
            outdata[:] = x
            self.last_level = float((x * x).mean()) ** 0.5

        st = sd.Stream(samplerate=fs, dtype="float32", latency=0.1,
                       blocksize=max(1, int(fs * 0.05)),
                       device=(di, do), channels=(in_ch, out_ch), callback=cb)
        st.start()
        state["stream"] = st
        return ("ok", in_dev["name"], out_dev["name"], fs, st)

    def _ui_bridge_open(self, r):
        if isinstance(r, tuple) and r and r[0] == "__ERR__":
            self.lb_bridge.config(text="BLAD startu routingu: %s" % (r[1],))
            return
        _, in_name, out_name, fs, st = r
        self.stream = st
        self.running = True
        self.btn.config(text="WYLACZ ROUTING", bg="#c62828",
                        activebackground="#b71c1c")
        self.lb_bridge.config(
            text="Dziala: %s  ->  %s  @%d Hz" % (in_name, out_name, fs))

    def stop(self):
        if self.stream is not None:
            st = self.stream
            self.stream = None
            self.running = False
            self.last_level = 0.0
            self.btn.config(text="WLACZ ROUTING", bg="#2e7d32",
                            activebackground="#1b5e20")
            self.lb_bridge.config(text="Routing wylaczony.")

            def close_it():
                try:
                    st.stop()
                    st.close()
                except Exception:
                    pass

            threading.Thread(target=close_it, daemon=True).start()
            return
        self.running = False

    def _tick_vu(self):
        if self.running:
            w = self.canvas.winfo_width()
            lvl = min(1.0, self.last_level * 2.2)
            self.canvas.coords(self.bar, 0, 0, max(2, int(w * lvl)), 18)
            c = "#e53935" if lvl >= 0.98 else ("#fdd835" if lvl >= 0.85 else "#4caf50")
            self.canvas.itemconfig(self.bar, fill=c)
        self.root.after(120, self._tick_vu)

    # ---------- Sekcja 2: FLEXASIO ----------
    def _build_flexasio(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=" FLEXASIO (ASIO) ")
        pad = {"padx": 8, "pady": 4}
        self.lb_fx_status = ttk.Label(f, text="Sprawdzanie...")
        self.lb_fx_status.grid(row=0, column=0, columnspan=4, sticky="w", **pad)
        self.btn_fx_install = ttk.Button(f, text="Zainstaluj FlexASIO", command=self.install_flexasio)
        self.btn_fx_install.grid(row=1, column=0, **pad)
        ttk.Button(f, text="Sprawdz ponownie", command=self.check_flexasio).grid(row=1, column=1, **pad)

        ttk.Label(f, text="Wejscie ASIO (co widzi DAW/JAM VOX):").grid(row=2, column=0, columnspan=2, sticky="w", **pad)
        self.cb_fx_in = ttk.Combobox(f, state="readonly", width=52)
        self.cb_fx_in.grid(row=3, column=0, columnspan=4, sticky="we", **pad)
        ttk.Label(f, text="Wyjscie ASIO (dokad gra DAW/JAM VOX):").grid(row=4, column=0, columnspan=2, sticky="w", **pad)
        self.cb_fx_out = ttk.Combobox(f, state="readonly", width=52)
        self.cb_fx_out.grid(row=5, column=0, columnspan=4, sticky="we", **pad)

        btn_row = ttk.Frame(f)
        btn_row.grid(row=6, column=0, columnspan=4, sticky="we", pady=(10, 2))
        ttk.Button(btn_row, text="Profil: JAM VOX",
                   command=lambda: self.apply_profile("jamvox")).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Profil: OBS",
                   command=lambda: self.apply_profile("obs")).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Zapisz .toml",
                   command=self.apply_profile_manual).pack(side="left", padx=2)
        ttk.Button(btn_row, text="Pokaz .toml", command=self.show_toml).pack(side="left", padx=2)

        self.buf_ms = tk.IntVar(value=10)
        self.ch = tk.IntVar(value=2)
        ttk.Label(f, text="Bufor [ms]:").grid(row=7, column=0, sticky="e", **pad)
        ttk.Spinbox(f, from_=4, to=60, textvariable=self.buf_ms, width=6).grid(row=7, column=1, sticky="w", **pad)
        ttk.Label(f, text="Kanaly:").grid(row=7, column=2, sticky="e", **pad)
        ttk.Spinbox(f, from_=1, to=8, textvariable=self.ch, width=6).grid(row=7, column=3, sticky="w", **pad)

        self.lb_fx = ttk.Label(f, text="", wraplength=480, justify="left")
        self.lb_fx.grid(row=8, column=0, columnspan=4, sticky="w", **pad)

    def check_flexasio(self, *_):
        self._async(self._flex_compute, self._ui_flex_status)

    def _flex_compute(self):
        reg_ok, pads = flexasio_status()
        return reg_ok, pads, flexasio_devices(pads)

    def _ui_flex_status(self, r):
        if isinstance(r, tuple) and r and r[0] == "__ERR__":
            self.lb_fx_status.config(
                text="Blad sprawdzenia FlexASIO: %s" % (r[1],))
            self.btn_fx_install.config(state="normal")
            return
        reg_ok, pads, devs = r
        self.pads = pads
        self.fx_devices = devs
        cur_in = self.cb_fx_in.get()
        cur_out = self.cb_fx_out.get()
        self.cb_fx_in["values"] = devs
        self.cb_fx_out["values"] = devs
        self._preselect(self.cb_fx_in, self.fx_devices, "cable output")
        self._preselect(self.cb_fx_out, self.fx_devices, "sluchawki")
        self._preselect(self.cb_fx_out, self.fx_devices, "głośniki")
        for cb, cur in ((self.cb_fx_in, cur_in), (self.cb_fx_out, cur_out)):
            if cur and cur in list(cb["values"]):
                cb.current(list(cb["values"]).index(cur))
        if reg_ok and self.pads:
            self.lb_fx_status.config(
                text="FlexASIO: ZAINSTALOWANY (rejestr OK, x64: %s)"
                     % self.pads[0])
            self.btn_fx_install.config(state="disabled")
        elif reg_ok:
            self.lb_fx_status.config(
                text="FlexASIO: zarejestrowany, ale brak plikow w Program Files")
            self.btn_fx_install.config(state="normal")
        else:
            self.lb_fx_status.config(
                text="FlexASIO: BRAK - kliknij 'Zainstaluj FlexASIO'")
            self.btn_fx_install.config(state="normal")

    def install_flexasio(self):
        if self.installing:
            return
        exe = resource_path(os.path.join("installers", FLX_INSTALLER))
        if not os.path.exists(exe):
            messagebox.showerror("Brak instalatora",
                                 "Nie znaleziono wbudowanego instalatora:\n%s" % exe)
            return
        self.installing = True
        self.btn_fx_install.config(state="disabled", text="Instalacja...")
        self.lb_fx_status.config(text="Uruchamiam instalator - potwierdz UAC, potem wroc.")
        self.root.update()
        try:
            subprocess.Popen([exe, "/S"], creationflags=NO_WINDOW)
        except Exception as e:
            messagebox.showerror("Blad", str(e))
        self._poll_install(0)
        self.installing = False

    def _poll_install(self, n):
        reg, pads = flexasio_status()
        if reg and pads:
            self.check_flexasio()
            self.lb_fx_status.config(text="FlexASIO zainstalowany i zarejestrowany. Gotowe.")
            return
        if n >= 20:
            self.btn_fx_install.config(state="normal", text="Zainstaluj FlexASIO")
            self.lb_fx_status.config(text="Instalator zakonczony, ale nie wykryto rejestracji.")
            return
        self.lb_fx_status.config(text="Instalacja w toku... (%d/20)" % n)
        self.root.after(3000, self._poll_install, n + 1)

    def refresh_fx_device_list(self, *_):
        if self.fx_devices:
            self.cb_fx_in["values"] = self.fx_devices
            self.cb_fx_out["values"] = self.fx_devices
        else:
            self.check_flexasio()

    def apply_profile(self, prof):
        devs = self.fx_devices
        if not devs:
            messagebox.showwarning("Brak urzadzen",
                                   "Nie wczytano listy urzadzen (FlexASIO zainstalowany?).")
            return
        if prof == "jamvox":
            inp = pick(devs, ["axe io one", "axe io", "ik multimedia"]) or self.cb_fx_in.get()
            outp = pick(devs, ["cable input"]) or self.cb_fx_out.get()
            note = "JAM VOX tryb: ONE -> JAM VOX -> CABLE -> OBS. Nie uzywaj MOSTU rownoczesnie."
        else:
            inp = pick(devs, ["cable output"]) or self.cb_fx_in.get()
            outp = pick(devs, ["słuchawki", "głośniki", "speakers"]) or self.cb_fx_out.get()
            note = "OBS tryb: monitoring przez FlexASIO na glosniki, wejscie z kabla."
        if not inp or not outp:
            messagebox.showwarning("Niedopelnione wejscie/wyjscie",
                                   "Profil: popracze automatycznie. Wybierz urzadzenia recznie (lista urzadzen EXE).")
            return
        self.cb_fx_in.current(devs.index(inp))
        self.cb_fx_out.current(devs.index(outp))
        write_toml(inp, outp, self.buf_ms.get(), self.ch.get(), FLX_CONFIG, note)
        self.show_toml()
        self.lb_fx.config(
            text="Profil %s zapisany:\n %s\nWejscie: %s\nWyjscie: %s" % (prof.upper(), FLX_CONFIG, inp, outp))

    def apply_profile_manual(self):
        inp = self.cb_fx_in.get()
        outp = self.cb_fx_out.get()
        if not inp:
            messagebox.showwarning("Brak wejscia", "Wybierz urzadzenie wejscia ASIO.")
            return
        write_toml(inp, outp, self.buf_ms.get(), self.ch.get(), FLX_CONFIG)
        self.lb_fx.config(text="FlexASIO.toml zapisany:\nWejscie: %s\nWyjscie: %s" % (inp, outp or "wylaczone"))

    def show_toml(self):
        try:
            with open(FLX_CONFIG, "r", encoding="utf-8") as f:
                txt = f.read()
        except Exception:
            txt = "(brak pliku)"
        w = tk.Toplevel(self.root)
        w.title("FlexASIO.toml - %s" % FLX_CONFIG)
        w.geometry("560x380")
        st = scrolledtext.ScrolledText(w, wrap="none")
        st.pack(fill="both", expand=True)
        st.insert("1.0", txt)
        st.config(state="disabled")

    # ---------- Sekcja 3: ASIO Capture (wtyczka OBS) ----------
    def _build_asiocap(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=" ASIO Capture (wtyczka) ")
        pad = {"padx": 8, "pady": 4}
        self.lb_ac_status = ttk.Label(f, text="Sprawdzanie...", wraplength=480,
                                      justify="left")
        self.lb_ac_status.grid(row=0, column=0, columnspan=4, sticky="w", **pad)
        ttk.Button(f, text="Zainstaluj / aktualizuj wtyczke",
                   command=self.install_asiocap).grid(row=1, column=0, **pad)
        ttk.Button(f, text="Sprawdz ponownie",
                   command=self.check_asiocap).grid(row=1, column=1, **pad)

        ttk.Label(f, text="Sterowniki ASIO (HKLM\\SOFTWARE\\ASIO):").grid(
            row=2, column=0, columnspan=4, sticky="w", **pad)
        cols = ("dll", "state")
        self.tree_ac = ttk.Treeview(f, columns=cols, show="tree", height=7,
                                    selectmode="browse")
        self.tree_ac.heading("#0", text="Sterownik")
        self.tree_ac.column("#0", width=150)
        self.tree_ac.heading("dll", text="DLL")
        self.tree_ac.column("dll", width=240, anchor="w")
        self.tree_ac.heading("state", text="Stan")
        self.tree_ac.column("state", width=80, anchor="center")
        self.tree_ac.grid(row=3, column=0, columnspan=4, sticky="we", **pad)
        self.tree_ac.bind("<<TreeviewSelect>>", self._on_ac_select)

        self.lb_ac_sel = ttk.Label(f, text="", wraplength=480, justify="left")
        self.lb_ac_sel.grid(row=4, column=0, columnspan=4, sticky="w", **pad)

        btn2 = ttk.Frame(f)
        btn2.grid(row=5, column=0, columnspan=4, sticky="w", **pad)
        ttk.Button(btn2, text="Przywroc zaznaczony",
                   command=self.restore_ac_selected).pack(side="left", padx=2)
        ttk.Button(btn2, text="Przywroc wszystkie",
                   command=self.restore_ac_all).pack(side="left", padx=2)
        ttk.Button(btn2, text="Odkryl (wskaz) OBS",
                   command=self.reveal_obs).pack(side="left", padx=2)

        btn3 = ttk.Frame(f)
        btn3.grid(row=6, column=0, columnspan=4, sticky="w", **pad)
        ttk.Button(btn3, text="Zainstaluj redirect 32-bit (JAM VOX)",
                   command=self.install_ac_redirect32).pack(side="left", padx=2)
        ttk.Button(btn3, text="Przywroc redirect 32-bit",
                   command=self.restore_ac_redirect32).pack(side="left", padx=2)
        self.lb_ac32 = ttk.Label(f, text="", wraplength=520, justify="left")
        self.lb_ac32.grid(row=7, column=0, columnspan=4, sticky="w", **pad)

        ttk.Label(f, text="JAM VOX - wyjscie audio (Preferences3.xml):"
                  ).grid(row=8, column=0, columnspan=4, sticky="w", **pad)
        btn4 = ttk.Frame(f)
        btn4.grid(row=9, column=0, columnspan=4, sticky="w", **pad)
        ttk.Button(btn4, text="Przelacz JAM VOX na ASIO",
                   command=self.vox_to_asio).pack(side="left", padx=2)
        ttk.Button(btn4, text="Sprawdz ponownie",
                   command=self.check_vox).pack(side="left", padx=2)
        ttk.Button(btn4, text="Pokaz plik konfiguracyjny",
                   command=self.reveal_vox_pref).pack(side="left", padx=2)
        self.lb_vox = ttk.Label(f, text="", wraplength=520, justify="left")
        self.lb_vox.grid(row=10, column=0, columnspan=4, sticky="w", **pad)

    def check_vox(self, *_):
        """Pokazuje aktualne wyjscie audio JAM VOX i ostrzega, gdy to nie ASIO."""
        self._async(self._vox_compute, self._ui_vox)

    def _vox_compute(self):
        procs = jamvox_running()
        if procs:
            return ("running", ", ".join(procs))
        d = vox_device()
        if d is None:
            return ("nomissing", vox_pref_path())
        return (d.get("deviceType", "?"),
                d.get("audioOutputDeviceName", "?"),
                d.get("audioDeviceRate", "?"))

    def _ui_vox(self, r):
        if isinstance(r, tuple) and r and r[0] == "__ERR__":
            self.lb_vox.config(text="Blad sprawdzenia JAM VOX: %s" % (r[1],))
            return
        if r[0] == "running":
            self.lb_vox.config(
                text="JAM VOX uruchomiony (%s) - zamknij go "
                     "przed zmiana trybu audio." % r[1])
            return
        if r[0] == "nomissing":
            self.lb_vox.config(
                text="Nie znaleziono %s - uruchom JAM VOX raz i zamknij, "
                     "aby zapisal konfiguracje." % r[1])
            return
        dtype, out, rate = r
        if dtype.lower() == "asio":
            self.lb_vox.config(
                text="OK: JAM VOX gra przez ASIO (%s, %s Hz). Ten sam "
                     "sterownik musi byc wybrany w OBS."
                     % (out, rate))
        else:
            self.lb_vox.config(
                text="PROBLEM: JAM VOX gra przez \"%s\" (wyjscie: %s), a "
                     "proxy przechwytuje tylko ASIO - w OBS bedzie cisza.\n"
                     "Popraw: zaznacz sterownik na liscie wyzej i kliknij "
                     "'Przelacz JAM VOX na ASIO'." % (dtype, out))

    def vox_to_asio(self):
        iid = self.tree_ac.focus()
        sel = next((d for d in getattr(self, "ac_drivers", [])
                    if d["name"] == iid), None)
        if sel is None:
            messagebox.showwarning(
                "JAM VOX", "Najpierw zaznacz sterownik ASIO na liscie "
                           "sterownikow powyzej.\n\n"
                           "Sterownik musi byc TEN SAM co w zrodle OBS.")
            return
        name = sel["name"]
        if not messagebox.askyesno(
                "JAM VOX -> ASIO",
                "Przelaczyc JAM VOX na ASIO?\n\n"
                "Sterownik : %s\nPlik      : %s\n\n"
                "JAM VOX musi byc zamkniety. Zrobie kopie bezpieczenstwa "
                "pliku konfiguracyjnego." % (name, vox_pref_path())):
            return
        ok, msg = write_vox_asio(name)
        self.check_vox()
        if ok:
            messagebox.showinfo("JAM VOX", msg)
        else:
            messagebox.showerror("JAM VOX", msg)

    def reveal_vox_pref(self):
        p = vox_pref_path()
        if os.path.isfile(p):
            self.root.clipboard_clear()
            self.root.clipboard_append(p)
            messagebox.showinfo("Preferences3.xml",
                                "Sciezka skopiowana do schowka:\n%s" % p)
        else:
            messagebox.showwarning(
                "Preferences3.xml",
                "Nie znaleziono pliku:\n%s\n\nUruchom JAM VOX i zamknij go."
                % (p or "(brak APPDATA)"))

    def check_asiocap(self, *_):
        self._async(self._ac_compute, self._ui_ac_status)

    def _ac_compute(self):
        pd = obs_plugins_dir()
        ok_c = ok_p = ok_x = False
        if pd:
            ok_c = os.path.exists(os.path.join(pd, ASIO_CAPTURE_DLL))
            ok_p = os.path.exists(os.path.join(pd, ASIO_PROXY_DLL))
            ok_x = os.path.exists(os.path.join(pd, ASIO_PROXY_X86_DLL))
        return pd, ok_c, ok_p, ok_x

    def _ui_ac_status(self, r):
        if isinstance(r, tuple) and r and r[0] == "__ERR__":
            self.lb_ac_status.config(
                text="Blad sprawdzenia wtyczki: %s" % (r[1],))
            return
        pd, ok_c, ok_p, ok_x = r
        have_obs = bool(pd)
        if ok_c and ok_p:
            extra = ""
            if not ok_x:
                extra = "\nUWAGA: brak %s - kliknij 'Zainstaluj / aktualizuj'." % ASIO_PROXY_X86_DLL
            self.lb_ac_status.config(
                text="Wtyczka ASIO Capture: ZAINSTALOWANA w OBS (%s). "
                     "Dodaj zrodlo 'DAW Audio Capture (ASIO)', wybierz sterownik "
                     "i uruchom DAW/JAM VOX.%s" % (pd, extra))
        else:
            parts = []
            if not have_obs:
                parts.append("nie znaleziono OBS (zainstaluj OBS Studio)")
            else:
                if not ok_c:
                    parts.append("brak %s" % ASIO_CAPTURE_DLL)
                if not ok_p:
                    parts.append("brak %s" % ASIO_PROXY_DLL)
            self.lb_ac_status.config(
                text="Wtyczka ASIO Capture: BRAK - " + ", ".join(parts) +
                     ". Kliknij 'Zainstaluj / aktualizuj wtyczke'.")
        self.refresh_asio_tree()
        self.check_vox()

    def refresh_asio_tree(self):
        self.ac_drivers = asio_drivers()
        self.tree_ac.delete(*self.tree_ac.get_children())
        for d in self.ac_drivers:
            if d["original"]:
                state = "wtyczka"
            elif d["clsid"] and d["dll"]:
                state = "zwykly"
            else:
                state = "?"
            self.tree_ac.insert("", "end", iid=d["name"],
                                text=d["name"],
                                values=(d["dll"] or "(brak)", state))
        if not self.ac_drivers:
            self.tree_ac.insert("", "end", text="(brak zarejestrowanych)")
            return
        # zaznacz ostatnio wybrany sterownik (asio_choice.txt)
        want = ""
        try:
            p = os.path.join(exe_dir(), AC_CHOICE)
            if os.path.exists(p):
                with open(p, "r", encoding="utf-8") as f:
                    want = f.read().strip()
        except Exception:
            pass
        if want:
            for d in self.ac_drivers:
                if d["name"] == want and self.tree_ac.exists(want):
                    self.tree_ac.selection_set(want)
                    self.tree_ac.focus(want)
                    self.tree_ac.see(want)
                    self._on_ac_select()
                    break

    def _on_ac_select(self, *_):
        iid = self.tree_ac.focus()
        sel = next((d for d in getattr(self, "ac_drivers", [])
                    if d["name"] == iid), None)
        if not sel:
            self.lb_ac_sel.config(text="")
            self.lb_ac32.config(text="")
            return
        # zapamietaj wybor sterownika (asio_choice.txt obok .exe)
        try:
            with open(os.path.join(exe_dir(), AC_CHOICE), "w",
                      encoding="utf-8") as f:
                f.write(sel["name"] + "\n")
        except Exception:
            pass
        txt = "%s [%s]\nInprocServer32: %s" % (sel["name"], sel["clsid"],
                                                sel["dll"] or "(brak)")
        if sel["original"]:
            txt += "\nOriginalServer: %s  (aktywny redirect przez asio-proxy)" % sel["original"]
        self.lb_ac_sel.config(text=txt)

        dll32, orig32 = redirect32_info(sel["clsid"])
        if dll32 or orig32:
            part = "32-bit (WOW6432Node): %s" % dll32
            if orig32:
                part += "\n  -> redirect AKTYWNY, OriginalServer: %s" % orig32
            self.lb_ac32.config(text=part)
        else:
            self.lb_ac32.config(text="32-bit: brak wpisu rejestru dla tego sterownika.")

    def install_asiocap(self):
        src_c = resource_path(os.path.join("installers", ASIO_CAPTURE_DLL))
        src_p = resource_path(os.path.join("installers", ASIO_PROXY_DLL))
        src_x = resource_path(os.path.join("installers", ASIO_PROXY_X86_DLL))
        for p in (src_c, src_p, src_x):
            if not os.path.exists(p):
                messagebox.showerror(
                    "Brak wbudowanych plikow",
                    "Nie znaleziono DLL: %s" % p)
                return
        pd = obs_plugins_dir()
        if not pd:
            messagebox.showerror("OBS", "Nie znaleziono katalogu OBS.")
            return
        body = '$srcC = %s\n$srcP = %s\n$srcX = %s\n$dest = %s\n' % (
            json.dumps(src_c), json.dumps(src_p), json.dumps(src_x),
            json.dumps(pd))
        body += ('Copy-Item -LiteralPath $srcC -Destination $dest -Force\n'
                 'Copy-Item -LiteralPath $srcP -Destination $dest -Force\n'
                 'Copy-Item -LiteralPath $srcX -Destination $dest -Force\n'
                 '"OK"\n')
        self.lb_ac_status.config(text="Kopiowanie DLL do OBS - potwierdz UAC...")
        self.root.update()
        ok, _ = run_elevated(body)
        if ok:
            self.lb_bottom.config(text="Wtyczka skopiowana do %s" % pd)
        else:
            messagebox.showerror("UAC", "Skrypt administracyjny nie zostal "
                                        "wykonany lub zakonczony bledem.")
        self.check_asiocap()

    def restore_ac_selected(self):
        iid = self.tree_ac.focus()
        sel = next((d for d in getattr(self, "ac_drivers", [])
                    if d["name"] == iid), None)
        if not sel or not sel["original"]:
            messagebox.showinfo("Brak redirectu", "Ten sterownik nie jest "
                                 "przekierowany przez wtyczke.")
            return
        self.restore_ac_driver(sel, "restore_one")

    def restore_ac_all(self):
        targets = [d for d in getattr(self, "ac_drivers", []) if d["original"]]
        if not targets:
            messagebox.showinfo("Brak redirectow",
                                "Zaden sterownik nie jest przekierowany.")
            return
        found = any(self.restore_ac_driver(d, "restore_all") for d in targets)
        if not found:
            messagebox.showinfo("Brak redirectow",
                                "Nie odnaleziono zaden OriginalServer.")
        self.check_asiocap()

    def restore_ac_driver(self, d, mode):
        if not d["clsid"] or not d["original"]:
            return False
        key = r"HKLM\SOFTWARE\Classes\CLSID\%s\InprocServer32" % d["clsid"]
        body = ('reg.exe add "%s" /ve /t REG_SZ /d "%s" /f\n'
                'reg.exe delete "%s" /v OriginalServer /f\n'
                '"OK %s"\n' % (key, d["original"], key, d["name"]))
        self.lb_ac_status.config(text="Przywracanie %s - potwierdz UAC..." % d["name"])
        self.root.update()
        ok, _ = run_elevated(body)
        if ok:
            self.lb_bottom.config(text="Przywrocono: %s (%s)" % (d["name"],
                                                                 d["original"]))
        else:
            messagebox.showerror("UAC", "Nie przywrocono: %s" % d["name"])
        return ok

    def install_ac_redirect32(self):
        iid = self.tree_ac.focus()
        sel = next((d for d in getattr(self, "ac_drivers", [])
                    if d["name"] == iid), None)
        if not sel or not sel["clsid"]:
            messagebox.showinfo("Sterownik", "Wybierz sterownik z listy "
                                            "(np. AXE IO ONE).")
            return
        src_x = resource_path(os.path.join("installers", ASIO_PROXY_X86_DLL))
        pd = obs_plugins_dir()
        if not pd or not os.path.exists(src_x):
            messagebox.showerror("Brak plikow", "Nie znaleziono %s w bundle."
                                            % ASIO_PROXY_X86_DLL)
            return
        dll32, orig32 = redirect32_info(sel["clsid"])
        if orig32:
            messagebox.showinfo("Redirect 32-bit aktywny",
                "Juz przekierowany na %s\n\n"
                "(OriginalServer: %s)" % (dll32, orig32))
            return
        if not dll32:
            messagebox.showerror("Brak wpisu 32-bit",
                "Sterownik %s nie ma wpisu InprocServer32 "
                "w widoku 32-bit (WOW6432Node)." % sel["name"])
            return
        proxy_x = os.path.join(pd, ASIO_PROXY_X86_DLL)
        key = (r"HKLM\SOFTWARE\WOW6432Node\Classes\CLSID\%s"
               r"\InprocServer32" % sel["clsid"])
        body = ('$s = %s\n$d = %s\n$k = %s\n$orig = "%s"\n' % (
            json.dumps(src_x), json.dumps(pd), json.dumps(key), dll32))
        body += ('if (-not (Test-Path "$d\\%s")) '
                 '{ Copy-Item -LiteralPath $s -Destination $d -Force }\n'
                 'reg.exe add $k /ve /t REG_SZ '
                 '/d "%s\\%s" /f\n'
                 'reg.exe add $k /v OriginalServer /t REG_SZ '
                 '/d $orig /f\n'
                 '"OK"\n' % (ASIO_PROXY_X86_DLL, pd, ASIO_PROXY_X86_DLL))
        self.lb_ac_status.config(
            text="Redirect 32-bit dla %s - potwierdz UAC..." % sel["name"])
        self.root.update()
        ok, txt = run_elevated(body)
        self._ac32_feedback(ok, "Redirect 32-bit ustawiony (JAM VOX)", txt,
                            sel["name"])
        if sel["original"] is None:
            self.check_asiocap()

    def restore_ac_redirect32(self):
        iid = self.tree_ac.focus()
        sel = next((d for d in getattr(self, "ac_drivers", [])
                    if d["name"] == iid), None)
        if not sel or not sel["clsid"]:
            messagebox.showinfo("Sterownik", "Wybierz sterownik z listy.")
            return
        dll32, orig32 = redirect32_info(sel["clsid"])
        if not orig32:
            messagebox.showinfo("Brak redirectu 32-bit",
                                "Ten sterownik nie ma zapisanego OriginalServer "
                                "(stan oryginalny).")
            return
        key = (r"HKLM\SOFTWARE\WOW6432Node\Classes\CLSID\%s"
               r"\InprocServer32" % sel["clsid"])
        body = ('reg.exe add "%s" /ve /t REG_SZ /d "%s" /f\n'
                'reg.exe delete "%s" /v OriginalServer /f\n'
                '"OK %s"\n' % (key, orig32, key, sel["name"]))
        self.lb_ac_status.config(
            text="Przywracanie 32-bit dla %s - potwierdz UAC..." % sel["name"])
        self.root.update()
        ok, txt = run_elevated(body)
        self._ac32_feedback(ok, "Redirect 32-bit przywrocony", txt,
                            sel["name"])
        self.check_asiocap()

    def _ac32_feedback(self, ok, ok_text, txt, name):
        if ok:
            self.lb_bottom.config(text="%s (%s)" % (ok_text, name))
            messagebox.showinfo(ok_text, "OK")
        else:
            messagebox.showerror("UAC/Blad",
                "Skrypt administracyjny nie powiodl sie.\n" + (txt or ""))

    def reveal_obs(self):
        d = obs_dir()
        if not d:
            messagebox.showerror("OBS", "Nie znaleziono OBS Studio.")
            return
        subprocess.Popen(["explorer.exe", d], creationflags=NO_WINDOW)

    # ---------- Sekcja 3b: MONITORING BEZ OBS ----------
    def _build_monitor(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=" Monitoring (bez OBS) ")
        pad = {"padx": 8, "pady": 4}

        self.lb_mon_status = ttk.Label(f, text="", wraplength=520, justify="left")
        self.lb_mon_status.grid(row=0, column=0, columnspan=4, sticky="w", **pad)

        cfg = load_config(MON_CONFIG, {"dev": "", "pair": "Out 1-2",
                                      "gain_db": 0.0, "lag_ms": 4,
                                      "render": "manager"})

        ttk.Label(f, text="Urzadzenie wyjsciowe:").grid(
            row=1, column=0, sticky="w", **pad)
        self.mon_dev = ttk.Combobox(f, state="readonly", width=52)
        self.mon_dev["values"] = ["(sprawdzam urzadzenia...)"]
        self.mon_dev.current(0)
        self.mon_dev.grid(row=2, column=0, columnspan=4, sticky="we", **pad)
        ttk.Button(f, text="Odswiez liste",
                   command=self.mon_refresh_devices).grid(row=3, column=0, **pad)
        self._mon_devs = []

        ttk.Label(f, text="para wyjscia").grid(row=4, column=1, sticky="w", **pad)
        self.mon_pair = ttk.Combobox(f, state="readonly", width=10,
                                     values=["Out 1-2", "Out 3-4", "Out 5-6", "Out 7-8"])
        self.mon_pair.set(cfg["pair"])
        self.mon_pair.grid(row=4, column=2, sticky="w", **pad)

        ttk.Label(f, text="zegar wyjscia:").grid(row=3, column=1, sticky="w", **pad)
        RATES = ["Auto (zegar ASIO)", "44100 Hz", "48000 Hz"]
        self.mon_rate = ttk.Combobox(f, state="readonly", width=18, values=RATES)
        try:
            ri = [0, 44100, 48000].index(int(cfg.get("rate", 0)))
        except Exception:
            ri = 0
        self.mon_rate.current(ri)
        self.mon_rate.grid(row=3, column=2, sticky="w", **pad)
        ttk.Label(f, text="(rate inny niz w ASIO = konwersja czasu; "
                          "urzadzenie musi go obslugiwac)").grid(
            row=3, column=3, sticky="w", **pad)

        ttk.Label(f, text="glosnosc dB").grid(row=5, column=0, sticky="w", **pad)
        self.mon_gain = ttk.Scale(f, from_=-60, to=12, orient="horizontal",
                                  length=220,
                                  command=lambda v: self.lb_mon_gain.config(
                                      text="%+.0f dB" % float(v)))
        self.mon_gain.set(float(cfg["gain_db"]))
        self.mon_gain.grid(row=5, column=1, columnspan=2, sticky="we", **pad)
        self.lb_mon_gain = ttk.Label(f, text="%+.0f dB" % float(cfg["gain_db"]))
        self.lb_mon_gain.grid(row=5, column=3, sticky="w", **pad)

        ttk.Label(f, text="ms opoznienia").grid(row=6, column=0, sticky="w", **pad)
        # Odkad blok czytnika to 128 klatek (~2.9 ms), dolna granica spadla
        # z 8 ms do 4 ms. Jeszcze mniej = przerwy w odczycie SHM przy
        # rozjezdzie zegarow. Dla najnizszej latencji uzyj renderu "proxy".
        self.mon_lag = ttk.Spinbox(f, from_=4, to=250, width=6,
                                   text=str(max(4, cfg["lag_ms"])))
        self.mon_lag.grid(row=6, column=1, sticky="w", **pad)
        ttk.Label(f, text="min 4 (ponizej = przerwy)",
                  foreground="#8a6d1f").grid(row=6, column=2,
                                             columnspan=2, sticky="w", **pad)

        ttk.Label(f, text="latencja wyjscia").grid(row=9, column=0,
                                                   sticky="w", **pad)
        # wariant "system" zostawia wybor bufora Windows (test 6 brzmial
        # najlepiej); "low" wymusza 22 ms, ale na tym sterowniku potrafi
        # odrzucic WDM przy starcie (WdmSyncIoctl 0x490)
        self.mon_lat = ttk.Combobox(
            f, width=30, state="readonly",
            values=("system (wybrane przez Windows)", "low (22 ms)"))
        self.mon_lat.current(
            1 if cfg.get("latencja", "system") == "low" else 0)
        self.mon_lat.grid(row=9, column=1, columnspan=2, sticky="w", **pad)

        ttk.Label(f, text="kto gra do sluchawek").grid(row=10, column=0,
                                                       sticky="w", **pad)
        # "proxy" = direct monitoring: SHM -> watek w DAW -> WASAPI, bez
        # Pythona i jego 10 ms zapasu. "manager" = stara droga przez nas.
        self.mon_render = ttk.Combobox(
            f, width=44, state="readonly",
            values=("manager (przez ten program)",
                    "proxy (direct - bezposrednio z DAW)"))
        self.mon_render.current(
            1 if cfg.get("render", "manager") == "proxy" else 0)
        self.mon_render.grid(row=10, column=1, columnspan=3, sticky="w", **pad)

        self.mon_auto = tk.BooleanVar(value=bool(cfg.get("mon_auto", True)))
        ttk.Checkbutton(f, text="Routing automatyczny (jak zrodlo w OBS:"
                                " podepnie sie, gdy proxy zagra)",
                        variable=self.mon_auto,
                        command=self.mon_auto_toggle).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(10, 2))
        ttk.Button(f, text="Zapisz ustawienia",
                   command=self.mon_save).grid(row=7, column=3, sticky="we", **pad)

        self.lb_mon_lat = ttk.Label(f, text="", wraplength=520, justify="left",
                                    font=("Consolas", 9))
        self.lb_mon_lat.grid(row=8, column=0, columnspan=4, sticky="w", **pad)

        # ---- silnik monitoringu: WSZYSTKO (urzadzenia, SHM, strumien,
        #      tasklist) leci w watku tla; GUI tylko czyta snapshot i dostaje
        #      gotowce przez kolejke. Zadne audio/NIE zasypia  watku GUI.
        self.mon = None
        self._rstop = threading.Event()
        self._rlock = threading.Lock()
        self._rdata = {"diag": self._mon_hint(), "mon": "czeka na proxy (auto)",
                       "auto": bool(cfg.get("mon_auto", True)),
                       "want_dev": str(cfg.get("dev", "")),
                       "cfg": (None, 0, 1, float(cfg["gain_db"]),
                               max(4, min(250, int(cfg["lag_ms"]))),
                               int(cfg.get("rate", 0) or 0),
                               cfg.get("latencja", "system")),
                       "last_wp": None, "stall": 0.0, "refresh_dev": True,
                       "want_attach": False, "want_release": False,
                       "render_proxy": (cfg.get("render") == "proxy")}
        self._ctl = DirectCtl()   # blok sterujacy z proxy (moze nie powstac)
        self._attach_fail_at = 0.0
        self._mon_err = ""
        self._au_spin = 0
        self._dev_refresh_at = 0.0
        self.lb_mon_status.config(text=self._mon_hint())
        self.root.after(600, self._mon_status_tick)
        self.root.after(150, self._mon_service_tick)
        self.root.after(400, self._direct_tick)
        threading.Thread(target=self._router_loop, daemon=True).start()

    def _mon_service_tick(self):
        # Kto gra do sluchawek: piszemy to stad, bo watek GUI ma dostep do
        # widgetow, a router (watek tla) tylko czyta gotowa flage. Raz na
        # 150 ms, bez blokad - przeciez to tylko kilka liczb.
        try:
            proxy = (self.mon_render.current() == 1)
        except Exception:
            proxy = False
        devname = ""
        try:
            n = self.mon_dev.current()
            if 0 <= n < len(self._mon_devs):
                # etykieta ma postac "nazwa  [Windows WASAPI]" - proxy
                # porownuje z nazwa endpointu, wiec obcinamy dopisek
                devname = self._mon_devs[n][0].split("  [")[0].strip()
        except Exception:
            devname = ""
        # bez mapy sterujacej direct nie zadziala - trzymamy stara droge.
        # 'direct' pilnuje tez automatycznego routingu: auto wylaczone =
        # proxy tez ma przestac grac (ten checkbox to jedyne wlaczenie).
        try:
            auto = bool(self.mon_auto.get())
        except Exception:
            auto = True
        proxy = proxy and self._ctl.ok and auto
        try:
            self._ctl.write(proxy, devname)
        except Exception:
            pass
        with self._rlock:
            self._rdata["render_proxy"] = proxy

        # PortAudio/WDM na tym sterowniku odrzuca start streamu wywolany
        # z watku tla (WdmSyncIoctl 0x492, 8/8 prob), a z watku glownego
        # przechodzi.  Dlatego watek routera tylko probuje, a samo
        # open/close robi ten tick w watku GUI.  Koszt: jedno krotkie
        # wywolanie raz na 150 ms, wylacznie gdy jest co zrobic.
        try:
            with self._rlock:
                want = self._rdata.get("want_attach")
                rel = self._rdata.get("want_release")
                self._rdata["want_attach"] = False
                self._rdata["want_release"] = False
            if rel:
                self._mon_release()
            if want and self.mon is None and not proxy:
                self._mon_attach()
        except Exception as e:
            mon_log("BLAD _mon_service_tick: %s" % e)
        self.root.after(150, self._mon_service_tick)

    def _direct_tick(self):
        # Stan direct monitoringu w etykiecie opoznienia (tylko w trybie
        # proxy - w trybie manager te pole aktualizuje _mon_tick).
        with self._rlock:
            proxy = self._rdata.get("render_proxy", False)
        if proxy:
            act, st, lagf, per, rate, und = self._ctl.snapshot()
            r = float(rate) if rate > 0 else 1.0
            txt = ("kto gra: PROXY (direct)\n"
                   "stan: %s\n"
                   "okres WASAPI  %d klatek  (%.1f ms)\n"
                   "zapas (SHM)   %d klatek  (%.1f ms)\n"
                   "zegar %d Hz | przerwy %d\n"
                   "ustawienie 'kto gra' = manager, aby wrocic do tej drogi"
                   % (DIRECT_ST.get(st, "? %d" % st),
                      per, per * 1000.0 / r,
                      lagf, lagf * 1000.0 / r,
                      rate, und))
            if not self._ctl.ok:
                txt = ("kto gra: PROXY (direct)\n"
                       "BLAD: nie udalo sie otworzyc bloku sterujacego")
            self.lb_mon_lat.config(text=txt)
        self.root.after(400, self._direct_tick)

    def _mon_hint(self):
        return ("Tak jak zrodlo \"DAW Audio Capture\" w OBS: raz ustawiasz\n"
                "urzadzenie wyjsciowe, potem tylko startujesz program ASIO\n"
                "(JAM VOX / TH3) - routing podepnie sie sam. Bez przyciskow.\n"
                "\"ms opoznienia\" = czytamy TYLE za piszacym (bufor).\n"
                "Zwieksz, jak sie pluje; zmniejsz, jak chcesz szybciej.\n"
                "UWAGA: Bluetooth (Jabra) i HDMI (LG TV) maja w Windows "
                "twardy floor ~22 ms.")

    # ------  kolejka GUI: jedyny most z watku tla do interfejsu  ----------
    def _pump_gui(self):
        try:
            while True:
                job = self._gui_q.get_nowait()
                try:
                    job()
                except Exception:
                    pass
        except queue.Empty:
            pass
        try:
            self.root.after(40, self._pump_gui)
        except Exception:
            pass

    def _async(self, work, apply_ui):
        """Wykonuje work() na watku tla, wynik podaje apply_ui(r) na watku
        GUI. apply_ui(('__ERR__', e)) gdy work rzucil wyjatek."""

        def run():
            try:
                r = work()
            except Exception as e:
                r = ("__ERR__", e)
            self._gui_q.put(lambda: apply_ui(r))

        threading.Thread(target=run, daemon=True).start()

    def mon_refresh_devices(self):
        with self._rlock:
            self._rdata["refresh_dev"] = True

    def _gui_populate_devices(self, devs):
        self._mon_devs = devs
        if not devs:
            self.mon_dev["values"] = ["(brak urzadzen wyjsciowych)"]
            self.mon_dev.current(0)
            return
        self.mon_dev["values"] = [lbl for lbl, _ in devs]
        want = self._rdata.get("want_dev", "")
        pick = 0
        for n, (lbl, idx) in enumerate(devs):
            if str(idx) == str(want):
                pick = n
                break
        self.mon_dev.current(pick)

    def _gui_dev_err(self, msg):
        self.mon_dev["values"] = ["(blad urzadzen: %s)" % msg]
        self.mon_dev.current(0)

    def _gui_sched_mon_tick(self):
        self.root.after(300, self._mon_tick)

    def _gui_clear_lat(self):
        self.lb_mon_lat.config(text="")

    def mon_dev_index(self):
        try:
            n = self.mon_dev.current()
        except Exception:
            return None
        if n < 0 or n >= len(self._mon_devs):
            return None
        return self._mon_devs[n][1]

    def mon_cfg(self):
        try:
            g = float(self.mon_gain.get())
        except Exception:
            g = 0.0
        try:
            lg = int(float(self.mon_lag.get()))
        except Exception:
            lg = 10
        pair = self.mon_pair.get()
        base = 0
        try:
            base = (int(pair.split("-")[0]) - 1)
        except Exception:
            pass
        try:
            rate = [0, 44100, 48000][max(0, self.mon_rate.current())]
        except Exception:
            rate = 0
        return (self.mon_dev_index(), base, base + 1, g,
                max(4, min(250, lg)), rate, self.mon_lat.get())

    def mon_save(self):
        idx, cl, cr, g, lg, rate, _lat = self.mon_cfg()
        try:
            render = ("proxy" if self.mon_render.current() == 1
                      else "manager")
        except Exception:
            render = "manager"
        save_config(MON_CONFIG, {"dev": "" if idx is None else str(idx),
                                 "pair": self.mon_pair.get(),
                                 "gain_db": g, "lag_ms": lg,
                                 "rate": rate,
                                 "latencja": ("low" if self.mon_lat.current() == 1
                                              else "system"),
                                 "render": render,
                                 "mon_auto": bool(self.mon_auto.get())})

    def mon_auto_toggle(self):
        save_config(MON_CONFIG, {"mon_auto": bool(self.mon_auto.get())})
        with self._rlock:
            self._rdata["auto"] = bool(self.mon_auto.get())

    # ----------  router: watek tla, tutaj caly dzwiek i SHM  ----------
    def _router_loop(self):
        while not self._rstop.is_set():
            now = time.time()
            with self._rlock:
                auto = self._rdata.get("auto", True)
                cfg = self._rdata.get("cfg")
                refresh = self._rdata.get("refresh_dev", False)
                proxy = self._rdata.get("render_proxy", False)
            # lista urzadzen - raz na starcie i na klawisz "Odswiez",
            # tylko tutaj (PortAudio moze zajac sekunde lub dwie)
            if refresh and now - self._dev_refresh_at > 2.0:
                self._dev_refresh_at = now
                with self._rlock:
                    self._rdata["refresh_dev"] = False
                try:
                    devs = out_devices()
                except Exception as e:
                    self._gui_q.put(lambda e=e: self._gui_dev_err(str(e)))
                else:
                    self._gui_q.put(lambda d=devs: self._gui_populate_devices(d))
            if not auto:
                if self.mon is not None:
                    self._ask_release()
                with self._rlock:
                    self._rdata["last_wp"] = None
                    self._rdata["stall"] = 0.0
            else:
                ok, _sr, _nc, _act, wp = shm_quick()
                prev = self._rdata.get("last_wp")
                stall = self._rdata.get("stall", 0.0)
                if ok:
                    self._rdata["last_wp"] = wp
                    stall = (0.0 if (prev is None or wp != prev)
                             else stall + 0.3)
                else:
                    self._rdata["last_wp"] = None
                    stall += 0.3
                self._rdata["stall"] = stall
                if proxy:
                    # direct monitoring: jedyny renderer to watek w DAW.
                    # Nigdy nie podpinamy wlasnego strumienia - dwa zrodla
                    # gralyby to samo z roznych opoznien (fazowy smrod).
                    if self.mon is not None:
                        self._ask_release()
                elif (ok and self.mon is None
                        and now - self._attach_fail_at > 5.0
                        and cfg and cfg[0] is not None):
                    with self._rlock:
                        self._rdata["want_attach"] = True
                elif (not ok and self.mon is not None and stall > 4.0):
                    self._rdata["stall"] = 0.0
                    self._ask_release()
            # diagnostyka budowana W TLE (tasklist/registry/log),
            # POZA blokada: blokada tylko na chwile przy zapisie
            self._au_spin += 1
            if self.mon is not None:
                mon_txt = "PODPIETE - leci 1:1"
            elif proxy:
                mon_txt = self._proxy_status_txt()
            else:
                mon_txt = self._mon_err or "czeka na proxy (auto)"
            diag_txt = _mon_diag_text(
                full_scan=(self._au_spin % 6 == 0),
                mon_state=mon_txt)
            with self._rlock:
                self._rdata["mon"] = mon_txt
                self._rdata["diag"] = diag_txt
            time.sleep(0.3)

    def _proxy_status_txt(self):
        """Jedna linijka o tym, co robi watek direct w DAW (dla statusu)."""
        if not self._ctl.ok:
            return "PROXY DIRECT: brak bloku sterujacego"
        act, st, lagf, per, rate, und = self._ctl.snapshot()
        if st == 2:
            return ("PROXY DIRECT: gra bezposrednio (okres %d klatek, "
                    "zapas %d, przerwy %d)" % (per, lagf, und))
        return "PROXY DIRECT: %s" % DIRECT_ST.get(st, "? %d" % st)

    def _ask_release(self):
        with self._rlock:
            self._rdata["want_release"] = True

    def _mon_attach(self):
        # cfg = (dev, ch_l, ch_r, gain_db, lag_ms, rate, latencja) - rate moze
        # byc nieobecne (starsze ustawienia) wtedy zegar = zegar ASIO.
        # latencja: "low" = zadana przez PortAudio, "system" = wybrana przez
        # Windows (test 6 - na tym sterowniku dziala stabilnie)
        if self.mon is not None:
            return
        cfg = list(self.mon_cfg()) + ["system"] * (7 - len(self.mon_cfg()))
        idx, cl, cr, g, lg, rate, lat = cfg[:7]
        if idx is None:
            return
        m = AsioMonitor(idx, cl, cr, gain_db=g, lag_ms=lg, out_rate=rate)
        m.lat_mode = None if lat == "system" else "low"
        try:
            m.start()
        except Exception as e:
            self._attach_fail_at = time.time()
            self._mon_err = "BLAD startu: %s" % e
            mon_log("BLAD podpietycia dev=%s lag=%sms fs=%s blok=%s: %r"
                    % (idx, lg, m.in_rate, m.BLOCK, e))
            with self._rlock:
                self._rdata["mon"] = self._mon_err
            return
        self.mon = m
        self._mon_err = ""
        mon_log("podpieto dev=%s ch=%s/%s lag=%sms fs=%d->%d (watk GUI)"
                % (idx, cl, cr, lg, m.in_rate, m.out_rate))
        with self._rlock:
            self._rdata["mon"] = "PODPIETE - leci 1:1"
        self._gui_sched_mon_tick()

    def _mon_release(self):
        if self.mon is None:
            return
        try:
            self.mon.stop()
        except Exception:
            pass
        self.mon = None
        self._attach_fail_at = 0.0
        self._mon_err = ""
        mon_log("odlaczono (zatrzymany zapis)")
        self._gui_clear_lat()
        with self._rlock:
            self._rdata["mon"] = "czeka na proxy (auto)"

    def _mon_status_tick(self):
        cfg = self.mon_cfg()
        with self._rlock:
            self._rdata["cfg"] = cfg
            d = self._rdata.get("diag")
        txt = d or self._mon_hint()
        self.lb_mon_status.config(text=txt)
        self.root.after(600, self._mon_status_tick)

    def _mon_tick(self):
        m = self.mon
        if m is None:
            return
        try:
            m.set_gain(float(self.mon_gain.get()))
        except Exception:
            pass
        buf, blk, dev, tot = m.latency_breakdown()
        try:
            dname = sd.query_devices(m.dev)["name"]
        except Exception:
            dname = "?"
        conv = ("konwersja %d -> %d Hz" % (m.in_rate, m.out_rate)
                if m.out_rate and m.out_rate != m.in_rate
                else "1:1 bez konwersji")
        sub = ("  [podmienione API]" if m.subbed_dev is not None else "")
        self.lb_mon_lat.config(
            text="bufor (za pisarzem) %6.2f ms\n"
                  "blok wyjscia         %6.2f ms\n"
                  "urzadzenie wyjsciowe %6.2f ms\n"
                  "WE RAZEM                  %6.2f ms\n"
                  "we %d Hz -> wy %d Hz | %s%s\n"
                  "urzadzenie: %s\n"
                  "przerwy %d | przeskoki %d"
                  % (buf, blk, dev, tot, m.in_rate, m.out_rate,
                     conv, sub, dname, m.underruns, m.skips))
        self.root.after(400, self._mon_tick)

    # ---------- Sekcja 4: NAGRYWARKA (DAW) ----------
    def _build_recorder(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text=" Nagrywarka (DAW) ")
        pad = {"padx": 8, "pady": 4}

        self.lb_rec_status = ttk.Label(f, text="Sprawdzanie...", wraplength=500,
                                       justify="left")
        self.lb_rec_status.grid(row=0, column=0, columnspan=4, sticky="w", **pad)

        cfg = load_config(REC_CONFIG, {
            "folder": os.path.join(os.path.expanduser("~"), "Documents", "AXE IO ONE Nagrania"),
            "prefix": "daw_",
            "format": "WAV",
            "lag_ms": 10,
            "pair": "Out 1-2",
        })

        ttk.Label(f, text="Folder zapisu:").grid(row=1, column=0, sticky="w", **pad)
        self.rec_folder = ttk.Entry(f, width=46)
        self.rec_folder.insert(0, cfg["folder"])
        self.rec_folder.grid(row=2, column=0, columnspan=3, sticky="we", **pad)
        ttk.Button(f, text="Wybierz...",
                   command=self.rec_pick_folder).grid(row=2, column=3, **pad)

        self.rec_prefix = ttk.Entry(f, width=12)
        self.rec_prefix.insert(0, cfg["prefix"])
        self.rec_prefix.grid(row=3, column=0, sticky="w", **pad)
        ttk.Label(f, text="prefiks pliku").grid(row=3, column=1, sticky="w", **pad)

        self.rec_format = ttk.Combobox(f, state="readonly", width=6,
                                       values=["WAV", "MP3"])
        self.rec_format.set(cfg["format"])
        self.rec_format.grid(row=3, column=2, sticky="w", **pad)
        ttk.Label(f, text="format").grid(row=3, column=3, sticky="w", **pad)

        self.rec_pair = ttk.Combobox(f, state="readonly", width=10,
                                     values=["Out 1-2", "Out 3-4", "Out 5-6", "Out 7-8"])
        self.rec_pair.set(cfg["pair"])
        self.rec_pair.grid(row=4, column=0, sticky="w", **pad)
        ttk.Label(f, text="para wyjscia").grid(row=4, column=1, sticky="w", **pad)

        self.rec_lag = ttk.Spinbox(f, from_=0, to=250, width=6,
                                   text=str(cfg["lag_ms"]))
        self.rec_lag.grid(row=4, column=2, sticky="w", **pad)
        ttk.Label(f, text="ms opoznienia").grid(row=4, column=3, sticky="w", **pad)

        self.btn_rec = tk.Button(f, text="START", font=("Segoe UI", 11, "bold"),
                                 bg="#c62828", fg="white", activebackground="#8e0000",
                                 relief="raised", bd=4, command=self.rec_toggle)
        self.btn_rec.grid(row=5, column=0, columnspan=2, sticky="we", pady=(14, 4))
        ttk.Button(f, text="Otworz folder",
                   command=self.rec_open_folder).grid(row=5, column=2, sticky="we", **pad)
        ttk.Button(f, text="Sprawdz proxy",
                   command=self.check_recorder).grid(row=5, column=3, sticky="we", **pad)

        self.canvas_rec = tk.Canvas(f, height=18, bg="#111")
        self.canvas_rec.grid(row=6, column=0, columnspan=4, sticky="we", **pad)
        self.rec_bar = self.canvas_rec.create_rectangle(
            0, 0, 0, 18, fill="#2e7d32")

        self.lb_rec_info = ttk.Label(f, text="", wraplength=500, justify="left")
        self.lb_rec_info.grid(row=7, column=0, columnspan=4, sticky="w", **pad)

    def rec_pick_folder(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.rec_folder.get())
        if d:
            self.rec_folder.delete(0, "end")
            self.rec_folder.insert(0, d)

    def rec_open_folder(self):
        d = self.rec_folder.get()
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        subprocess.Popen(["explorer.exe", d], creationflags=NO_WINDOW)

    def check_recorder(self, *_):
        self._async(self._rec_compute, self._ui_rec_status)

    def _rec_compute(self):
        r = ShmReader()
        try:
            r.open()
        except Exception as e:
            return ("no", str(e))
        sr, nch, bfr, wp, act = r.header()
        r.close()
        return ("ok", sr, nch, bfr, wp, act)

    def _ui_rec_status(self, r):
        if isinstance(r, tuple) and r and r[0] == "__ERR__":
            self.lb_rec_status.config(
                text="Blad sprawdzenia pamieci: %s" % (r[1],))
            return
        if r[0] == "no":
            self.lb_rec_status.config(
                text="Proxy NIE jest aktywne.\n"
                     "Uruchom DAW / JAM VOX z przekierowaniem na proxy "
                     "(zakladka 'ASIO Capture'),\n"
                     "wtedy wspolna pamiec 'Local\\OBSDAWCapture_Shm' "
                     "pojawi sie tutaj.\n\n(%s)" % r[1])
            self.lb_rec_info.config(text="")
            return
        _, sr, nch, bfr, wp, act = r
        if not act:
            self.lb_rec_status.config(
                text="Proxy dziala, ale nic nie streamuje.\n"
                     "Sterownik ASIO jest zamknity - uruchom zrodlo w DAW/JAM VOX.\n\n"
                     "fs=%d Hz, kanalow=%d, bufor ASIO=%d klatek"
                     % (sr, nch, bfr))
        else:
            self.lb_rec_status.config(
                text="Proxy aktywne - mozesz nagrywac.\n\n"
                     "fs=%d Hz, kanalow=%d, bufor ASIO=%d klatek, "
                     "zapisano klatek=%d" % (sr, nch, bfr, wp))

    def rec_cfg(self):
        pair = self.rec_pair.get() or "Out 1-2"
        m = re.search(r"(\d+)-(\d+)", pair)
        ch_l = (int(m.group(1)) - 1) if m else 0
        ch_r = (int(m.group(2)) - 1) if m else 1
        try:
            lag = max(0, min(250, int(str(self.rec_lag.get()).strip() or 0)))
        except ValueError:
            lag = 10
        return {
            "folder": self.rec_folder.get().strip(),
            "prefix": self.rec_prefix.get().strip() or "daw_",
            "format": self.rec_format.get().strip().upper() or "WAV",
            "lag_ms": lag,
            "pair": pair,
            "ch_l": ch_l,
            "ch_r": ch_r,
        }

    def rec_toggle(self):
        if self.rec_thread is not None and self.rec_thread.is_alive():
            self.rec_stop()
        else:
            self.rec_start()

    def rec_start(self):
        cfg = self.rec_cfg()
        save_config(REC_CONFIG, cfg)
        folder = cfg["folder"]
        try:
            os.makedirs(folder, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Nagrywarka", "Nie mozna utworzyc folderu:\n%s" % e)
            return

        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        base = os.path.join(folder, cfg["prefix"] + stamp)
        self.rec_path_wav = base + ".wav"
        self.rec_path_out = self.rec_path_wav
        if cfg["format"] == "MP3":
            self.rec_path_out = base + ".mp3"
            if not find_ffmpeg():
                messagebox.showwarning(
                    "Nagrywarka",
                    "Nie znaleziono ffmpeg - zapisze WAV zamiast MP3.\n"
                    "Zainstaluj ffmpeg i dodaj go do PATH, aby dostac MP3.")
                cfg["format"] = "WAV"
                self.rec_path_out = self.rec_path_wav

        self.rec_thread = threading.Thread(target=self._rec_worker, args=(cfg,),
                                           daemon=True)
        self.rec_stop_flag.clear()
        self.rec_error = None
        self.rec_info = "start..."
        self.rec_level = 0.0
        self.btn_rec.config(text="STOP", bg="#37474f", activebackground="#263238")
        self.lb_rec_info.config(text="Nagrywanie do:\n%s" % self.rec_path_out)
        self.rec_thread.start()
        self.root.after(300, self._rec_tick)

    def rec_stop(self):
        self.rec_stop_flag.set()
        self.btn_rec.config(text="STOP...", state="disabled")
        self.lb_rec_info.config(text="Zatrzymywanie...")

    def _rec_tick(self):
        if self.rec_thread is None or not self.rec_thread.is_alive():
            self.btn_rec.config(text="START", bg="#c62828",
                                activebackground="#8e0000", state="normal")
            if self.rec_error:
                messagebox.showerror("Nagrywarka", self.rec_error)
                self.lb_rec_info.config(text="Blad: " + self.rec_error)
            else:
                p = self.rec_path_out
                if p and os.path.exists(p):
                    sz = os.path.getsize(p) / 1024.0 / 1024.0
                    self.lb_rec_info.config(
                        text="Zapisano:\n%s\n(%.2f MB)" % (p, sz))
            self.rec_thread = None
            return
        w = max(1, int(self.canvas_rec.winfo_width()))
        self.canvas_rec.coords(self.rec_bar, 0, 0,
                               int(w * max(0.0, min(1.0, self.rec_level))), 18)
        self.root.after(80, self._rec_tick)

    def _rec_worker(self, cfg):
        ch_l, ch_r = cfg["ch_l"], cfg["ch_r"]
        lag_ms = cfg["lag_ms"]
        wav_path = self.rec_path_wav
        out_path = self.rec_path_out

        r = ShmReader()
        wf = None
        try:
            r.open()
            sr, nch, bfr, write_pos, active = r.header()
            if sr <= 0:
                raise OSError("proxy nie podalo sampleRate")
            if active == 0:
                # poczekaj na start streamu, nie kasuj od razu
                for _ in range(100):
                    if self.rec_stop_flag.is_set():
                        return
                    time.sleep(0.1)
                    sr, nch, bfr, write_pos, active = r.header()
                    if active:
                        break
                if not active:
                    self.rec_error = ("Proxy jest zainstalowane, ale sterownik ASIO "
                                      "nie streamuje - uruchom zrodlo w DAW/JAM VOX.")
                    return

            ch_l = min(ch_l, max(0, nch - 1))
            ch_r = min(ch_r, max(0, nch - 1))

            wf = wave.open(wav_path, "wb")
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(sr)

            lag_frames = int(sr * lag_ms / 1000.0)
            read_pos = write_pos - lag_frames
            if read_pos < 0:
                read_pos = write_pos

            self.rec_info = "fs=%d Hz, lag=%d ms, kanal %d/%d" % (
                sr, lag_ms, ch_l + 1, ch_r + 1)

            idle = 0
            while not self.rec_stop_flag.is_set():
                sr, nch, bfr, write_pos, active = r.header()
                if not active:
                    idle += 1
                    if idle > 50:      # ~2.5 s bez dzwieku
                        self.rec_info = "cisza - proxy nie streamuje"
                    time.sleep(0.05)
                    continue
                idle = 0

                if sr <= 0:
                    time.sleep(0.05)
                    continue

                target = write_pos - lag_frames
                # resync identyczny jak w daw-source.cpp
                skip = target - read_pos
                if skip > REC_CHUNK * 4:
                    read_pos = target            # wyrzuc przeterminowane
                elif skip < -(REC_CHUNK * 4):
                    read_pos = write_pos         # czekaj na pisarza
                    time.sleep(0.005)
                    continue
                if read_pos < 0:
                    read_pos = write_pos - lag_frames

                peak = 0.0
                guard = 0
                while (read_pos + REC_CHUNK <= target and
                       guard < REC_CHUNK * 4 and
                       not self.rec_stop_flag.is_set()):
                    l, rr = r.chunk(read_pos, ch_l, ch_r)
                    wf.writeframes(interleave_s16(l, rr))
                    for v in (l[0], rr[0], l[REC_CHUNK - 1], rr[REC_CHUNK - 1]):
                        a = v if v >= 0.0 else -v
                        if a > peak:
                            peak = a
                    read_pos += REC_CHUNK
                    guard += 1
                self.rec_level = peak
                if guard == 0:
                    time.sleep(0.005)
        except Exception as e:
            self.rec_error = str(e)
        finally:
            try:
                if wf is not None:
                    wf.close()
            except Exception:
                pass
            r.close()

            # WAV gotowy -> opcjonalna konwersja do MP3
            if (self.rec_error is None and out_path != wav_path and
                    os.path.exists(wav_path)):
                exe = find_ffmpeg()
                if exe:
                    try:
                        subprocess.run(
                            [exe, "-y", "-i", wav_path, "-c:a", "libmp3lame",
                             "-b:a", "192k", out_path],
                            capture_output=True, timeout=600,
                            creationflags=NO_WINDOW)
                        if os.path.exists(out_path):
                            os.remove(wav_path)
                        else:
                            self.rec_error = ("ffmpeg nie utworzyl MP3 - "
                                              "plik WAV zostal w: " + wav_path)
                    except Exception as e:
                        self.rec_error = ("konwersja MP3 nie powiodla sie: %s\n"
                                          "WAV zostal w: %s" % (e, wav_path))
                else:
                    self.rec_error = ("brak ffmpeg - WAV zostal w: " + wav_path)

    def on_close(self):
        self.stop()
        if self.rec_thread is not None and self.rec_thread.is_alive():
            self.rec_stop_flag.set()
        self.root.destroy()


_single_mutex = None


def acquire_single_instance():
    """Tylko jedna instancja Managera.

    Dwie instancje otwieraja to samo urzadzenie wyjsciowe odsluchu -
    druga jest cicha albo wyrzuca bledami, a uzytkownik nie wie
    ktorej okna dotyczy problem.  Mutex od razu zwalnia sie po smierci
    procesu, wiec nie zostawia sladu po awarii.
    """
    global _single_mutex
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        h = k32.CreateMutexW(None, False, "Local\\AXE_IO_ONE_OBS_Manager")
        if not h:
            return True          # nie umiemy - nie blokujemy
        already = ctypes.get_last_error() == 183   # ERROR_ALREADY_EXISTS
        if already:
            k32.CloseHandle(h)
            return False
        _single_mutex = h         # trzymamy uchwyt do konca zycia
        return True
    except Exception:
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true", help="test bez GUI: status FlexASIO + listy urzadzen")
    ap.add_argument("--audiotest", type=str, default=None, metavar="URZ",
                    help="test bez GUI: DEV (numer) lub nazwa, np. Jabra")
    ap.add_argument("--rate", type=int, default=0, help="--audiotest: zegar")
    ap.add_argument("--lag", type=int, default=10, help="--audiotest: bufor ms")
    ap.add_argument("--blok", type=int, default=0, help="--audiotest: blok klatek")
    ap.add_argument("--dluznosc", type=float, default=8.0,
                    help="--audiotest: ile sekund")
    ap.add_argument("--latencja", choices=("low", "system"), default="low",
                    help="--audiotest: low = 22 ms, system = bufor Windows")
    ap.add_argument("--ton", type=float, default=0.0,
                    help="--audiotest: zamiast SHM pusc ton testowy (Hz)")
    ap.add_argument("--tytul", type=str, default="",
                    help="--audiotest: opis testu dopisywany do raportu")
    ap.add_argument("--raport", type=str, default="",
                    help="--audiotest: plik, do ktorego dopisac wynik")
    args = ap.parse_args()
    if args.audiotest is not None:
        idx = None
        devs = sd.query_devices()
        key = str(args.audiotest)
        # Kolejnosc host API ma znaczenie: WASAPI idzie prosto do
        # wybranego endpointu (Jabra = 22 ms), a MME do uzytkownika
        # systemowego (tu 104 ms i w calej aplikacji wygladalo to jak
        # "duzy lag").  Bez tego wybor po nazwie dawal zly device.
        prefer = ("Windows WASAPI", "Windows WDM")
        if key.isdigit():
            idx = int(key)
        else:
            hits = []
            for i, dv in enumerate(devs):
                if dv["max_output_channels"] > 0 and \
                        key.lower() in (dv["name"] or "").lower():
                    hits.append(i)
            idx = None
            for want in prefer:
                for i in hits:
                    if sd.query_hostapis(devs[i]["hostapi"])["name"] == want:
                        idx = i
                        break
                if idx is not None:
                    break
            if idx is None and hits:
                idx = hits[0]
        if idx is None or idx >= len(devs):
            print("nie znaleziono urzadzenia '%s'" % key)
            report(args, 2, "brak")
            return 2
        d = devs[idx]
        print("urzadzenie %d = %s | host=%s | domyslne=%s"
              % (idx, d["name"], sd.query_hostapis(d["hostapi"])["name"],
                 d["default_samplerate"]))
        r = ShmReader()
        try:
            r.open()
            print("shm = fs=%d ch=%d buf=%d writePos=%d active=%d" % r.header())
        except Exception as e:
            print("shm = NIEOTWARTA (%s)" % e)
        if args.ton > 0:
            return tone_test(idx, args)
        m = AsioMonitor(idx, 0, 1, gain_db=0.0, lag_ms=args.lag,
                        out_rate=args.rate)
        if args.blok:
            m.BLOCK = args.blok
        m.lat_mode = None if args.latencja == "system" else "low"
        t0 = time.time()
        try:
            m.start()
        except Exception as e:
            print("OTWARCIE NIEUDANE: %r" % (e,))
            report(args, 1, d["name"])
            return 1
        print("OTWARTE in=%d out=%d latencja=%.1fms blok=%d (%s) bufor=%dms"
              % (m.in_rate, m.out_rate, m.dev_latency * 1000, m.BLOCK,
                 args.latencja, args.lag))
        print("graj i sluchaj - test trwa %.0f s" % args.dluznosc)
        lags = []
        while time.time() - t0 < args.dluznosc:
            time.sleep(0.1)
            lags.append(m.shm_lag_ms)
        m.stop()
        if lags:
            print("bufor SHM: min %.1f  sredni %.1f  max %.1f ms"
                  % (min(lags), sum(lags) / len(lags), max(lags)))
        print("przerwy=%d  przeskoki=%d" % (m.underruns, m.skips))
        print("ZAMKNIETE OK")
        report(args, 0, d["name"],
               "bufor=%dms blok=%d latencja=%.1fms przerwy=%d przeskoki=%d"
               % (args.lag, m.BLOCK, m.dev_latency * 1000, m.underruns,
                  m.skips))
        return 0
    if args.selftest:
        reg, pads = flexasio_status()
        devs = flexasio_devices(pads)
        lines = ["sounddevice=%s" % sd.__version__,
                 "flexasio_registry=%s" % reg,
                 "pads=%s" % pads,
                 "devices=%d" % len(devs)]
        lines += ["  " + d for d in devs]
        # stan nagrywarki (sprawdza dzialanie ctypes w zamrożonym .exe)
        try:
            r = ShmReader()
            r.open()
            sr, nch, bfr, wp, act = r.header()
            lines.append("shm=OTWARTA fs=%d ch=%d buf=%d writePos=%d active=%d"
                         % (sr, nch, bfr, wp, act))
            r.close()
        except Exception as e:
            lines.append("shm=NIEOTWARTA (%s)" % e)
        lines.append("shm_name=%s" % SHM_NAME)
        lines.append("shm_bytes=%d" % (36 + RING_FRAMES * DAW_MAX_CHANNELS * 4))
        lines.append("ffmpeg=%s" % (find_ffmpeg() or "brak"))
        txt = "\n".join(lines)
        print(txt)
        try:
            with open("selftest.log", "w", encoding="utf-8") as f:
                f.write(txt + "\n")
        except Exception:
            pass
        return 0
    if not acquire_single_instance():
        mon_log("druga instancja - odrzucona (pid=%d)" % os.getpid())
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning(
                "Manager juz dziala",
                "AXE IO ONE Manager jest juz uruchomiony.\n\n"
                "Dwie instancje otwieraja to samo urzadzenie wyjsciowe "
                "odsluchu - druga jest cicha.\n"
                "Zamknij tamto okno i uruchom tylko to.")
            root.destroy()
        except Exception:
            pass
        return 0
    mon_log("start (pid=%d)" % os.getpid())
    root = tk.Tk()
    try:
        App(root)
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())