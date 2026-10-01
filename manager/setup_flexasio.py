#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Setup FlexASIO -> 'AXE I/O ONE' jako wirtualny sterownik ASIO.

FlexASIO to zarejestrowany user-mode sterownik ASIO. Ten skrypt generuje
plik %USERPROFILE%\\FlexASio.toml, ktory sprawia, ze DAW-y widza urzadzenie
"FlexASIO" z wejsciem rownym temu co routuje nasz bridge (CABLE Output)
albo bezposrednio z AXE I/O ONE.

Uzycie:
  python setup_flexasio.py --dry-run
  python setup_flexasio.py                            # CABLE Output -> glosniki
  python setup_flexasio.py --input-hint "axe io one"  # prosta z ONE (bez bridge'a)
  python setup_flexasio.py --output-hint ""           # bez wyjscia (monitor w DAW)
"""
import os
import re
import sys
import json
import argparse
import subprocess

PROGFILES = [os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")]


def find_pad():
    for pf in PROGFILES:
        d = os.path.join(pf, "FlexASIO")
        if os.path.isdir(d):
            exe = os.path.join(d, "x64", "PortAudioDevices.exe")
            if os.path.exists(exe):
                return exe
    return None


def list_devices(pad):
    try:
        r = subprocess.run([pad], capture_output=True, timeout=60)
        out = (r.stdout or b"").decode("utf-8", "replace") + "\n" + (r.stderr or b"").decode("utf-8", "replace")
    except Exception as e:
        print("Blad PortAudioDevices:", e)
        return []
    devs = []
    for line in out.splitlines():
        m = re.search(r'WASAPI:\d+\| name\[(.*)\]', line)
        if m:
            name = m.group(1)
            if name.endswith("[Loopback]"):
                continue
            devs.append(name)
    return devs


def pick(devs, hint):
    for d in devs:
        if hint and hint.casefold() in d.casefold():
            return d
    return None


def main():
    ap = argparse.ArgumentParser(description="Generuje FlexASIO.toml")
    ap.add_argument("--dry-run", action="store_true", help="tylko pokaz co by wpisal")
    ap.add_argument("--profile", choices=["obs", "jamvox"], default=None,
                    help="gotowy szablon: 'obs' (wejscie=kabel, wyjscie=glosniki) | 'jamvox' (wejscie=ONE, wyjscie=kabel)")
    ap.add_argument("--input-hint", default=None,
                    help="po czym szukac wejscia (np. 'axe io one', 'CABLE Output')")
    ap.add_argument("--output-hint", default=None,
                    help="po czym szukac wyjscia; '' = wyjscie wylaczone")
    ap.add_argument("--channels", type=int, default=2)
    ap.add_argument("--buffer-ms", type=int, default=10, help="bufor ASIO w ms")
    ap.add_argument("--config", default=os.path.join(os.environ["USERPROFILE"], "FlexASIO.toml"))
    args = ap.parse_args()

    if args.profile == "jamvox":
        if args.input_hint is None:
            args.input_hint = "axe io one"
        if args.output_hint is None:
            args.output_hint = "CABLE Input"
    elif args.profile == "obs":
        if args.input_hint is None:
            args.input_hint = "CABLE Output"
        if args.output_hint is None:
            args.output_hint = "Słuchawki"
    if args.input_hint is None:
        args.input_hint = "CABLE Output"
    if args.output_hint is None:
        args.output_hint = "Słuchawki"

    pad = find_pad()
    if pad is None:
        print("Nie znaleziono FlexASIO w", PROGFILES)
        return 2
    devs = list_devices(pad)
    print("Dostepne urzadzenia WASAPI:")
    for d in devs:
        print("   -", d)

    inp = pick(devs, args.input_hint)
    outp = pick(devs, args.output_hint) if args.output_hint else None

    if inp is None:
        print("\nBrak urzadzenia pasujacego do wejscia '%s'." % args.input_hint)
        return 1
    print("\nWejscie ASIO  :", inp)
    print("Wyjscie ASIO  :", outp or "wylaczone")

    sr = 48000
    buf = max(32, int(sr * args.buffer_ms / 1000))
    lines = [
        '# Wygenerowane przez setup_flexasio.py (openCode)',
        'backend = "Windows WASAPI"',
        'bufferSizeSamples = %d' % buf,
        '',
        '[input]',
        'device = %s' % json.dumps(inp, ensure_ascii=False),
        'channels = %d' % args.channels,
    ]
    if outp:
        lines += ['', '[output]',
                  'device = %s' % json.dumps(outp, ensure_ascii=False),
                  'channels = %d' % args.channels]
    text = "\n".join(lines) + "\n"

    if args.dry_run:
        print("\n=== FLEXASIO.TOML (dry-run) ===")
        print(text)
        return 0

    try:
        with open(args.config, "w", encoding="utf-8") as f:
            f.write(text)
        print("\nZapisano:", args.config)
    except Exception as e:
        print("\nBLAD zapisu:", e)
        return 1

    tester = os.path.join(os.path.dirname(pad), "FlexASIOTest.exe")
    if os.path.exists(tester):
        print("Test sterownika FlexASIO (okolo 5 s)...")
        try:
            r = subprocess.run([tester], capture_output=True, text=True, timeout=90)
            tail = (r.stdout or "") if len(r.stdout or "") < 3000 else (r.stdout or "")[-3000:]
            print(tail[:2000])
        except Exception as e:
            print("Test nie uruchomil sie:", e)
    print("Gotowe. W DAW wybierz sterownik ASIO: FlexASIO")
    return 0


if __name__ == "__main__":
    sys.exit(main())