#pragma once
#include <windows.h>
#include <cstdint>

// Named objects — visible to both the proxy (inside DAW) and the OBS plugin
#define DAW_CAPTURE_SHM_NAME   L"Local\\OBSDAWCapture_Shm"
#define DAW_CAPTURE_EVENT_NAME L"Local\\OBSDAWCapture_NewData"

// Ring buffer — power-of-two so frame index wraps with a cheap & mask
// 16384 frames @ 48kHz = ~341ms total; target read lag is only ~40ms
static constexpr uint32_t RING_FRAMES        = 16384; // 2^14
static constexpr uint32_t DAW_MAX_CHANNELS   = 8;     // avoid conflict with mmreg.h MAX_CHANNELS

// OBS reads this many frames per callback (~40ms @ 48kHz, ~46ms @ 44100)
static constexpr uint32_t READ_CHUNK_FRAMES  = 2048;

// OBS read pointer stays this many frames behind the write pointer (~40ms)
static constexpr uint32_t TARGET_LAG_FRAMES  = 2048;

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

static_assert(sizeof(DAWCaptureShm) <= 6 * 1024 * 1024, "SHM too large");
