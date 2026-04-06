#include <windows.h>
#include <intrin.h>
#include <cstring>
#include <cmath>
#include <stdio.h>
#include "proxy-driver.hpp"

// ---- DIAGNOSTIC AUDIO LOGGING — flip to 0 to remove ----
#define PROXY_DEBUG_AUDIO_LOG 1
// ---------------------------------------------------------

// Simple append-log shared across this TU
static void ProxyLog(const char *fmt, ...)
{
    wchar_t tmp[MAX_PATH], path[MAX_PATH];
    GetTempPathW(MAX_PATH, tmp);
    swprintf_s(path, L"%sobs-asio-proxy.log", tmp);
    HANDLE f = CreateFileW(path,
        GENERIC_WRITE, FILE_SHARE_READ, nullptr,
        OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) return;
    SetFilePointer(f, 0, nullptr, FILE_END);
    char buf[512];
    va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
    DWORD w; WriteFile(f, buf, (DWORD)strlen(buf), &w, nullptr);
    CloseHandle(f);
}

ProxyASIODriver *ProxyASIODriver::s_instance = nullptr;

// Forward declaration — defined further below near the other format helpers
static int sampleByteSize(ASIOSampleType t);

// ---------------------------------------------------------------------------
// Construction / destruction
// ---------------------------------------------------------------------------

ProxyASIODriver::ProxyASIODriver(IASIO *real, HMODULE realDll, const GUID &clsid)
    : m_real(real), m_realDll(realDll), m_clsid(clsid), m_refCount(1)
    , m_bufInfos(nullptr), m_numChannels(0), m_bufferFrames(0), m_numOut(0)
    , m_shmHandle(nullptr), m_shm(nullptr), m_eventHandle(nullptr)
{
    memset(&m_dawCallbacks, 0, sizeof(m_dawCallbacks));
    memset(m_outIdx,  0, sizeof(m_outIdx));
    memset(m_outType, 0, sizeof(m_outType));

    s_instance = this;
    openSharedMemory();
}

ProxyASIODriver::~ProxyASIODriver()
{
    closeSharedMemory();
    if (m_real)   { m_real->Release(); m_real = nullptr; }
    if (m_realDll){ FreeLibrary(m_realDll); m_realDll = nullptr; }
    if (s_instance == this) s_instance = nullptr;
}

// ---------------------------------------------------------------------------
// IUnknown
// ---------------------------------------------------------------------------

HRESULT STDMETHODCALLTYPE ProxyASIODriver::QueryInterface(REFIID riid, void **ppv)
{
    // ASIO convention: only responds to its own CLSID used as IID
    if (IsEqualIID(riid, m_clsid) || IsEqualIID(riid, IID_IUnknown)) {
        *ppv = static_cast<IASIO*>(this);
        AddRef();
        return S_OK;
    }
    *ppv = nullptr;
    return E_NOINTERFACE;
}

ULONG STDMETHODCALLTYPE ProxyASIODriver::AddRef()
{
    return InterlockedIncrement(&m_refCount);
}

ULONG STDMETHODCALLTYPE ProxyASIODriver::Release()
{
    LONG ref = InterlockedDecrement(&m_refCount);
    if (ref == 0) delete this;
    return ref;
}

// ---------------------------------------------------------------------------
// Simple pass-throughs
// ---------------------------------------------------------------------------

ASIOBool ProxyASIODriver::init(void *sysHandle) {
    return m_real->init(sysHandle);
}
void ProxyASIODriver::getDriverName(char *name) {
    m_real->getDriverName(name);
    // Append a marker so users can identify our proxy in logs
    strncat_s(name, 32, " [OBS]", 6);
}
long     ProxyASIODriver::getDriverVersion()         { return m_real->getDriverVersion(); }
void     ProxyASIODriver::getErrorMessage(char *s)   { m_real->getErrorMessage(s); }
ASIOError ProxyASIODriver::start()                   { return m_real->start(); }
ASIOError ProxyASIODriver::stop()                    {
    if (m_shm) { m_shm->active = 0; _ReadWriteBarrier(); }
    return m_real->stop();
}
ASIOError ProxyASIODriver::getChannels(long *ni, long *no)
    { return m_real->getChannels(ni, no); }
