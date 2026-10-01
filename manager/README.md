# AXE I/O ONE - DAW Recorder

A small single-window Windows app that records exactly what your DAW / JAM VOX
sends through ASIO straight to disk (WAV, or MP3 when `ffmpeg` is available) -
without OBS, without virtual cables, and without any hardware-monitoring bleed.

It reads the proxy's shared memory (`Local\OBSDAWCapture_Shm`) read-only, so it
can run at the same time as the OBS source: each consumer keeps its own read
position.

> The user interface is in **Polish**. The detailed description is in the Polish
> [`README.txt`](README.txt); this file is the English overview.

## Requirement

The ASIO driver must already be redirected to the proxy. That is done by the
**installer** `AXE-IO-ONE-OBS-Audio-Capture-Setup.exe` (it installs the OBS
plugin and sets the 64-bit / 32-bit redirect). Once the redirect is in place,
the proxy creates the shared memory whenever a host (DAW / JAM VOX) opens the
ASIO driver.

## Download

Grab **`AXE_IO_ONE_OBS_Manager.exe`** from the
[Releases page](https://github.com/pmbb81-wq/DAW-ASIO-Capture/releases/latest).
It is self-contained - no Python needed.

## Usage

1. Start your DAW (or JAM VOX) and make it play through the ASIO driver.
2. Run **`AXE_IO_ONE_OBS_Manager.exe`**.
3. Click **"Sprawdz proxy"** - the status line should read
   *"Proxy aktywne - mozesz nagrywac"*.
4. Choose the folder, file prefix, format, output pair (`Out 1-2` by default) and
   the lag in ms (default **10**, resync buffer only - it does not colour the
   recording).
5. **START** ... **STOP**. The file appears in the chosen folder.

## Output formats

`ffmpeg` is **bundled inside the app**, so every format below works out of the
box - nothing to install or put on `PATH`.

| Format | Encoder | Notes |
|--------|---------|-------|
| `WAV`  | -       | Native, 16-bit stereo PCM. Written directly, no conversion. |
| `MP3`  | `libmp3lame` | 192 kbps CBR. |
| `FLAC` | `flac`  | Lossless, compression level 8. |
| `OGG`  | `libvorbis` | VBR quality 5. |
| `M4A`  | `aac`   | 192 kbps AAC in an MP4 container. |

The recorder always writes a WAV first and then transcodes it, so a conversion
failure leaves the lossless WAV on disk instead of losing the take. If `ffmpeg`
cannot be found at all, the app falls back to WAV and says so.

## Building from source (optional)

```bash
pip install pyinstaller
# ffmpeg must be present as ffmpeg/ffmpeg.exe next to the spec (it gets
# bundled into the exe):
#   curl -L -o ffmpeg.zip \
#     https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip
pyinstaller AXE_IO_ONE_OBS_Manager.spec
```

The result is `dist/AXE_IO_ONE_OBS_Manager.exe` (~67 MB, ffmpeg included).
No third-party runtime packages are required (Tkinter only).

## Shared-memory layout

The layout is fixed and must match `shared/shared-memory.hpp` in this repository:

| Offset | Type | Meaning |
|-------:|------|---------|
| 0  | uint32 | sampleRate |
| 4  | uint32 | numChannels |
| 8  | uint32 | bufferFrames |
| 12 | int64  | writePos (next frame the proxy will write) |
| 20 | int64  | readPos (used by the OBS source - the recorder does not touch it) |
| 28 | int32  | active (1 while streaming) |
| 32 | int32  | padding |
| 36 | float32[] | interleaved data, `RING_FRAMES * 8` samples |

## License

MIT (see the repository `LICENSE`). Original plugin by Monte Emerson
([emersound/DAW-ASIO-Capture](https://github.com/emersound/DAW-ASIO-Capture)).

The bundled `ffmpeg.exe` is a separate LGPL build
([FFmpeg](https://ffmpeg.org/), LGPL-2.1-or-later) redistributed unmodified.
Source: <https://github.com/BtbN/FFmpeg-Builds>.
