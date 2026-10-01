#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AXE I/O ONE -> OBS audio bridge (WASAPI -> wirtualny kabel)

Lapie audio z urzadzenia Wejscia (AXE I/O ONE) i odtwarza je do wirtualnego
kabla (VB-CABLE "CABLE Input"), ktory OBS laczy jako "CABLE Output".

Przyklady:
  python axeio_obs_bridge.py --list
  python axeio_obs_bridge.py
  python axeio_obs_bridge.py --in "axe io" --out "cable input" --gain -3 --vu
"""
import sys
import time
import argparse
import sounddevice as sd

DEFAULT_IN = "axe io"
DEFAULT_OUT = "cable input"
last_level = 0.0


def find_device(pattern, want_input):
    pat = pattern.casefold()
    best = None
    for i, dev in enumerate(sd.query_devices()):
        if pat not in dev["name"].casefold():
            continue
        nch = dev["max_input_channels"] if want_input else dev["max_output_channels"]
        if nch < 1:
            continue
        host = sd.query_hostapis()[dev["hostapi"]]["name"]
        score = (2 if "wasapi" in host.casefold() else 0) + nch
        if best is None or score > best[0]:
            best = (score, i, dev["name"])
    return None if best is None else best[1]


def list_devices():
    print("Urzadzenia audio (szukaj AXE I/O ONE i CABLE):")
    for i, dev in enumerate(sd.query_devices()):
        host = sd.query_hostapis()[dev["hostapi"]]["name"]
        print("  [%2d] %-55s %-24s in=%d out=%d" % (
            i, dev["name"], host, dev["max_input_channels"], dev["max_output_channels"]))


def vu_line(level):
    n = int(level * 40)
    n = max(0, min(40, n))
    return "#" * n + "-" * (40 - n)


def main():
    ap = argparse.ArgumentParser(description="AXE I/O ONE -> wirtualny kabel -> OBS")
    ap.add_argument("--list", action="store_true", help="wypisz urzadzenia i wyjdz")
    ap.add_argument("--in", dest="input", default=DEFAULT_IN, help="wzorzec nazwy wejscia (AXE I/O)")
    ap.add_argument("--out", dest="output", default=DEFAULT_OUT, help="wzorzec nazwy wyjscia (kabel wirtualny)")
    ap.add_argument("--fs", type=int, default=0, help="czestotliwosc probkowania (0 = auto)")
    ap.add_argument("--in-ch", dest="in_ch", type=int, default=0, help="kanaly wejscia (0 = auto)")
    ap.add_argument("--out-ch", dest="out_ch", type=int, default=2, help="kanaly wyjscia (2 = stereo)")
    ap.add_argument("--gain", type=float, default=0.0, help="wzmocnienie w dB, np. -6")
    ap.add_argument("--limit", type=float, default=1.0, help="ogranicznik wyjscia 0..1")
    ap.add_argument("--latency", type=float, default=0.1, help="bufor w sekundach")
    ap.add_argument("--vu", action="store_true", help="wskaznik poziomu (RMS)")
    args = ap.parse_args()

    if args.list:
        list_devices()
        return 0

    di = find_device(args.input, want_input=True)
    do = find_device(args.output, want_input=False)
    if di is None:
        print("BRAK wejscia pasujacego do '%s'. Uzyj --list." % args.input)
        return 2
    if do is None:
        print("BRAK wyjscia pasujacego do '%s'. Zainstaluj VB-CABLE, potem --list." % args.output)
        return 2
    in_dev = sd.query_devices(di)
    out_dev = sd.query_devices(do)
    print("Wejscie : [%d] %s  (ch=%d, fs=%g)" % (
        di, in_dev["name"], in_dev["max_input_channels"], in_dev["default_samplerate"]))
    print("Wyjscie : [%d] %s  (ch=%d, fs=%g)" % (
        do, out_dev["name"], out_dev["max_output_channels"], out_dev["default_samplerate"]))

    fs = args.fs or int(in_dev["default_samplerate"])
    in_ch = args.in_ch or in_dev["max_input_channels"]
    if in_ch < 1:
        in_ch = 1
    out_ch = max(1, args.out_ch)
    gain = 10 ** (args.gain / 20.0)
    limit = max(0.0, min(1.0, args.limit))
    block = max(1, int(fs * 0.05))
    global last_level
    last_level = 0.0

    def cb(indata, outdata, frames, time_info, status):
        global last_level
        if status:
            print("status:", status, flush=True)
        x = indata * gain
        ch_in = x.shape[1]
        if ch_in != out_ch:
            if ch_in > out_ch:
                x = x[:, :out_ch]
            else:
                cols = [0] * out_ch if ch_in == 1 else list(range(ch_in)) + [ch_in - 1] * (out_ch - ch_in)
                x = x[:, cols]
        if limit < 1.0:
            x = x * limit
        outdata[:] = x
        last_level = float((x * x).mean()) ** 0.5

    while True:
        try:
            with sd.Stream(samplerate=fs, blocksize=block, dtype="float32",
                           latency=(args.latency, args.latency),
                           device=(di, do),
                           channels=(in_ch, out_ch), callback=cb) as st:
                print("Dziala (Ctrl+C aby zakonczyc)...")
                t0 = time.time()
                while True:
                    time.sleep(0.2)
                    if args.vu and time.time() - t0 >= 0.25:
                        print("\r  [%s] %.1f%%   " % (vu_line(last_level), last_level * 100), end="", flush=True)
                        t0 = time.time()
        except KeyboardInterrupt:
            print("\nKoniec.")
            return 0
        except Exception as e:
            print("Blad strumienia:", e)
            print("Ponowne otwarcie za 2 s...")
            time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())