ASIOError ProxyASIODriver::getLatencies(long *il, long *ol)
    { return m_real->getLatencies(il, ol); }
ASIOError ProxyASIODriver::getBufferSize(long *mn, long *mx, long *pref, long *gran)
    { return m_real->getBufferSize(mn, mx, pref, gran); }
ASIOError ProxyASIODriver::canSampleRate(ASIOSampleRate sr)
    { return m_real->canSampleRate(sr); }
ASIOError ProxyASIODriver::getSampleRate(ASIOSampleRate *sr)
    { return m_real->getSampleRate(sr); }
ASIOError ProxyASIODriver::setSampleRate(ASIOSampleRate sr)
    { return m_real->setSampleRate(sr); }
ASIOError ProxyASIODriver::getClockSources(ASIOClockSource *c, long *n)
    { return m_real->getClockSources(c, n); }
ASIOError ProxyASIODriver::setClockSource(long ref)
    { return m_real->setClockSource(ref); }
ASIOError ProxyASIODriver::getSamplePosition(ASIOSamples *p, ASIOTimeStamp *t)
    { return m_real->getSamplePosition(p, t); }
ASIOError ProxyASIODriver::getChannelInfo(ASIOChannelInfo *info)
    { return m_real->getChannelInfo(info); }
ASIOError ProxyASIODriver::controlPanel()
    { return m_real->controlPanel(); }
ASIOError ProxyASIODriver::future(long sel, void *opt)
    { return m_real->future(sel, opt); }
ASIOError ProxyASIODriver::outputReady()
{
    // If the DAW calls outputReady(), it means it fills output buffers
    // asynchronously AFTER returning from bufferSwitch. We must wait
    // until this call to read the output data.
    if (!m_dawUsesOutputReady) {
        m_dawUsesOutputReady = true;
        ProxyLog("outputReady: DAW uses outputReady — deferring ring writes\r\n");
    }
    if (m_pendingIndex >= 0) {
        writeOutputsToRing(m_pendingIndex);
        m_pendingIndex = -1;
    }
    return m_real->outputReady();
}

// ---------------------------------------------------------------------------
// createBuffers — the key interception point
// ---------------------------------------------------------------------------

static ASIOCallbacks s_proxyCallbacks = {
    ProxyTrampoline::bufferSwitch,
    ProxyTrampoline::sampleRateChanged,
    ProxyTrampoline::asioMessage,
    ProxyTrampoline::bufferSwitchTimeInfo
};

ASIOError ProxyASIODriver::createBuffers(ASIOBufferInfo *infos, long numChannels,
                                          long bufferSize, ASIOCallbacks *callbacks)
{
    // Save the DAW's callbacks so we can call through after capturing
    m_dawCallbacks = *callbacks;

    // Save buffer layout references
    m_bufInfos     = infos;
    m_numChannels  = numChannels;
    m_bufferFrames = bufferSize;

    // Tell the real driver to use OUR trampoline callbacks
    ASIOError err = m_real->createBuffers(infos, numChannels, bufferSize, &s_proxyCallbacks);
    if (err != ASE_OK) return err;

    // Figure out which channels are outputs and query their sample format
    m_numOut = 0;
    for (long i = 0; i < numChannels && m_numOut < PROXY_MAX_OUT; ++i) {
        if (!infos[i].isInput) {
            m_outIdx[m_numOut] = i;

            ASIOChannelInfo ci{};
            ci.channel = infos[i].channelNum;
            ci.isInput = ASIOFalse;
            m_real->getChannelInfo(&ci);
            m_outType[m_numOut] = ci.type;

            ++m_numOut;
        }
    }

    ProxyLog("createBuffers: numChannels=%ld bufSize=%ld outChannelsFound=%ld shm=%p\r\n",
             numChannels, bufferSize, m_numOut, m_shm);

    // Log full channel layout so we can verify output detection
    for (long i = 0; i < numChannels; ++i) {
        ASIOChannelInfo ci{};
        ci.channel = infos[i].channelNum;
        ci.isInput = infos[i].isInput;
        m_real->getChannelInfo(&ci);
        ProxyLog("  ch[%ld] channelNum=%ld isInput=%ld name='%s' type=%d buf[0]=%p buf[1]=%p\r\n",
                 i, infos[i].channelNum, (long)infos[i].isInput,
                 ci.name, (int)ci.type,
                 infos[i].buffers[0], infos[i].buffers[1]);
    }
    for (long i = 0; i < m_numOut; ++i) {
        ProxyLog("  outIdx[%ld] = bufInfos[%d] type=%d\r\n",
                 i, m_outIdx[i], (int)m_outType[i]);
    }

    // Write metadata into shared memory so OBS knows what to expect
    if (m_shm) {
        ASIOSampleRate sr = 0;
        m_real->getSampleRate(&sr);
        m_shm->sampleRate    = static_cast<uint32_t>(sr);
        m_shm->numChannels   = static_cast<uint32_t>(m_numOut);
        m_shm->bufferFrames  = static_cast<uint32_t>(bufferSize);
        m_shm->writePos      = 0;
        m_shm->readPos       = 0;
        _ReadWriteBarrier();
        m_shm->active        = 1;
        ProxyLog("createBuffers: SHM active=1 sampleRate=%u numChannels=%u\r\n",
                 m_shm->sampleRate, m_shm->numChannels);
    } else {
        ProxyLog("createBuffers: WARNING m_shm is null — shared memory not open!\r\n");
    }

    return ASE_OK;
}

