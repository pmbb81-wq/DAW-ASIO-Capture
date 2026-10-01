#pragma once
#include <windows.h>
#include <cstdint>

// Named objects — visible to both the proxy (inside DAW) and the OBS plugin
#define DAW_CAPTURE_SHM_NAME   L"Local\\OBSDAWCapture_Shm"
#define DAW_CAPTURE_EVENT_NAME L"Local\\OBSDAWCapture_NewData"

// Ring buffer — power-of-two so frame index wraps with a cheap & mask
// 131072 frames = ~2.7s @ 48kHz, ~680ms @ 192kHz — safe at all standard rates
static constexpr uint32_t RING_FRAMES        = 131072; // 2^17
static constexpr uint32_t DAW_MAX_CHANNELS   = 8;      // avoid conflict with mmreg.h MAX_CHANNELS

// OBS reads this many frames per chunk.
// CRITICAL for latency: the reader can only emit a whole chunk at a time,
// so the chunk size is the hard floor of the capture delay. 256 frames cost
// ~5.3ms @48k; 64 frames cost ~1.3ms @48k with no downside for normal ASIO
// buffers (the per-wake cap below still drains a full ASIO buffer in one go).
static constexpr uint32_t READ_CHUNK_FRAMES  = 64;

// Fallback lag in milliseconds — the per-source "capture_lag_ms" setting
// overrides this at runtime. Small cushion so the reader never outruns the
// writer's ASIO bursts (protects against torn reads of the frame being
// written). 4ms is safe at every standard rate.
static constexpr uint32_t TARGET_LAG_MS      = 4;

// Written by the proxy (inside DAW process), read by OBS plugin
#pragma pack(push, 1)
struct DAWCaptureShm {
    // Metadata — written once at ASIO start
    uint32_t sampleRate;     // e.g. 44100 or 48000
    uint32_t numChannels;    // output channels captured (usually 2)
    uint32_t bufferFrames;   // ASIO hardware buffer size (e.g. 128, 256)

    // Ring buffer control
    // Indices never reset; use & (RING_FRAMES-1) to get slot in data[]
    volatile int64_t writePos; // next frame the proxy will write
    volatile int64_t readPos;  // next frame OBS will read

    volatile int32_t active;   // set to 1 by proxy once streaming, 0 on stop
    int32_t          _pad;

    // Interleaved float audio ring: data[(frameIdx & (RING_FRAMES-1)) * DAW_MAX_CHANNELS + ch]
    float data[RING_FRAMES * DAW_MAX_CHANNELS];
};
#pragma pack(pop)

static_assert(sizeof(DAWCaptureShm) <= 8 * 1024 * 1024, "SHM exceeds 8MB — reduce RING_FRAMES or DAW_MAX_CHANNELS");
