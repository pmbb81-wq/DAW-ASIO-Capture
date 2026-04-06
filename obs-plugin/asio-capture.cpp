#include <windows.h>
#include <objbase.h>
#include <intrin.h>
#include <cstring>
#include <cstdio>
#include "asio-capture.hpp"

ASIOCapture *ASIOCapture::s_instance = nullptr;

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

ASIOCapture::ASIOCapture()  {}
ASIOCapture::~ASIOCapture() { close(); }

// ---------------------------------------------------------------------------
// open() — load the driver and enumerate its input channels
// ---------------------------------------------------------------------------

bool ASIOCapture::open(const GUID &clsid)
{
    close();
    m_clsid = clsid;

    // Load the driver DLL via COM (same way FL Studio does)
    // We use CoCreateInstance so Windows handles the DLL path lookup.
    HRESULT hr = CoCreateInstance(clsid, nullptr,
                                  CLSCTX_INPROC_SERVER, clsid,
                                  reinterpret_cast<void**>(&m_driver));
    if (FAILED(hr) || !m_driver) return false;

    // init() requires a window handle; desktop window is safe for headless use
    if (!m_driver->init(GetDesktopWindow())) {
        m_driver->Release(); m_driver = nullptr;
        return false;
    }

    // Query total channel count
    long numIn = 0, numOut = 0;
    m_driver->getChannels(&numIn, &numOut);

    // Build the input channel list — this includes physical inputs AND
    // software-return / loopback channels on multi-client interfaces like Focusrite.
    m_inputs.clear();
    for (long i = 0; i < numIn; ++i) {
        ASIOChannelInfo ci{};
        ci.channel = i;
        ci.isInput = ASIOTrue;
        m_driver->getChannelInfo(&ci);

        ASIOChanDesc d{};
        d.index = i;
        d.type  = ci.type;
        strncpy_s(d.name, ci.name, sizeof(d.name) - 1);
        m_inputs.push_back(d);
    }

    return true;
}

void ASIOCapture::close()
{
    stopCapture();
    if (m_driver) { m_driver->Release(); m_driver = nullptr; }
    m_inputs.clear();
    if (s_instance == this) s_instance = nullptr;
}

// ---------------------------------------------------------------------------
// startCapture() — set up ASIO buffers and start the stream
// ---------------------------------------------------------------------------

bool ASIOCapture::startCapture(const std::vector<int> &channelSelection, long bufSizeHint)
{
    if (!m_driver || channelSelection.empty()) return false;
    stopCapture();

    m_selection = channelSelection;

    // Determine buffer size
    long minSize, maxSize, prefSize, gran;
    m_driver->getBufferSize(&minSize, &maxSize, &prefSize, &gran);
    m_bufFrames = (bufSizeHint > 0) ? bufSizeHint : prefSize;
    if (m_bufFrames < minSize) m_bufFrames = minSize;
    if (m_bufFrames > maxSize) m_bufFrames = maxSize;

    // Build buffer info array — input channels only
    m_bufInfos.clear();
    for (int sel : channelSelection) {
        if (sel < 0 || sel >= (int)m_inputs.size()) continue;
        ASIOBufferInfo bi{};
        bi.isInput    = ASIOTrue;
        bi.channelNum = m_inputs[sel].index;
        bi.buffers[0] = bi.buffers[1] = nullptr;
        m_bufInfos.push_back(bi);
    }
    if (m_bufInfos.empty()) return false;

    // Set up callbacks
    m_callbacks.bufferSwitch         = ASIOCapture::cbBufferSwitch;
    m_callbacks.sampleRateDidChange  = ASIOCapture::cbSampleRateChanged;
    m_callbacks.asioMessage          = ASIOCapture::cbAsioMessage;
    m_callbacks.bufferSwitchTimeInfo = ASIOCapture::cbBufferSwitchTimeInfo;

    ASIOError err = m_driver->createBuffers(
        m_bufInfos.data(), (long)m_bufInfos.size(),
        m_bufFrames, &m_callbacks);
    if (err != ASE_OK) return false;

    // Fill ring metadata
    ASIOSampleRate sr = 0;
    m_driver->getSampleRate(&sr);
    m_ring.sampleRate   = static_cast<uint32_t>(sr);
    m_ring.numChannels  = static_cast<uint32_t>(m_bufInfos.size());
    m_ring.writePos     = 0;
    m_ring.active       = 0;

    s_instance = this;

    err = m_driver->start();
    if (err != ASE_OK) {
        m_driver->disposeBuffers();
        return false;
    }

    m_ring.active = 1;
    m_running = true;
    return true;
}

