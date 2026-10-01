#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AXE I/O ONE - Nagrywarka (DAW)

Nagrywa dokladnie to, co DAW / JAM VOX wysyla przez ASIO, prosto ze wspolnej
pamieci proxy (Local\\OBSDAWCapture_Shm) - bez kabli wirtualnych i bez
domieszki monitoringu sprzetowego. Dziala rownoczesnie ze zrodlem OBS
(kazdy ma wlasna pozycje odczytu).

Wymaga aktywnego przekierowania ASIO na proxy - ustawia je instalator
"AXE IO ONE - OBS Audio Capture" (albo wczesniejsza wersja Managera).
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
import threading
import datetime
import subprocess
import wave
from ctypes import wintypes

import tkinter as tk
from tkinter import ttk, messagebox

REC_CONFIG = "recorder_config.json"

# ---------------------------------------------------------------------------
#  Layout wspolnej pamieci proxy - MUSI byc identyczny z
#  shared/shared-memory.hpp (DAW-ASIO-Capture):
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

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Formaty nagrywania. WAV zapisywany jest natywnie (bez ffmpeg); pozostale
# wymagaja ffmpeg, ktory jest wbudowany w .exe (katalog ffmpeg obok aplikacji).
FORMATS = {
    "WAV":  (".wav", None),
    "MP3":  (".mp3", ["-c:a", "libmp3lame", "-b:a", "192k"]),
    "FLAC": (".flac", ["-c:a", "flac", "-compression_level", "8"]),
    "OGG":  (".ogg", ["-c:a", "libvorbis", "-q:a", "5"]),
    "M4A":  (".m4a", ["-c:a", "aac", "-b:a", "192k"]),
}
FORMAT_NAMES = ["WAV", "MP3", "FLAC", "OGG", "M4A"]
DEFAULT_FORMAT = "WAV"


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


