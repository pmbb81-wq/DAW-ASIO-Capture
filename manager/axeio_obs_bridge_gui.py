#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AXE I/O ONE -> OBS Bridge (GUI)
Przechwyciuje dzwiek z wejscia (AXE I/O ONE) przez WASAPI i odtwarza do
wirtualnego kabla (VB-CABLE), ktory OBS nagrywa jako osobna sciezke.
"""
import sys
import time
import argparse
import tkinter as tk
from tkinter import ttk

import sounddevice as sd

TITLE = "AXE I/O ONE -> OBS Bridge"


def find_devices(want_input):
    out = []
    try:
        for i, dev in enumerate(sd.query_devices()):
            nch = dev["max_input_channels"] if want_input else dev["max_output_channels"]
            if nch < 1:
                continue
            host = sd.query_hostapis()[dev["hostapi"]]["name"]
            tag = "WASAPI" if "wasapi" in host.casefold() else host
            out.append("%02d  %s  [%s]  (%d ch)" % (i, dev["name"], tag, nch))
    except Exception:
        pass
    return out


class App:
    def __init__(self, root):
        self.root = root
        self.stream = None
        self.last_level = 0.0
        self.running = False

        root.title(TITLE)
        root.geometry("460x420")
        root.minsize(420, 380)
        root.resizable(False, False)

        pad = {"padx": 10, "pady": 6}
        frm = ttk.Frame(root)
        frm.pack(fill="both", expand=True, padx=12, pady=12)

        ttk.Label(frm, text="Wejscie (AXE I/O ONE):").grid(row=0, column=0, sticky="w", **pad)
        self.cb_in = ttk.Combobox(frm, state="readonly", width=42)
        self.cb_in.grid(row=1, column=0, columnspan=2, sticky="we", **pad)

        ttk.Label(frm, text="Wyjscie (wirtualny kabel):").grid(row=2, column=0, sticky="w", **pad)
        self.cb_out = ttk.Combobox(frm, state="readonly", width=42)
        self.cb_out.grid(row=3, column=0, columnspan=2, sticky="we", **pad)

        self.btn_refresh = ttk.Button(frm, text="Odswiez urzadzenia", command=self.refresh)
        self.btn_refresh.grid(row=3, column=2, padx=6, pady=6)

        ttk.Label(frm, text="Wzmocnienie (dB):").grid(row=4, column=0, sticky="w", **pad)
        self.gain = tk.DoubleVar(value=0.0)
        self.lb_gain = ttk.Label(frm, text="0 dB", width=8)
        self.sc_gain = ttk.Scale(frm, from_=-24.0, to=12.0, variable=self.gain,
                                 command=lambda v: self.lb_gain.config(text="%.0f dB" % float(v)))
        self.sc_gain.grid(row=5, column=0, columnspan=1, sticky="we", **pad)
        self.lb_gain.grid(row=5, column=1, sticky="w")

        ttk.Label(frm, text="Czestotliwosc probkowania:").grid(row=6, column=0, sticky="w", **pad)
        self.fs = ttk.Combobox(frm, state="readonly", values=["auto", "44100", "48000", "96000"], width=10)
        self.fs.current(0)
        self.fs.grid(row=7, column=0, sticky="w", **pad)

        self.btn = tk.Button(frm, text="WLACZ ROUTING", font=("Segoe UI", 11, "bold"),
                             bg="#2e7d32", fg="white", activebackground="#1b5e20",
                             relief="raised", bd=4, command=self.toggle)
        self.btn.grid(row=8, column=0, columnspan=3, sticky="we", pady=(18, 6))

        self.canvas = tk.Canvas(frm, height=18, bg="#111")
        self.canvas.grid(row=9, column=0, columnspan=3, sticky="we", pady=(2, 8))
        self.bar = self.canvas.create_rectangle(0, 0, 0, 18, fill="#4caf50")

        self.lb_status = ttk.Label(frm, text="Wybierz urzadzenia i wcisnij WLACZ ROUTING.")
        self.lb_status.grid(row=10, column=0, columnspan=3, sticky="w", **pad)

        self.refresh()
        self.root.after(120, self._tick_vu)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def refresh(self):
        ins = find_devices(True)
        outs = find_devices(False)
        keep_in = self.cb_in.get()
        keep_out = self.cb_out.get()
        self.cb_in["values"] = ins
        self.cb_out["values"] = outs
        if ins:
            self.cb_in.current(0)
        if outs:
            self.cb_out.current(0)
        for cb, keep in ((self.cb_in, keep_in), (self.cb_out, keep_out)):
            if keep:
                vals = list(cb["values"])
                if keep in vals:
                    cb.current(vals.index(keep))
        self._status("Listy urzadzen odswiezone (WEJSCIA=%d, WYJSCIA=%d)." % (len(ins), len(outs)))

    def _status(self, msg):
        self.lb_status.config(text=msg)

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
        di = self._dev_index(self.cb_in.get())
        do = self._dev_index(self.cb_out.get())
        if di is None or do is None:
            self._status("Najpierw wybierz Wejscie i Wyjscie (przycisk Odswiez).")
            return
        in_dev = sd.query_devices(di)
        out_dev = sd.query_devices(do)
        try:
            fs_sel = self.fs.get()
            fs = int(in_dev["default_samplerate"]) if fs_sel == "auto" else int(fs_sel)
            in_ch = in_dev["max_input_channels"]
            if in_ch < 1:
                in_ch = 1
            out_ch = min(2, out_dev["max_output_channels"])
            if out_ch < 1:
                out_ch = 2
        except Exception as e:
            self._status("Blad konfiguracji urzadzenia: %s" % e)
            return

        def cb(indata, outdata, frames, t, status):
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
            self.last_level = float((x * x).mean()) ** 0.5

        self.gain_raw = 10 ** (self.gain.get() / 20.0)
        limit = 1.0
        gain = self.gain_raw
        try:
            self.stream = sd.Stream(samplerate=fs, dtype="float32", latency=0.1,
                                    blocksize=max(1, int(fs * 0.05)),
                                    device=(di, do), channels=(in_ch, out_ch), callback=cb)
            self.stream.start()
        except Exception as e:
            self._status("BLAD startu routingu: %s" % e)
            return
        self.running = True
        self.btn.config(text="WYLACZ ROUTING", bg="#c62828", activebackground="#b71c1c")
        self._status("Dziala: %s -> %s  @%d Hz, wzmocnienie %.0f dB" % (
            in_dev["name"], out_dev["name"], fs, self.gain.get()))

    def stop(self):
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            self.stream = None
        self.running = False
        self.last_level = 0.0
        self.btn.config(text="WLACZ ROUTING", bg="#2e7d32", activebackground="#1b5e20")
        self._status("Routing wylaczony.")

    def _tick_vu(self):
        if self.running:
            w = self.canvas.winfo_width()
            lvl = min(1.0, self.last_level * 2.2)
            self.canvas.coords(self.bar, 0, 0, max(2, int(w * lvl)), 18)
            if lvl >= 0.98:
                self.canvas.itemconfig(self.bar, fill="#e53935")
            elif lvl >= 0.85:
                self.canvas.itemconfig(self.bar, fill="#fdd835")
            else:
                self.canvas.itemconfig(self.bar, fill="#4caf50")
        self.root.after(120, self._tick_vu)

    def on_close(self):
        self.stop()
        self.root.destroy()


def main():
    ap = argparse.ArgumentParser(prog=TITLE)
    ap.add_argument("--selftest", action="store_true", help="wypisz urzadzenia i zakoncz")
    args = ap.parse_args()
    if args.selftest:
        lines = ["Wersja sounddevice: %s" % sd.__version__,
                 "Ilość urzadzen: %d" % len(sd.query_devices())]
        lines += ["  in : %s" % k for k in find_devices(True)]
        lines += ["  out: %s" % k for k in find_devices(False)]
        text = "\n".join(lines)
        print(text)
        try:
            with open("selftest.log", "w", encoding="utf-8") as f:
                f.write(text + "\n")
        except Exception:
            pass
        return 0
    root = tk.Tk()
    try:
        App(root)
        root.mainloop()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())