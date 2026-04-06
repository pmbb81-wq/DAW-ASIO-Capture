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

// OBS reads this many frames per chunk (~10ms @ 48kHz)
// Smaller chunks = more responsive with tiny ASIO buffers (32-64 frames)
static constexpr uint32_t READ_CHUNK_FRAMES  = 512;

// Target lag in milliseconds — converted to frames at runtime based on sample rate.
// This keeps the delay consistent (~40ms) regardless of whether the DAW runs
// at 44.1kHz, 48kHz, 88.2kHz, 96kHz, or 192kHz.
static constexpr uint32_t TARGET_LAG_MS      = 40;

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
