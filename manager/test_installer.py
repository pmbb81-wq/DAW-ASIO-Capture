"""Test artefaktow: EXE managera, instalator i zawartosc dystrybucji.

Weryfikuje, ze po przebudowie wszystko jest na miejscu i nowe, oraz ze
Manager gotowy do rozprowadzenia odpala sie bez crasha (bundle PyInstaller:
numpy/sounddevice/tkinter + wbudowane instalatory).  Nie wymaga UAC.
"""
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
MANAGER = os.path.join(ROOT, "dist", "AXE_IO_ONE_OBS_Manager.exe")
SETUP = os.path.join(ROOT, "dist", "AXE-IO-ONE-OBS-Audio-Capture-Setup.exe")
INSTALLERS = os.path.join(ROOT, "installers")
NOW = time.time()
DAY = 24 * 3600

fails = []


def check(label, ok, extra=""):
    print("  [%s] %s %s" % ("OK " if ok else "BLAD", label, extra))
    if not ok:
        fails.append(label + (" | " + extra if extra else ""))


print("  artefakty (EXE/instalator)")

for name, path, minmb in (("Manager", MANAGER, 20),
                          ("Instalator", SETUP, 3)):
    if not os.path.isfile(path):
        check(name + " istnieje", False)
        continue
    mb = os.path.getsize(path) / 1e6
    new = (NOW - os.path.getmtime(path)) < DAY
    check(name + " istnieje + dzisiejszy + rozmiar",
          new and mb >= minmb, "%.1f MB, %s" % (mb, "dzis" if new else "STARY"))

print("  instalatory wewnetrzne (bundle datas)")

for dll in ("asio-proxy.dll", "asio-proxy-x86.dll",
            "obs-daw-capture.dll", "FlexASIO-1.10b.exe"):
    p = os.path.join(INSTALLERS, dll)
    check("installers\\" + dll, os.path.isfile(p) and os.path.getsize(p) > 0)

print("  jarzmo uruchomieniowe EXE (smoke test)")

try:
    p = subprocess.Popen([MANAGER])
    time.sleep(8)
    alive = p.poll() is None
    check("Manager startuje bez crasha", alive)
    if alive:
        p.terminate()
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
except Exception as e:
    check("Manager startuje bez crasha", False, repr(e))

print()
if fails:
    print("  WYNIK: BLEDY:")
    for x in fails:
        print("    " + x)
    sys.exit(1)
print("  WYNIK: WSZYSTKO OK")