ASIOError ProxyASIODriver::disposeBuffers()
{
    if (m_shm) { m_shm->active = 0; _ReadWriteBarrier(); }
    m_bufInfos    = nullptr;
    m_numChannels = 0;
    m_numOut      = 0;
    return m_real->disposeBuffers();
}

// ---------------------------------------------------------------------------
// Buffer switch — called by the real ASIO driver on its high-priority thread
// ---------------------------------------------------------------------------

void ProxyASIODriver::onBufferSwitch(long index, ASIOBool direct)
{
    static long callCount = 0;
    if (++callCount <= 3)
        ProxyLog("bufferSwitch #%ld index=%ld active=%d writePos=%lld\r\n",
                 callCount, index,
                 m_shm ? (int)m_shm->active : -1,
                 m_shm ? m_shm->writePos : -1);

    // Call the DAW's callback — it fills the output buffers
    if (m_dawCallbacks.bufferSwitch)
        m_dawCallbacks.bufferSwitch(index, direct);

    if (m_dawUsesOutputReady) {
        // DAW fills buffers asynchronously; defer read until outputReady()
        m_pendingIndex = index;
    } else {
        // DAW fills buffers synchronously in the callback; read now
        writeOutputsToRing(index);
    }
}

ASIOTime *ProxyASIODriver::onBufferSwitchTimeInfo(ASIOTime *t, long index, ASIOBool direct)
{
    ASIOTime *ret = nullptr;
    if (m_dawCallbacks.bufferSwitchTimeInfo)
        ret = m_dawCallbacks.bufferSwitchTimeInfo(t, index, direct);
    else if (m_dawCallbacks.bufferSwitch)
        m_dawCallbacks.bufferSwitch(index, direct);

    if (m_dawUsesOutputReady) {
        m_pendingIndex = index;
    } else {
        writeOutputsToRing(index);
    }
    return ret;
}

void ProxyASIODriver::onSampleRateChanged(ASIOSampleRate sr)
{
    if (m_shm) {
        m_shm->sampleRate = static_cast<uint32_t>(sr);
        _ReadWriteBarrier();
    }
    if (m_dawCallbacks.sampleRateDidChange)
        m_dawCallbacks.sampleRateDidChange(sr);
}

long ProxyASIODriver::onAsioMessage(long sel, long val, void *msg, double *opt)
{
    if (m_dawCallbacks.asioMessage)
        return m_dawCallbacks.asioMessage(sel, val, msg, opt);
    return 0;
}

// ---------------------------------------------------------------------------
// writeOutputsToRing — copies output channel buffers into the shared ring
// ---------------------------------------------------------------------------

