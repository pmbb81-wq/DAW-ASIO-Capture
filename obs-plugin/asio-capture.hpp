#pragma once
#include <windows.h>
#include <objbase.h>
#include <vector>
#include <string>
#include <cstdint>
#include "../shared/asio-types.hpp"
#include "../shared/shared-memory.hpp"  // for RING_FRAMES, DAW_MAX_CHANNELS etc.

// One captured channel descriptor
struct ASIOChanDesc {
    long           index;   // channel number in the ASIO driver
    char           name[64];
    ASIOSampleType type;
};

// Ring buffer used entirely inside OBS's process (no shared memory needed)
struct LocalRing {
    static constexpr uint32_t FRAMES = RING_FRAMES;
    static constexpr uint32_t CHMAX  = DAW_MAX_CHANNELS;

    volatile int64_t writePos = 0;
    uint32_t         sampleRate = 0;
    uint32_t         numChannels = 0;
    volatile int32_t active = 0;

    float data[FRAMES * CHMAX] = {};
};

// Owns a live ASIO input session.
// Create one, call open(), then the ring buffer fills automatically.
class ASIOCapture {
public:
    ASIOCapture();
    ~ASIOCapture();

    // Load driver, query channels — call before showing properties
    bool open(const GUID &driverClsid);
    void close();

    // Channel list available after open()
    const std::vector<ASIOChanDesc> &inputChannels() const { return m_inputs; }

    // Start capturing the given channel indices (into inputChannels() list)
    // bufSizeHint: preferred ASIO buffer size (0 = use driver preferred)
    bool startCapture(const std::vector<int> &channelSelection, long bufSizeHint = 0);
    void stopCapture();

    LocalRing *ring() { return &m_ring; }
    bool       isRunning() const { return m_running; }

    // Static trampoline targets — called by ASIO driver on its thread
    static void   cbBufferSwitch(long index, ASIOBool direct);
    static long   cbAsioMessage(long sel, long val, void *msg, double *opt);
    static ASIOTime *cbBufferSwitchTimeInfo(ASIOTime *t, long index, ASIOBool direct);
    static void   cbSampleRateChanged(ASIOSampleRate sr);

    static ASIOCapture *instance() { return s_instance; }

private:
    void  onBufferSwitch(long index);
    float toFloat(const void *src, ASIOSampleType type) const;
    int   byteSize(ASIOSampleType type) const;

    IASIO              *m_driver   = nullptr;
    HMODULE             m_driverDll = nullptr;
    GUID                m_clsid    = {};

    std::vector<ASIOChanDesc>  m_inputs;         // all input channels
    std::vector<int>           m_selection;      // indices into m_inputs
    std::vector<ASIOBufferInfo> m_bufInfos;      // ASIO buffer descriptors

    ASIOCallbacks m_callbacks = {};
    long          m_bufFrames = 0;
    bool          m_running   = false;

    LocalRing m_ring;

    static ASIOCapture *s_instance;
};
