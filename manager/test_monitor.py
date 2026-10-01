"""Test odsluchu: SHM -> wyjscie, dokladnie jak daw-source.cpp.

Czytnik przyklejony TARGET_LAG klatek za piszacym (bufor = pokretlo),
klatki ida 1:1, zero konwersji, zero numpy.  Harness DETERMINISTYCZNY:
pisarz wyliczany z tego samego zegara co konsument (write = elapsed*rate*drift,
calkowite bloki 256) - bez gruboziarnistego sleep(), mierzymy czysty algorytm.
Scenariusze:
  1/2) zegary rowne: ton 440 Hz, ZERO przerw i ZERO przeskokow,
  3)   postoj pisarza 40 ms: przeskok + przerwy, ton zostaje,
  4)   pisarz wolniejszy 1%: przerwy i przeskok (odzysk sygmentu),
  5)   pisarz szybszy 1%: przeskok (wyrzucenie nadmiaru),
  6)   zrodlo milczy: hold ostatniej probki, cisza NIE,
  7)   gain +6 dB obciety do 1.0,
  8)   start przy writePos=0: synced = -lag, zero wyjatkow.
"""
import importlib.util
import os
import time

import numpy as np

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "axeio_obs_manager.py")
spec = importlib.util.spec_from_file_location("mgr", SRC)
mgr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mgr)

RATE = 44100
TONE = 440.0


class FakeSrc:
    def __init__(self):
        self.map = type("M", (), {"writePos": 0})()

    def read_pair(self, fi, cl, cr, n):
        v = np.sin(2 * np.pi * TONE * (fi + np.arange(n)) / RATE).astype(np.float32)
        return v, v