void ProxyASIODriver::writeOutputsToRing(long index)
{
    if (!m_shm || !m_shm->active || m_numOut == 0 || !m_bufInfos) return;

    const long  frames   = m_bufferFrames;
    const long  nch      = m_numOut;
    const uint32_t mask  = RING_FRAMES - 1;

    int64_t writePos = m_shm->writePos; // read current write head

    for (long f = 0; f < frames; ++f) {
        uint32_t slot = static_cast<uint32_t>((writePos + f) & mask);
        float *dst = &m_shm->data[slot * DAW_MAX_CHANNELS];

        for (long ch = 0; ch < nch; ++ch) {
            const int    bi  = m_outIdx[ch];
            const void  *src = static_cast<const uint8_t*>(m_bufInfos[bi].buffers[index])
                               + f * sampleByteSize(m_outType[ch]);
            dst[ch] = sampleToFloat(src, m_outType[ch]);
        }
        // Zero out unused channel slots so OBS doesn't read garbage
        for (long ch = nch; ch < (long)DAW_MAX_CHANNELS; ++ch)
            dst[ch] = 0.0f;
    }

    // Memory barrier then advance write pointer — OBS polls this
    _ReadWriteBarrier();
    m_shm->writePos = writePos + frames;

    // Optionally signal the OBS event so it can wake up immediately
    if (m_eventHandle)
        SetEvent(m_eventHandle);

#if PROXY_DEBUG_AUDIO_LOG
    {
        static DWORD s_lastLogMs = 0;
        DWORD now = GetTickCount();
        if (now - s_lastLogMs >= 20) {
            s_lastLogMs = now;
            // Find peak absolute value across all frames of ch0 in the ASIO buffer
            float peak = 0.0f;
            if (nch >= 1 && m_bufInfos) {
                const int   bi  = m_outIdx[0];
                const void *buf = m_bufInfos[bi].buffers[index];
                if (buf) {
                    int bsz = sampleByteSize(m_outType[0]);
                    for (long f = 0; f < frames; ++f) {
                        float v = sampleToFloat(
                            static_cast<const uint8_t*>(buf) + f * bsz,
                            m_outType[0]);
                        if (v > peak) peak = v;
                        if (-v > peak) peak = -v;
                    }
                }
            }
            // Also check what's in the ring at the slot we just wrote
            float ringPeak = 0.0f;
            if (m_shm) {
                for (long f = 0; f < frames; ++f) {
                    uint32_t slot = static_cast<uint32_t>((writePos + f) & mask);
                    float v = m_shm->data[slot * DAW_MAX_CHANNELS];
                    if (v > ringPeak) ringPeak = v;
                    if (-v > ringPeak) ringPeak = -v;
                }
            }
            ProxyLog("[AUDIO] t=%lu nch=%ld frames=%ld asioPeak=%.6f ringPeak=%.6f wpos=%lld\r\n",
                     now, nch, frames, peak, ringPeak, m_shm ? m_shm->writePos : -1LL);
        }
    }
#endif
}

// ---------------------------------------------------------------------------
// Sample format conversion helpers
// ---------------------------------------------------------------------------

static int sampleByteSize(ASIOSampleType t)
{
    switch (t) {
        case ASIOSTInt16LSB: case ASIOSTInt16MSB: return 2;
        case ASIOSTInt24LSB: case ASIOSTInt24MSB: return 3;
        case ASIOSTInt32LSB: case ASIOSTInt32MSB:
        case ASIOSTInt32LSB16: case ASIOSTInt32LSB18:
        case ASIOSTInt32LSB20: case ASIOSTInt32LSB24:
        case ASIOSTFloat32LSB: case ASIOSTFloat32MSB: return 4;
        case ASIOSTFloat64LSB: case ASIOSTFloat64MSB: return 8;
        default: return 4;
    }
}

