# AXE I/O ONE OBS Manager

A single-window Windows app that gets a guitar/DAW's audio into OBS and back to
your ears with as little latency as possible. It is the companion GUI for the
[DAW ASIO Capture](../README.md) plugin in this repository.

> The app's user interface is in **Polish**. The detailed walk-through lives in
> the Polish [`README.txt`](README.txt); this file is the English overview.

## What it does

Five tabs, one window:

| Tab | Purpose |
|-----|---------|
| **MOST (ONE -> cable)** | Routes an input device (e.g. AXE I/O ONE) to a virtual cable (VB-CABLE) so OBS can record it as a separate track. |
| **FLEXASIO (ASIO)** | Installs/registers [FlexASIO](https://github.com/dechamps/FlexASIO) and writes `%USERPROFILE%\FlexASIO.toml` profiles (JAM VOX / OBS). |
| **ASIO Capture (wtyczka)** | Installs the `obs-daw-capture` plugin into OBS, sets up the 64-bit and 32-bit ASIO redirects, and restores them on demand. |
| **Nagrywarka (DAW)** | Records the ASIO source straight to disk (WAV/FLAC/MP3) without OBS. |
| **Monitoring (bez OBS)** | Real-time monitoring of whatever the ASIO program plays, with an adjustable latency cushion — no OBS, no DAW. |

## Download

Grab **`AXE_IO_ONE_OBS_Manager.exe`** from the
[Releases page](https://github.com/pmbb81-wq/DAW-ASIO-Capture/releases/latest).
It is self-contained (no Python needed) and bundles the plugin DLLs plus the
FlexASIO installer.

## Requirements

- Windows 10/11 (64-bit)
- [OBS Studio](https://obsproject.com/) (the plugin is built against the OBS 32.x API)
- An ASIO audio interface and its driver installed
- For the **MOST** tab only: [VB-CABLE](https://vb-audio.com/Cable/) (free; download and reboot once)

## Quick start (capture a DAW / JAM VOX into OBS)

1. Run **`AXE_IO_ONE_OBS_Manager.exe`**.
2. Open the **ASIO Capture (wtyczka)** tab and click **"Zainstaluj / aktualizuj wtyczke"** (accept the UAC prompt).
3. In OBS: **Sources +** -> **DAW Audio Capture (ASIO)** -> pick your ASIO driver (e.g. `AXE IO ONE`) -> **OK**.
4. Start your DAW (or JAM VOX) using the same ASIO driver — the OBS meter moves.
5. Is the DAW 32-bit (e.g. JAM VOX)? On the **ASIO Capture** tab, select the driver and click **"Zainstaluj redirect 32-bit (JAM VOX)"**.
6. To hear the capture without OBS, use the **Monitoring (bez OBS)** tab instead.

**Capture lag:** each OBS source has a **"Capture lag (ms)"** property; the default is **4 ms**. Lower = tighter sync, higher = safer against crackles. The hard floor is about 1.3 ms.

## Building from source (optional)

```bash
pip install sounddevice numpy pyinstaller
# put the FlexASIO installer next to the DLLs so it gets bundled:
#   installers/FlexASIO-1.10b.exe
pyinstaller AXE_IO_ONE_OBS_Manager.spec
```

The result is `dist/AXE_IO_ONE_OBS_Manager.exe`.

## Notes

- **FlexASIO** is GPL-3.0 — its installer is redistributed as-is inside the
  manager / release; source and license: https://github.com/dechamps/FlexASIO
- **VB-CABLE** is freeware and is **not** bundled; download it yourself.
- This manager and the low-latency plugin changes are MIT licensed (see the
  repository `LICENSE`). Original plugin by Monte Emerson
  ([emersound/DAW-ASIO-Capture](https://github.com/emersound/DAW-ASIO-Capture)).
