#pragma once
#include <windows.h>
#include <cstdint>
#include "../shared/asio-types.hpp"
#include "../shared/shared-memory.hpp"
#include "../shared/direct-monitor.hpp"

// Max output channels we will capture (stereo is typical; grab up to 8)
static constexpr int PROXY_MAX_OUT = DAW_MAX_CHANNELS;

// ProxyASIODriver: implements IASIO by wrapping the real driver.
// Intercepts createBuffers to inject our bufferSwitch hook.
// On each callback: calls the DAW's real callback first (output buffers get
// filled at normal ASIO timing), then copies filled output buffers to shared mem.
class ProxyASIODriver : public IASIO {
public:
    ProxyASIODriver(IASIO *real, HMODULE realDll, const GUID &driverClsid);
    virtual ~ProxyASIODriver();

    // IUnknown
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppv) override;
    ULONG   STDMETHODCALLTYPE AddRef()  override;
    ULONG   STDMETHODCALLTYPE Release() override;

    // IASIO — all forwarded to m_real, with hooks on createBuffers/disposeBuffers/stop
    ASIOBool  init(void *sysHandle) override;
    void      getDriverName(char *name) override;
    long      getDriverVersion() override;
    void      getErrorMessage(char *string) override;
    ASIOError start() override;
    ASIOError stop() override;
    ASIOError getChannels(long *ni, long *no) override;
    ASIOError getLatencies(long *il, long *ol) override;
    ASIOError getBufferSize(long *mn, long *mx, long *pref, long *gran) override;
    ASIOError canSampleRate(ASIOSampleRate sr) override;
    ASIOError getSampleRate(ASIOSampleRate *sr) override;
    ASIOError setSampleRate(ASIOSampleRate sr) override;
    ASIOError getClockSources(ASIOClockSource *clocks, long *n) override;
    ASIOError setClockSource(long ref) override;
    ASIOError getSamplePosition(ASIOSamples *pos, ASIOTimeStamp *ts) override;
    ASIOError getChannelInfo(ASIOChannelInfo *info) override;
    ASIOError createBuffers(ASIOBufferInfo *infos, long numChannels,
                            long bufferSize, ASIOCallbacks *callbacks) override;
    ASIOError disposeBuffers() override;
    ASIOError controlPanel() override;
    ASIOError future(long sel, void *opt) override;
    ASIOError outputReady() override;

    // Called from static trampoline callbacks
    void onBufferSwitch(long index, ASIOBool direct);
    ASIOTime *onBufferSwitchTimeInfo(ASIOTime *t, long index, ASIOBool direct);
    void onSampleRateChanged(ASIOSampleRate sr);
    long onAsioMessage(long sel, long val, void *msg, double *opt);

    // Singleton accessor — ASIO is effectively single-instance per process
    static ProxyASIODriver *instance() { return s_instance; }

private:
    void     openSharedMemory();
    void     closeSharedMemory();
    void     writeOutputsToRing(long index);
    float    sampleToFloat(const void *src, ASIOSampleType type) const;

    IASIO  *m_real;
    HMODULE m_realDll;
    GUID    m_clsid;
    LONG    m_refCount;

    // Buffers registered by the DAW via createBuffers
    ASIOBufferInfo *m_bufInfos;    // pointer to DAW's array (valid while buffers live)
    long            m_numChannels;
    long            m_bufferFrames;

    // Which entries in m_bufInfos are OUTPUT channels
    int  m_outIdx[PROXY_MAX_OUT]; // index into m_bufInfos[]
    long m_numOut;

    // Sample types per output channel (queried after createBuffers)
    ASIOSampleType m_outType[PROXY_MAX_OUT];

    // outputReady() support — if the DAW uses outputReady(), we defer
    // ring writes until outputReady() is called instead of bufferSwitch
    bool m_dawUsesOutputReady = false;
    long m_pendingIndex       = -1;  // buffer index to write when outputReady fires

    // DAW's original callbacks (saved so we can call through)
    ASIOCallbacks m_dawCallbacks;

    // Shared memory (tape)
    HANDLE        m_shmHandle;
    DAWCaptureShm *m_shm;
    HANDLE        m_eventHandle; // signalled when new data arrives

    // Direct monitoring: watek renderujacy proxy (SHM -> WASAPI) - patrz
    // shared/direct-monitor.hpp. Sterowany przez Manager (GUI).
    DirectMonitor m_direct;

    static ProxyASIODriver *s_instance;
};

// Static trampoline callbacks — installed as the ASIO driver's callbacks
// so the real driver calls us, and we forward to the DAW after copying.
namespace ProxyTrampoline {
    void       bufferSwitch(long index, ASIOBool direct);
    ASIOTime * bufferSwitchTimeInfo(ASIOTime *t, long index, ASIOBool direct);
    void       sampleRateChanged(ASIOSampleRate sr);
    long       asioMessage(long sel, long val, void *msg, double *opt);
}