float ProxyASIODriver::sampleToFloat(const void *src, ASIOSampleType type) const
{
    switch (type) {
    case ASIOSTFloat32LSB:
        return *static_cast<const float*>(src);

    case ASIOSTFloat64LSB:
        return static_cast<float>(*static_cast<const double*>(src));

    case ASIOSTInt32LSB: {
        int32_t v; memcpy(&v, src, 4);
        return v * (1.0f / 2147483648.0f);
    }
    case ASIOSTInt32LSB24: {
        // 24-bit value in the upper 24 bits of a 32-bit word
        int32_t v; memcpy(&v, src, 4);
        return (v >> 8) * (1.0f / 8388608.0f);
    }
    case ASIOSTInt24LSB: {
        // 3-byte packed little-endian
        const uint8_t *b = static_cast<const uint8_t*>(src);
        int32_t v = b[0] | (b[1] << 8) | (b[2] << 16);
        if (v & 0x800000) v |= 0xFF000000; // sign extend
        return v * (1.0f / 8388608.0f);
    }
    case ASIOSTInt16LSB: {
        int16_t v; memcpy(&v, src, 2);
        return v * (1.0f / 32768.0f);
    }
    // MSB (big-endian) variants — byteswap then same as LSB
    case ASIOSTFloat32MSB: {
        uint32_t raw; memcpy(&raw, src, 4);
        raw = _byteswap_ulong(raw);
        float v; memcpy(&v, &raw, 4);
        return v;
    }
    case ASIOSTInt32MSB: {
        uint32_t raw; memcpy(&raw, src, 4);
        raw = _byteswap_ulong(raw);
        int32_t v; memcpy(&v, &raw, 4);
        return v * (1.0f / 2147483648.0f);
    }
    case ASIOSTInt16MSB: {
        uint16_t raw; memcpy(&raw, src, 2);
        raw = _byteswap_ushort(raw);
        int16_t v; memcpy(&v, &raw, 2);
        return v * (1.0f / 32768.0f);
    }
    default:
        return 0.0f;
    }
}

// ---------------------------------------------------------------------------
// Shared memory management
// ---------------------------------------------------------------------------

void ProxyASIODriver::openSharedMemory()
{
    ProxyLog("openSharedMemory: attempting...\r\n");
    // Try to open existing mapping first (OBS may have created it)
    m_shmHandle = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, DAW_CAPTURE_SHM_NAME);
    if (!m_shmHandle) {
        // Create it ourselves — OBS will open it when it starts
        m_shmHandle = CreateFileMappingW(
            INVALID_HANDLE_VALUE, nullptr,
            PAGE_READWRITE, 0, sizeof(DAWCaptureShm),
            DAW_CAPTURE_SHM_NAME);
    }
    if (!m_shmHandle) return;

    m_shm = static_cast<DAWCaptureShm*>(
        MapViewOfFile(m_shmHandle, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(DAWCaptureShm)));

    ProxyLog("openSharedMemory: handle=%p mapped=%p lastErr=%lu\r\n",
             m_shmHandle, m_shm, GetLastError());

    // Create (or open) event so OBS can optionally wait on it
    m_eventHandle = CreateEventW(nullptr, FALSE, FALSE, DAW_CAPTURE_EVENT_NAME);
}

void ProxyASIODriver::closeSharedMemory()
{
    if (m_shm) { UnmapViewOfFile(m_shm); m_shm = nullptr; }
    if (m_shmHandle) { CloseHandle(m_shmHandle); m_shmHandle = nullptr; }
    if (m_eventHandle) { CloseHandle(m_eventHandle); m_eventHandle = nullptr; }
}

// ---------------------------------------------------------------------------
// Trampoline callbacks (static — called by the real ASIO driver)
// ---------------------------------------------------------------------------

namespace ProxyTrampoline {
    void CALLBACK bufferSwitch(long index, ASIOBool direct) {
        if (ProxyASIODriver::instance())
            ProxyASIODriver::instance()->onBufferSwitch(index, direct);
    }
    ASIOTime *CALLBACK bufferSwitchTimeInfo(ASIOTime *t, long index, ASIOBool direct) {
        if (ProxyASIODriver::instance())
            return ProxyASIODriver::instance()->onBufferSwitchTimeInfo(t, index, direct);
        return t;
    }
    void CALLBACK sampleRateChanged(ASIOSampleRate sr) {
        if (ProxyASIODriver::instance())
            ProxyASIODriver::instance()->onSampleRateChanged(sr);
    }
    long CALLBACK asioMessage(long sel, long val, void *msg, double *opt) {
        if (ProxyASIODriver::instance())
            return ProxyASIODriver::instance()->onAsioMessage(sel, val, msg, opt);
        return 0;
    }
}
