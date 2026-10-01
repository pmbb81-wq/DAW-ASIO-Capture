# OBS DAW Audio Capture (ASIO)

Capture your DAW's ASIO output directly into OBS as a separate audio source. No virtual audio cables, no loopback routing, no mic bleed — pure software output from your DAW into OBS.

> **This fork (`pmbb81-wq/DAW-ASIO-Capture`)** is a low-latency build of the original
> [`emersound/DAW-ASIO-Capture`](https://github.com/emersound/DAW-ASIO-Capture).
> The capture delay is configurable per source and defaults to a single-digit
> millisecond cushion instead of the original fixed ~40 ms, the reader wakes on
> an event instead of polling, and a **direct-monitoring** path lets the proxy
> play the ASIO output straight to your headphones without any extra process.
> See [Fork changes](#fork-changes) for the full list. Original MIT work by
> Monte Emerson; this fork keeps the same MIT license.

Built for musicians who stream live production sessions and need their DAW audio in OBS without interfering with their low-latency ASIO monitoring.

## About

If you've ever tried to get your DAW's audio into OBS for streaming, you know the pain. The usual advice is to install a virtual audio cable (VB-Audio, VoiceMeeter, etc.), route your DAW's output through it, set it as a source in OBS, pray the sample rates match, and troubleshoot the inevitable latency and routing headaches. Some setups require three or four pieces of software just to bridge the gap between your DAW and your stream.

This plugin replaces all of that with a single install. No virtual audio cables, no extra routing software, no DAW configuration changes, no latency added to your monitoring. Select your ASIO driver in OBS, start your DAW, and you're live. It just works.

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│  FL Studio / Ableton / Reaper / any DAW                        │
│                                                                 │
│  Calls ASIO driver to play audio                                │
│       │                                                         │
│       ▼                                                         │
│  ┌──────────────────┐    ┌──────────────────┐                   │
│  │  asio-proxy.dll  │───▶│ Real ASIO Driver │──▶ Audio Hardware │
│  │  (intercepts)    │    │ (Focusrite, etc) │                   │
│  └────────┬─────────┘    └──────────────────┘                   │
│           │                                                     │
│           │ Copies output buffers                               │
│           ▼                                                     │
│  ┌──────────────────┐                                           │
│  │  Shared Memory   │  Named ring buffer: Local\OBSDAWCapture   │
│  │  (~40ms buffer)  │                                           │
│  └────────┬─────────┘                                           │
└───────────┼─────────────────────────────────────────────────────┘
            │  (cross-process)
┌───────────┼─────────────────────────────────────────────────────┐
│  OBS      │                                                     │
│           ▼                                                     │
│  ┌──────────────────────┐                                       │
│  │ obs-daw-capture.dll  │──▶ OBS Audio Source ──▶ Stream/Record │
│  │ (reads ring buffer)  │                                       │
│  └──────────────────────┘                                       │
└─────────────────────────────────────────────────────────────────┘
```

The plugin works by inserting a transparent **ASIO proxy driver** between your DAW and your real audio interface driver. When your DAW loads its ASIO driver, Windows loads our proxy instead. The proxy:

1. Loads the real driver and forwards all calls to it — your DAW works exactly as before
2. After each audio buffer callback (when the DAW has filled its output buffers), copies the output samples into a **shared memory ring buffer**
3. The OBS plugin reads from the other side of that ring buffer with a small, configurable delay (default ~4 ms, adjustable per source)

Your ASIO monitoring stays at its original ultra-low latency. The capture delay is only in the OBS copy — think of it like a tape recorder that's always a hair behind the live performance. In this fork the cushion is miniscule, so the OBS audio lines up tightly with what you hear.

### Why Not Just Use...

| Approach | Problem |
|----------|---------|
| **Desktop Audio capture** | ASIO bypasses Windows audio entirely — there's nothing to capture |
| **Virtual audio cable** (VB-Audio, etc.) | Adds latency to your monitoring, requires routing setup in your DAW |
| **ASIO multi-client loopback** (Focusrite "Analog Out" inputs) | Captures the full hardware mix including your microphone and input monitoring — not pure DAW output |
| **obs-asio plugin** | Only captures ASIO *inputs* (mic/line), not DAW output |

This plugin captures the DAW's output buffers at the software level, before they hit the hardware. Pure DAW audio, zero bleed.

## A Note on Focusrite Hardware

Focusrite Scarlett, Clarett, and Red interfaces have a built-in multi-client ASIO feature with "loopback" input channels (labeled "Analog Out 1-2" etc. in the input list). You might think you could capture DAW output by recording those channels — but those loopback channels carry the **full hardware mix**, including your microphone, input monitoring, and any direct monitoring configured in Focusrite Control. That's not what you want for streaming.

This plugin bypasses that entirely by intercepting the DAW's output buffers at the software level, before they reach the Focusrite hardware. You get pure DAW audio — no mic bleed, no input monitoring, no hardware mix contamination.

## Supported Hardware

Works with any standard ASIO driver:

- **Focusrite** Scarlett, Clarett, Red series
- **Universal Audio** Apollo, Volt
- **RME** Fireface, Babyface, ADI
- **MOTU** M-series, Ultralite, 828
- **PreSonus** Studio, Quantum
- **Steinberg** UR series
- **Behringer** UMC series
- **Native Instruments** Komplete Audio
- **FL Studio ASIO** (built-in)
- Any other ASIO-compliant driver

## Supported DAWs

Any DAW that uses ASIO:

- FL Studio
- Ableton Live
- Reaper
- Cubase / Nuendo
- Studio One
- Bitwig Studio
- Pro Tools (ASIO mode)
- Logic Pro (via ASIO bridge)

## Tested With

This plugin has been tested with **FL Studio** and a **Focusrite Scarlett 8i6 (Gen 3)**. It should work with any DAW and any standard ASIO driver, but no other combinations have been verified. If you test it with a different DAW or interface, feel free to share your results — fork the repo and update this section.

## Requirements

- Windows 10/11 (64-bit)
- OBS Studio 28.0 or later (64-bit)
- An ASIO audio interface or driver
- Administrator privileges (one-time, for driver registration)

## Installation

### From Release (Recommended)

1. Download the latest release ZIP
2. Extract the contents
3. Close OBS if it's running
4. Right-click **`install.bat`** → **Run as administrator**
5. Done

### From Source

See [Building from Source](#building-from-source) below.

## Usage

### First-Time Setup

1. **Start OBS** — the plugin loads automatically
2. In the Sources panel, click **+** → **DAW Audio Capture (ASIO)**
3. In Properties, select your **ASIO Driver** from the dropdown (e.g., "Focusrite USB ASIO")
4. A **UAC prompt** will appear — click **Yes** (this registers the proxy driver; one-time per driver selection)
5. Select your **Output Pair** (usually "Out 1-2 (Master / Default)")
6. Click **OK**

### Starting a Session

1. Start **OBS** first
2. Start your **DAW** — it will load through the proxy transparently
3. You'll see the DAW audio meters bouncing in OBS
4. **Important:** Right-click the DAW source → **Advanced Audio Properties** → set Monitoring to **Monitor Off**
   - This ensures only the stream/recording hears the delayed audio
   - You continue monitoring through your ASIO interface at zero latency

### Ending a Session

1. Close your **DAW** first
2. Close **OBS** — this automatically restores your ASIO driver to its original state

If OBS crashes or is force-closed, run **`uninstall.bat`** as administrator to restore your driver.

### Output Pairs

Your ASIO interface likely has multiple output pairs. The plugin lets you select which pair to capture:

| Selection | Typical Use |
|-----------|-------------|
| **Out 1-2** | Master / main mix (most common) |
| **Out 3-4** | Headphone mix or submix |
| **Out 5-6** | Additional outputs |
| **Out 7-8** | Additional outputs |

In FL Studio, this corresponds to the output routing in the Mixer. Most setups send the master to Out 1-2.

## Uninstallation

1. Close OBS and your DAW
2. Right-click **`uninstall.bat`** → **Run as administrator**

This removes both plugin DLLs from OBS and restores all ASIO drivers to their original registration.

## Building from Source

### Prerequisites

- Visual Studio 2022 (Community edition is fine) with C++ desktop workload
- CMake 3.16+
- OBS Studio installed (for header generation)
- Git

### Steps

```bash
git clone https://github.com/pmbb81-wq/DAW-ASIO-Capture.git
cd DAW-ASIO-Capture
```

#### 1. Get OBS Headers

```bash
cd deps
git clone --sparse --filter=blob:none https://github.com/obsproject/obs-studio.git
cd obs-studio
git sparse-checkout set libobs
cd ../..
```

#### 2. Generate obs.lib

From a **Visual Studio x64 Native Tools Command Prompt**:

```bash
cd deps

# Dump exports from your installed OBS
dumpbin /exports "C:\Program Files\obs-studio\bin\64bit\obs.dll" > obs-exports.txt

# Create a .def file from the exports (or use the provided script)
# Then generate the import library:
lib /def:obs.def /out:obs.lib /machine:x64

cd ..
```

A pre-built `obs.lib` is included in releases for convenience.

#### 3. Configure and Build

```bash
cmake -B build -G "Visual Studio 17 2022" -A x64
cmake --build build --config Release
```

Output:
- `build/obs-plugin/Release/obs-daw-capture.dll`
- `build/asio-proxy/Release/asio-proxy.dll`

#### 4. Install

```bash
# Run install.bat as administrator, or copy manually:
copy build\obs-plugin\Release\obs-daw-capture.dll "C:\Program Files\obs-studio\obs-plugins\64bit\"
copy build\asio-proxy\Release\asio-proxy.dll "C:\Program Files\obs-studio\obs-plugins\64bit\"
```

## Technical Details

### Architecture

The system has two DLLs that run in separate processes:

**`asio-proxy.dll`** — runs inside your DAW's process
- Registered as a COM in-process server that replaces your real ASIO driver
- Loads the real driver internally and forwards all ASIO calls transparently
- Intercepts `createBuffers` to identify output channels and their sample format
- Intercepts `bufferSwitch` / `bufferSwitchTimeInfo` to copy filled output buffers to shared memory
- Converts all sample formats (Int16, Int24, Int32, Float32, Float64, MSB/LSB variants) to Float32

**`obs-daw-capture.dll`** — runs inside OBS's process
- Registers as an OBS audio input source
- Opens the shared memory ring buffer
- Reads audio data a small, configurable lag behind the write position (default 4 ms)
- Outputs planar Float32 audio to OBS at the DAW's native sample rate

### Shared Memory Ring Buffer

The two processes communicate through a named shared memory segment (`Local\OBSDAWCapture_Shm`):

- **Ring size:** 131,072 frames (~2.7s at 48kHz) — power of two for efficient wrapping
- **Target lag:** 4 ms by default (`TARGET_LAG_MS`), overridable per source via the **Capture lag (ms)** property
- **Read chunk:** 64 frames — the reader can only emit whole chunks, so this is the hard floor of the capture delay (~1.3 ms at 48kHz)
- **Max channels:** 8 (supports up to 7.1 surround, though stereo is typical)
- **Format:** Interleaved Float32 in the ring, converted to planar for OBS

The proxy writes frames and advances `writePos`. OBS reads frames a configurable lag behind `writePos`. A named event (`Local\OBSDAWCapture_NewData`) allows OBS to wake immediately when new data arrives instead of polling, so the reader does not add polling latency on top of the cushion.

### ASIO Driver Interception

Most ASIO hosts (FL Studio, Reaper, etc.) do not use Windows COM (`CoCreateInstance`) to load drivers. Instead, they:

1. Read the driver's CLSID from `HKLM\SOFTWARE\ASIO\{DriverName}\CLSID`
2. Look up the DLL path from `HKLM\SOFTWARE\Classes\CLSID\{clsid}\InprocServer32`
3. Call `LoadLibrary` + `GetProcAddress("DllGetClassObject")` directly

The plugin intercepts this by modifying the HKLM InprocServer32 default value to point to `asio-proxy.dll`, and saving the original DLL path as `OriginalServer` in the same key. This requires administrator privileges (handled via UAC prompt).

When OBS closes normally, it restores the original driver registration. The uninstall script also performs this restoration as a safety net.

### Sample Format Support

The proxy handles all standard ASIO sample types:

| Format | Bit Depth | Byte Order |
|--------|-----------|------------|
| `ASIOSTInt16LSB/MSB` | 16-bit integer | Little/Big endian |
| `ASIOSTInt24LSB/MSB` | 24-bit packed integer | Little/Big endian |
| `ASIOSTInt32LSB/MSB` | 32-bit integer | Little/Big endian |
| `ASIOSTInt32LSB16/18/20/24` | 16-24 bit in 32-bit container | Little endian |
| `ASIOSTFloat32LSB/MSB` | 32-bit float | Little/Big endian |
| `ASIOSTFloat64LSB/MSB` | 64-bit float | Little/Big endian |

All formats are normalized to Float32 [-1.0, 1.0] before writing to shared memory.

## Troubleshooting

### "Couldn't open the ASIO driver" in your DAW

1. Make sure OBS is running **before** you start your DAW
2. Check that you selected a driver in the plugin Properties and accepted the UAC prompt
3. Verify the proxy is registered: open `regedit`, navigate to `HKLM\SOFTWARE\Classes\CLSID\{your-driver-clsid}\InprocServer32` — the default value should point to `asio-proxy.dll`
4. Check `%TEMP%\obs-asio-proxy.log` for error details

### No audio in OBS (meters not moving)

1. Check the proxy log at `%TEMP%\obs-asio-proxy.log` — look for `[AUDIO]` lines with non-zero float values
2. Verify the Output Pair matches your DAW's master output routing
3. Make sure your DAW is actually playing audio (not muted/paused)
4. Check OBS log (Help → Log Files → Current Log) — search for `[DAW Capture]` entries

### Audio monitoring feedback / echo

Set the DAW source to **Monitor Off** in OBS Advanced Audio Properties. You should hear audio through your ASIO interface directly, not through OBS.

### DAW shows driver as "[DriverName] [OBS]"

This is normal — the proxy appends "[OBS]" to the driver name so you can confirm it's active. Audio quality and latency are identical to the original driver.

### Restoring your driver after a crash

If OBS crashes without cleaning up:
```
# Run as administrator
uninstall.bat
```
Or manually in `regedit`: find your driver's CLSID under `HKLM\SOFTWARE\Classes\CLSID`, copy the `OriginalServer` value back to `(Default)`, and delete the `OriginalServer` entry.

## Project Structure

```
obs-daw-capture/
├── shared/                  # Shared between proxy and plugin
│   ├── shared-memory.hpp    # Ring buffer struct and constants
│   └── asio-types.hpp       # ASIO type definitions (no SDK dependency)
├── asio-proxy/              # Proxy DLL (runs inside DAW)
│   ├── CMakeLists.txt
│   ├── asio-proxy.def       # DLL export definitions
│   ├── dllmain.cpp          # COM server entry point
│   ├── proxy-driver.cpp     # ASIO driver wrapper + ring buffer writer
│   └── proxy-driver.hpp
├── obs-plugin/              # OBS plugin DLL
│   ├── CMakeLists.txt
│   ├── plugin-main.cpp      # OBS module entry point
│   ├── daw-source.cpp       # OBS audio source + ring buffer reader
│   ├── daw-source.hpp
│   ├── asio-capture.cpp     # Direct ASIO capture (unused, kept for reference)
│   ├── asio-capture.hpp
│   ├── asio-registry.cpp    # ASIO driver enumeration + proxy registration
│   └── asio-registry.hpp
├── deps/                    # Build dependencies
│   ├── obs-studio/          # OBS headers (sparse clone)
│   ├── obs.lib              # OBS import library
│   └── obsconfig.h          # Build config stub
├── install.bat              # Installer (run as admin)
├── uninstall.bat            # Uninstaller (run as admin)
├── CMakeLists.txt           # Top-level CMake
├── LICENSE                  # MIT License
└── README.md                # This file
```

## Project Status

This project is released as-is. **I will not be maintaining it or writing updates.** It works, it solves the problem, and it's here for anyone who needs it.

If you want to extend it, fix bugs, or add features — **fork it.** That's why it's MIT licensed. Some ideas for anyone who picks it up:

- **Multi-instance support** — currently only one DAW source per OBS session
- **Channel naming** — query actual output names from the ASIO driver and show them in the Output Pair dropdown
- **macOS / Linux** — ASIO is Windows-only, but CoreAudio (macOS) and JACK (Linux) could use similar proxy approaches
- **Automatic driver restoration watchdog** — a background service to restore drivers if OBS crashes
- **ASIO4ALL / FL Studio ASIO support** — test and document behavior with software ASIO drivers

## Fork changes

This fork builds on the original plugin and focuses on latency, robustness and
a few quality-of-life extras:

- **Low, configurable capture lag.** The reader no longer holds a fixed ~40 ms
  cushion. `TARGET_LAG_MS` defaults to **4 ms**, and each source exposes a
  **Capture lag (ms)** property so you can trade stability for latency.
- **Smaller read chunk.** `READ_CHUNK_FRAMES` dropped from 256 to **64 frames**
  (~1.3 ms at 48 kHz). Because the reader can only emit whole chunks, this is
  the hard floor of the capture delay; the per-wake cap still drains a full ASIO
  buffer in one go, so the smaller chunk costs nothing on normal setups.
- **Event-driven wake, no polling tax.** The reader wakes on the proxy's
  data event with a short 2 ms wait and an adaptive "snap" threshold, so large
  ASIO buffers are still drained completely without dropouts.
- **Bigger ring buffer.** 131,072 frames (~2.7 s at 48 kHz) absorbs longer
  stalls and very high sample rates.
- **Direct monitoring.** The proxy can play the captured ASIO output straight
  to a chosen output device (WASAPI) from its own render thread — no second
  process, no Python, no extra buffering. See `shared/direct-monitor.cpp`.
- **32-bit proxy build.** `asio-proxy-x86/` builds the proxy for 32-bit hosts
  and DAWs (e.g. legacy 32-bit hosts).
- **Synchronous capture fix.** Output buffers are copied to the ring inside
  `bufferSwitch` for hosts that fill them synchronously, fixing missing audio
  on some DAWs.

## License

[MIT](LICENSE) — do whatever you want with it. Fork it, modify it, sell it, ship it. No restrictions, no obligations.

## Credits

Built by Monte Emerson with assistance from Claude (Anthropic).