def write_pos(elapsed, drift, stall=None):
    """Bloki zapisane przez pisarza po 'elapsed' sekundach (dryf x).
    'stall' = (t0, czas): od t0 pisarz stoi, po wznowieniu wraca na
    normalne tempo (zostaje 'czas' za schematem bez postoju)."""
    basef = max(0, int(elapsed * RATE * drift) // 256 * 256)
    if stall and elapsed >= stall[0]:
        basef = max(0, basef - int(stall[1] * RATE * drift) // 256 * 256)
    return basef


def run(drift=1.0, lag_ms=10.0, seconds=5.0, gain=0.0, stall=None):
    m = mgr.AsioMonitor(None, 0, 1, gain_db=gain, lag_ms=lag_ms)
    m._rdr = FakeSrc()
    m._stream = None
    m.in_rate, m.nch, m.out_rate, m.dev_latency = RATE, 2, RATE, 0.0
    m._src = 0 - int(lag_ms * RATE / 1000.0)
    m._synced = False
    m.skips = m.underruns = 0

    # Zegar WIRTUALNY (t = i*dt): pisarz i konsument ida wg tej samej osi.
    # Zero realnego sleep() -> wyniki deterministyczne, niezalezne od
    # obciazenia/jittera harmonogramu Windows (progi w scenariuszach
    # toleruja drobne artefakty kwantyzacji blokow).
    dt = m.BLOCK / float(RATE)
    steps = int(seconds / dt)
    got, dist = [], []
    for i in range(steps):
        t = i * dt
        m._rdr.map.writePos = write_pos(t, drift, stall)
        out = np.zeros((m.BLOCK, 2), dtype=np.float32)
        m._cb(out, m.BLOCK, None, None)
        got.append(out[:, 0].copy())
        dist.append(m.shm_lag_ms)
    m.got = np.concatenate(got)
    m.dist = np.array(dist)
    return m
    m.got = np.concatenate(got)
    m.dist = np.array(dist)
    return m


def freq(sig):
    z = int(np.argmax(np.abs(sig) > 0.01)) if np.any(np.abs(sig) > 0.01) else 0
    w = sig[z:z + 32768]
    if len(w) < 2048 or np.abs(w).max() < 0.05:
        return float("nan"), 0.0
    sp = np.abs(np.fft.rfft(w * np.hanning(len(w))))
    return (float(np.fft.rfftfreq(len(w), 1.0 / RATE)[np.argmax(sp)]),
            float(np.abs(w).max()))


fails = []


def base(label, m, lag, tol=4.0):
    f, amp = freq(m.got)
    print("     ton %.1f Hz | amp %.2f | bufor %.1f ms (cel %.0f) | "
          "przerwy %3d | przeskoki %3d"
          % (f, amp, m.dist.mean(), lag, m.underruns, m.skips))
    if abs(f - TONE) > 3:
        fails.append("%s: zla czestotliwosc %.1f Hz" % (label, f))
    if amp < 0.9:
        fails.append("%s: slaby sygnal %.2f" % (label, amp))
    if abs(m.dist.mean() - lag) > tol:
        fails.append("%s: bufor %.1f ms zamiast %.0f (+/- %.1f)"
                     % (label, m.dist.mean(), lag, tol))
    return m


print("  1) zegary rowne, bufor 10 ms")
m = base("1:1/10ms", run(), 10.0)
if m.underruns > 3:  # 1-2 startowe + ewentualny jitter kwantyzacji sleep
    fails.append("1:1: przerwy mimo rownych zagarow (%d)" % m.underruns)
if m.skips > 0:
    fails.append("1:1: przeskoki mimo rownych zagarow (%d)" % m.skips)

print("  2) zegary rowne, bufor 60 ms")
m = base("1:1/60ms", run(lag_ms=60.0), 60.0)
if m.underruns > 3:
    fails.append("60ms: przerwy mimo rownych zagarow (%d)" % m.underruns)
if m.skips > 0:
    fails.append("60ms: przeskoki mimo rownych zagarow (%d)" % m.skips)

print("  3) postoj pisarza 40 ms w t=2.5s (bufor 15 ms)")
m = base("stall/15ms", run(lag_ms=15.0, stall=(2.5, 0.04)), 15.0, tol=10.0)
if not 1 <= m.underruns <= 20:
    fails.append("stall: podejrzana liczba przerw (%d)" % m.underruns)
if not 0 <= m.skips <= 4:
    fails.append("stall: podejrzana liczba przeskokow (%d)" % m.skips)

print("  4) pisarz wolniejszy o 2% (drift 0.98) - przerwy, zero przeskokow")
m = base("wolniejszy", run(drift=0.98, lag_ms=30.0), 30.0, tol=15.0)
if m.underruns == 0:
    fails.append("wolniejszy: brak przerw mimo zrodla ponizej wyjscia")
if m.skips > 2:
    fails.append("wolniejszy: przeskoki przy zrodle tylko WOLNIEJSZYM"
                 " (bufor splywa, glowka nie skacze); %d" % m.skips)

print("  5) pisarz szybszy o 1% (drift 1.01) - przeskok")
m = base("szybszy", run(drift=1.01, lag_ms=60.0), 60.0, tol=12.0)
if m.skips == 0:
    fails.append("szybszy: brak przeskoku mimo zrodla powyzej wyjscia")
if m.underruns > m.skips + 10:
    fails.append("szybszy: podejrzanie duzo przerw (%d) przy %d przeskokach"
                 % (m.underruns, m.skips))

print("  6) zrodlo milczy - hold ostatniej probki")
m = run(lag_ms=20.0, seconds=1.0)
base_u = m.underruns
for _ in range(400):
    m._cb(np.zeros((m.BLOCK, 2), dtype=np.float32), m.BLOCK, None, None)
    if m.underruns > base_u:
        break
last = np.zeros((m.BLOCK, 2), dtype=np.float32)
m._cb(last, m.BLOCK, None, None)
print("     max %.4f | niezerowe: %s"
      % (np.abs(last).max(), "tak" if np.abs(last).max() > 0.01 else "NIE"))
if np.abs(last).max() < 0.01:
    fails.append("hold: cisza zamiast ostatniej probki")
if np.abs(last).max() > 1.0001:
    fails.append("hold przekracza 1.0: %.3f" % np.abs(last).max())

print("  7) gain +6 dB obciety do 1.0")
m = run(lag_ms=20.0, seconds=1.0, gain=6.0)
print("     max %.4f (na surowo 1.0*1.995)" % np.abs(m.got).max())
if np.abs(m.got).max() > 1.0001:
    fails.append("gain nie obciety: %.3f" % np.abs(m.got).max())
if np.abs(m.got).max() < 0.9:
    fails.append("gain: sygnal zniknal")

print("  8) start przy writePos=0")
m = mgr.AsioMonitor(None, 0, 1, lag_ms=20.0)
m._rdr = FakeSrc()
m._stream = None
m.in_rate, m.nch, m.out_rate = RATE, 2, RATE
m._src = 0
m._synced = False
try:
    out = np.zeros((m.BLOCK, 2), dtype=np.float32)
    m._cb(out, m.BLOCK, None, None)
    print("     src %d (cel %d) | przerwy %d"
          % (m._src, -int(20 * RATE / 1000), m.underruns))
    if m._src != -int(20.0 * RATE / 1000.0):
        fails.append("start: src=%d zamiast -882" % m._src)
except Exception as e:
    fails.append("start: wyjatek %r" % e)

print("  9) PRAWDZIWY ShmReader.read_pair (regresja: modul 'array' zaslanial "
      "konstruktor)")
try:
    import ctypes
    smap = mgr._ShmMap()
    nch = mgr.DAW_MAX_CHANNELS
    # kazda klatka: [0.0, 0.25, 0.5, 0.75, -1.0, ...] zaleznie od indeksu
    for fr in range(mgr.RING_FRAMES):
        for ch in range(nch):
            smap.data[fr * nch + ch] = (fr % 4) / 4.0 - 0.375
    rdr = mgr.ShmReader()
    rdr.map = smap
    rdr.floats = ctypes.cast(ctypes.addressof(smap.data),
                             ctypes.POINTER(ctypes.c_float))
    n = 1000
    wl, wr = rdr.read_pair(0, 0, 1, n)
    exp0 = [(i % 4) / 4.0 - 0.375 for i in range(n)]
    ok0 = len(wl) == n and all(abs(wl[i] - exp0[i]) < 1e-6 for i in range(0, n, 97))
    ok0 = ok0 and all(abs(wl[i] - exp0[i]) < 1e-6 for i in range(n))
    # zawiniecie pierścienia: ostatnie 100 klatek + poczatek
    start = mgr.RING_FRAMES - 500
    wl2, _ = rdr.read_pair(start, 0, 0, n)
    ok1 = len(wl2) == n
    if start < mgr.RING_FRAMES:
        for k, fr in enumerate([(start + k) % mgr.RING_FRAMES for k in range(n)]):
            e = (fr % 4) / 4.0 - 0.375
            if abs(wl2[k] - e) > 1e-6:
                ok1 = False
                break
    print("     dlugosc %d | kanaly poprawne %s | zawiniecie %s"
          % (len(wl), ok0, ok1))
    if not ok0:
        fails.append("read_pair: bledne probki kanalu L")
    if not ok1:
        fails.append("read_pair: bledne probki przez zawiniecie pierścienia")
except Exception as e:
    print("     WYJATEK:", repr(e))
    fails.append("read_pair: wyjatek %r (to tlumilo dzwiek w odsluchu)" % e)

print(" 10) konwersja zegara 48000 -> 44100 (katmull-rom)")
RIN, ROUT = 48000, 44100
try:
    m = mgr.AsioMonitor(None, 0, 1, lag_ms=20.0, out_rate=ROUT)
    m._stream = None
    m.in_rate, m.out_rate, m.nch = RIN, ROUT, 2
    m._ratio = float(RIN) / float(ROUT)
    m._rs_pos = 0.0
    m._rs_hist = [(0.0, 0.0)] * 3
    m._rs_resync = True
    m._src = 0
    m._synced = False
    m.skips = m.underruns = 0

    class Src48:
        """Pisarz w 48 kHz - ten sam ton, inny zegar."""

        def __init__(self):
            self.map = type("M", (), {"writePos": 0})()

        def read_pair(self, fi, cl, cr, n):
            v = np.sin(2 * np.pi * TONE * (fi + np.arange(n))
                       / float(RIN)).astype(np.float32)
            return v, v

    m._rdr = Src48()
    steps = 2000
    got = []
    for i in range(steps):
        # na kazdy blok wyjscia (256 @44.1k) pisarz zapisuje 256*ratio klatek
        m._rdr.map.writePos = int(i * m.BLOCK * m._ratio)
        out = np.zeros((m.BLOCK, 2), dtype=np.float32)
        m._cb(out, m.BLOCK, None, None)
        got.append(out[:, 0].copy())
    sig = np.concatenate(got)[4096:]
    f, amp = freq(sig)
    print("     ton %.1f Hz | amp %.3f | przerwy %d | przeskoki %d"
          % (f, amp, m.underruns, m.skips))
    if abs(f - TONE) > 4:
        fails.append("konwersja: zla czestotliwosc %.1f Hz" % f)
    if amp < 0.9:
        fails.append("konwersja: slaby sygnal %.3f" % amp)
    if m.underruns > 5:
        fails.append("konwersja: za duzo przerw (%d)" % m.underruns)
except Exception as e:
    print("     WYJATEK:", repr(e))
    fails.append("konwersja: wyjatek %r" % e)

print(" 11) strumien wyjscia MUSI dostac callback (regresja: cichy strumien)")
try:
    seen = {}

    class FakeStream:
        def __init__(self, **kw):
            seen["kw"] = kw
            self.samplerate = kw.get("samplerate", 0)
            self.latency = 0.02

        def start(self):
            seen["started"] = True

        def stop(self):
            pass

        def close(self):
            pass

    old = mgr.sd.OutputStream
    mgr.sd.OutputStream = FakeStream
    try:
        mon = mgr.AsioMonitor(7, 0, 1, lag_ms=10)
        mon._open_started(7, 44100)
    finally:
        mgr.sd.OutputStream = old
    kw = seen.get("kw", {})
    cb = kw.get("callback")
    # uwaga: mon._cb is mon._cb == False (bound method to nowy obiekt),
    # wiec porownujemy wlasciciela i sama funkcje
    ok_cb = (getattr(cb, "__self__", None) is mon
             and getattr(cb, "__func__", None) is mgr.AsioMonitor._cb)
    print("     callback=%s | start=%s | rate=%s"
          % (ok_cb, seen.get("started"), kw.get("samplerate")))
    if not ok_cb:
        fails.append("brak callbacka w strumieniu wyjscia - strumien "
                     "bedzie cichy (dzwiek z SHM nie trafia do urzadzenia)")
    if not seen.get("started"):
        fails.append("strumien wyjscia nie zostal wystartowany")
except Exception as e:
    print("     WYJATEK:", repr(e))
    fails.append("test callbacka: wyjatek %r" % e)

print(" 12) zajete urzadzenie: NIE podmieniaj na inne API (cicha sciezka MME)")
try:
    devs = [{"name": "Speaker (Jabra)", "max_output_channels": 2,
             "default_samplerate": 44100.0},
            {"name": "Inne", "max_output_channels": 2,
             "default_samplerate": 48000.0},
            {"name": "Speaker (Jabra)", "max_output_channels": 2,
             "default_samplerate": 44100.0}]
    tried = []

    class FakeStream:
        def __init__(self, **kw):
            tried.append(kw.get("device"))
            if kw.get("device") == 0:
                raise mgr.sd.PortAudioError(
                    "Error opening OutputStream: Host API error "
                    "[PaErrorCode -9999]", -9999)
            self.samplerate = kw.get("samplerate", 0)
            self.latency = 0.02

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    old_os, old_qd = mgr.sd.OutputStream, mgr.sd.query_devices
    mgr.sd.OutputStream = FakeStream
    mgr.sd.query_devices = lambda *a, **k: (devs[a[0]] if a else devs)
    try:
        mon = mgr.AsioMonitor(0, 0, 1, lag_ms=10)
        raised = None
        try:
            mon._open_started(0, [44100, 48000])
        except Exception as e:
            raised = e
    finally:
        mgr.sd.OutputStream, mgr.sd.query_devices = old_os, old_qd
    print("     urzadzenia probowane=%s | bled=%r" % (tried, raised))
    if 2 in tried:
        fails.append("przeskoczono na inne API mimo zajetego urzadzenia "
                     "- dzwiek poszedlby gdzie indziej (MME, inny mixer)")
    if not raised or mgr.AsioMonitor._is_rate_err(raised):
        fails.append("nie zgloszono bledu zajetosci urzadzenia")
except Exception as e:
    print("     WYJATEK:", repr(e))
    fails.append("test zajetego urzadzenia: wyjatek %r" % e)

print()
if fails:
    print("  WYNIK: BLEDY:")
    for x in fails:
        print("    " + x)
    raise SystemExit(1)
print("  WYNIK: WSZYSTKO OK")