void ASIOCapture::stopCapture()
{
    if (!m_running) return;
    m_running = false;
    m_ring.active = 0;
    if (m_driver) {
        m_driver->stop();
        m_driver->disposeBuffers();
    }
    m_bufInfos.clear();
}

// ---------------------------------------------------------------------------
// Buffer switch — called by ASIO driver on its high-priority thread
// ---------------------------------------------------------------------------

void ASIOCapture::onBufferSwitch(long index)
{
    if (!m_ring.active) return;

    const uint32_t nch   = (uint32_t)m_bufInfos.size();
    const uint32_t mask  = LocalRing::FRAMES - 1;
    int64_t        wpos  = m_ring.writePos;

    for (long f = 0; f < m_bufFrames; ++f) {
        uint32_t slot = (uint32_t)((wpos + f) & mask);
        float *dst = &m_ring.data[slot * LocalRing::CHMAX];

        for (uint32_t ch = 0; ch < nch && ch < LocalRing::CHMAX; ++ch) {
            const void *src = static_cast<const uint8_t*>(m_bufInfos[ch].buffers[index])
                              + f * byteSize(m_inputs[m_selection[ch]].type);
            dst[ch] = toFloat(src, m_inputs[m_selection[ch]].type);
        }
        for (uint32_t ch = nch; ch < LocalRing::CHMAX; ++ch)
            dst[ch] = 0.0f;
    }

    _ReadWriteBarrier();
    m_ring.writePos = wpos + m_bufFrames;
}

// ---------------------------------------------------------------------------
// Format conversion
// ---------------------------------------------------------------------------

int ASIOCapture::byteSize(ASIOSampleType t) const
{
    switch (t) {
    case ASIOSTInt16LSB: case ASIOSTInt16MSB: return 2;
    case ASIOSTInt24LSB: case ASIOSTInt24MSB: return 3;
    default: return 4;  // int32, float32 and all 32-bit packed variants
    }
}

float ASIOCapture::toFloat(const void *src, ASIOSampleType type) const
{
    switch (type) {
    case ASIOSTFloat32LSB: {
        float v; memcpy(&v, src, 4); return v;
    }
    case ASIOSTInt32LSB: {
        int32_t v; memcpy(&v, src, 4);
        return v * (1.0f / 2147483648.0f);
    }
    case ASIOSTInt32LSB24: {
        int32_t v; memcpy(&v, src, 4);
        return (v >> 8) * (1.0f / 8388608.0f);
    }
    case ASIOSTInt24LSB: {
        const uint8_t *b = static_cast<const uint8_t*>(src);
        int32_t v = b[0] | (b[1] << 8) | (b[2] << 16);
        if (v & 0x800000) v |= 0xFF000000;
        return v * (1.0f / 8388608.0f);
    }
    case ASIOSTInt16LSB: {
        int16_t v; memcpy(&v, src, 2);
        return v * (1.0f / 32768.0f);
    }
    case ASIOSTFloat32MSB: {
        uint32_t r; memcpy(&r, src, 4); r = _byteswap_ulong(r);
        float v; memcpy(&v, &r, 4); return v;
    }
    case ASIOSTInt32MSB: {
        uint32_t r; memcpy(&r, src, 4); r = _byteswap_ulong(r);
        int32_t v; memcpy(&v, &r, 4);
        return v * (1.0f / 2147483648.0f);
    }
    default: return 0.0f;
    }
}

// ---------------------------------------------------------------------------
// Static trampolines
// ---------------------------------------------------------------------------

void ASIOCapture::cbBufferSwitch(long index, ASIOBool)
{
    if (s_instance) s_instance->onBufferSwitch(index);
}

ASIOTime *ASIOCapture::cbBufferSwitchTimeInfo(ASIOTime *t, long index, ASIOBool)
{
    if (s_instance) s_instance->onBufferSwitch(index);
    return t;
}

void ASIOCapture::cbSampleRateChanged(ASIOSampleRate sr)
{
    if (s_instance) s_instance->m_ring.sampleRate = static_cast<uint32_t>(sr);
}

long ASIOCapture::cbAsioMessage(long sel, long val, void *, double *)
{
    // Respond to the minimum set of messages ASIO drivers expect
    switch (sel) {
    case 1: // kAsioSelectorSupported
        return (val == 1 || val == 2 || val == 3 || val == 6) ? 1 : 0;
    case 2: // kAsioEngineVersion
        return 2;
    case 3: // kAsioResetRequest
        return 1;
    case 6: // kAsioSupportsTimeInfo
        return 1;
    default:
        return 0;
    }
}