def find_ffmpeg():
    """Kolejnosc: ffmpeg wbudowany w .exe (_MEIPASS), potem obok .exe,
    potem PATH i typowe lokalizacje. Zwraca sciezke albo None."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = os.path.join(meipass, "ffmpeg", "ffmpeg.exe")
        if os.path.exists(p):
            return p
    ed = exe_dir()
    for p in (os.path.join(ed, "ffmpeg", "ffmpeg.exe"),
              os.path.join(ed, "ffmpeg.exe")):
        if os.path.exists(p):
            return p
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


class RecorderApp:
    def __init__(self, root):
        self.root = root

        self.rec_thread = None
        self.rec_stop_flag = threading.Event()
        self.rec_error = None
        self.rec_info = ""
        self.rec_level = 0.0
        self.rec_path_wav = None
        self.rec_path_out = None
        self._gui_q = queue.Queue()

        root.title("AXE I/O ONE - Nagrywarka (DAW)")
        root.geometry("560x460")
        root.resizable(False, False)

        self._build_ui()

        self.root.after(40, self._pump_gui)
        self.check_recorder()
        self.root.after(80, self._rec_tick)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self):
        f = ttk.Frame(self.root)
        f.pack(fill="both", expand=True, padx=12, pady=10)
        pad = {"padx": 8, "pady": 4}

        ttk.Label(f, text="Nagrywarka DAW - zapis strumienia ASIO (proxy)",
                  font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", **pad)

        self.lb_rec_status = ttk.Label(f, text="Sprawdzanie...", wraplength=520,
                                       justify="left")
        self.lb_rec_status.grid(row=1, column=0, columnspan=4, sticky="w", **pad)

        cfg = load_config(REC_CONFIG, {
            "folder": os.path.join(os.path.expanduser("~"), "Documents", "AXE IO ONE Nagrania"),
            "prefix": "daw_",
            "format": DEFAULT_FORMAT,
            "lag_ms": 10,
            "pair": "Out 1-2",
        })

        ttk.Label(f, text="Folder zapisu:").grid(row=2, column=0, sticky="w", **pad)
        self.rec_folder = ttk.Entry(f, width=46)
        self.rec_folder.insert(0, cfg["folder"])
        self.rec_folder.grid(row=3, column=0, columnspan=3, sticky="we", **pad)
        ttk.Button(f, text="Wybierz...",
                   command=self.rec_pick_folder).grid(row=3, column=3, **pad)

        self.rec_prefix = ttk.Entry(f, width=12)
        self.rec_prefix.insert(0, cfg["prefix"])
        self.rec_prefix.grid(row=4, column=0, sticky="w", **pad)
        ttk.Label(f, text="prefiks pliku").grid(row=4, column=1, sticky="w", **pad)

        self.rec_format = ttk.Combobox(f, state="readonly", width=6,
                                       values=FORMAT_NAMES)
        self.rec_format.set(cfg["format"] if cfg["format"] in FORMAT_NAMES
                            else DEFAULT_FORMAT)
        self.rec_format.grid(row=4, column=2, sticky="w", **pad)
        ttk.Label(f, text="format").grid(row=4, column=3, sticky="w", **pad)

        self.rec_pair = ttk.Combobox(f, state="readonly", width=10,
                                     values=["Out 1-2", "Out 3-4", "Out 5-6", "Out 7-8"])
        self.rec_pair.set(cfg["pair"])
        self.rec_pair.grid(row=5, column=0, sticky="w", **pad)
        ttk.Label(f, text="para wyjscia").grid(row=5, column=1, sticky="w", **pad)

        self.rec_lag = ttk.Spinbox(f, from_=0, to=250, width=6,
                                   text=str(cfg["lag_ms"]))
        self.rec_lag.grid(row=5, column=2, sticky="w", **pad)
        ttk.Label(f, text="ms opoznienia").grid(row=5, column=3, sticky="w", **pad)

        self.btn_rec = tk.Button(f, text="START", font=("Segoe UI", 11, "bold"),
                                 bg="#c62828", fg="white", activebackground="#8e0000",
                                 relief="raised", bd=4, command=self.rec_toggle)
        self.btn_rec.grid(row=6, column=0, columnspan=2, sticky="we", pady=(14, 4))
        ttk.Button(f, text="Otworz folder",
                   command=self.rec_open_folder).grid(row=6, column=2, sticky="we", **pad)
        ttk.Button(f, text="Sprawdz proxy",
                   command=self.check_recorder).grid(row=6, column=3, sticky="we", **pad)

        self.canvas_rec = tk.Canvas(f, height=18, bg="#111")
        self.canvas_rec.grid(row=7, column=0, columnspan=4, sticky="we", **pad)
        self.rec_bar = self.canvas_rec.create_rectangle(
            0, 0, 0, 18, fill="#2e7d32")

        self.lb_rec_info = ttk.Label(f, text="", wraplength=520, justify="left")
        self.lb_rec_info.grid(row=8, column=0, columnspan=4, sticky="w", **pad)

        ff = find_ffmpeg()
        ttk.Label(
            f,
            text=("MP3 / FLAC / OGG / M4A: ffmpeg wlaczony"
                  if ff else "Uwaga: brak ffmpeg - tylko WAV"),
            foreground=("#2e7d32" if ff else "#c62828"),
        ).grid(row=9, column=0, columnspan=4, sticky="w", **pad)

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
                     "(ustawionym przez instalator),\n"
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
        fmt = self.rec_format.get().strip().upper()
        return {
            "folder": self.rec_folder.get().strip(),
            "prefix": self.rec_prefix.get().strip() or "daw_",
            "format": fmt if fmt in FORMATS else DEFAULT_FORMAT,
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
        fmt = cfg["format"]
        if FORMATS[fmt][1] is not None:
            if find_ffmpeg():
                self.rec_path_out = base + FORMATS[fmt][0]
            else:
                messagebox.showwarning(
                    "Nagrywarka",
                    "Nie znaleziono ffmpeg - zapisze WAV zamiast %s.\n"
                    "ffmpeg jest dolaczony do aplikacji; jesli uruchamiasz ja "
                    "ze zrodla, dodaj ffmpeg do PATH." % fmt)
                cfg["format"] = DEFAULT_FORMAT

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

            # WAV gotowy -> konwersja do wybranego formatu
            if (self.rec_error is None and out_path != wav_path and
                    os.path.exists(wav_path)):
                ext = os.path.splitext(out_path)[1].upper().lstrip(".")
                args = FORMATS.get(ext, (None, None))[1]
                exe = find_ffmpeg()
                if exe and args:
                    try:
                        subprocess.run(
                            [exe, "-hide_banner", "-loglevel", "error", "-y",
                             "-i", wav_path] + args + [out_path],
                            capture_output=True, timeout=600,
                            creationflags=NO_WINDOW)
                        if os.path.exists(out_path):
                            os.remove(wav_path)
                        else:
                            self.rec_error = ("ffmpeg nie utworzyl pliku %s - "
                                              "WAV zostal w: %s" % (ext, wav_path))
                    except Exception as e:
                        self.rec_error = ("konwersja do %s nie powiodla sie: %s\n"
                                          "WAV zostal w: %s" % (ext, e, wav_path))
                else:
                    self.rec_error = ("brak ffmpeg - WAV zostal w: " + wav_path)

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

    def on_close(self):
        if self.rec_thread is not None and self.rec_thread.is_alive():
            self.rec_stop_flag.set()
        self.root.destroy()


_single_mutex = None


def acquire_single_instance():
    """Tylko jedna instancja aplikacji."""
    global _single_mutex
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW.restype = wintypes.HANDLE
        h = k32.CreateMutexW(None, False, "Local\\AXE_IO_ONE_DAW_Recorder")
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
    args = sys.argv[1:]
    if "--selftest" in args:
        lines = []
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
        try:
            root = tk.Tk()
            root.withdraw()
            messagebox.showwarning(
                "Nagrywarka juz dziala",
                "AXE I/O ONE Nagrywarka (DAW) jest juz uruchomiona.\n\n"
                "Zamknij tamto okno i uruchom tylko to.")
            root.destroy()
        except Exception:
            pass
        return 0

    root = tk.Tk()
    try:
        RecorderApp(root)
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
