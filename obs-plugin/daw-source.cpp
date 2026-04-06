// daw-source.cpp
// OBS audio source: installs a per-user COM redirect so asio-proxy.dll
// intercepts the DAW's ASIO driver, captures its output buffers into shared
// memory, and feeds OBS with ~40ms lag. Pure DAW software output — no
// hardware mix bleed, no input monitoring.

#include <windows.h>
#include <objbase.h>
#include <obs-module.h>
#include <media-io/audio-io.h>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>

#include "../shared/shared-memory.hpp"
#include "asio-registry.hpp"

// OBS logging helpers
#define DAWLOG(level, fmt, ...) \
    blog(level, "[DAW Capture] " fmt, ##__VA_ARGS__)

// ---------------------------------------------------------------------------
// Timestamp
// ---------------------------------------------------------------------------

static uint64_t hw_time_ns()
{
    static LARGE_INTEGER freq = {};
    if (!freq.QuadPart) QueryPerformanceFrequency(&freq);
    LARGE_INTEGER count;
    QueryPerformanceCounter(&count);
    return (uint64_t)count.QuadPart * 1000000000ULL / (uint64_t)freq.QuadPart;
}

// ---------------------------------------------------------------------------
// Per-source state
// ---------------------------------------------------------------------------

struct DAWSource {
    obs_source_t  *obsSource      = nullptr;
    HANDLE         shmHandle      = nullptr;
    DAWCaptureShm *shm            = nullptr;
    HANDLE         dataEvent      = nullptr;
    GUID           driverClsid    = {};
    bool           proxyInstalled = false;
    bool           synced         = false;
    int64_t        localReadPos   = 0;
    HANDLE         thread         = nullptr;
    volatile LONG  threadRunning  = 0;
    int            outputPair     = 0;   // 0=ch0-1, 1=ch2-3, 2=ch4-5, 3=ch6-7
    uint64_t       audioTimestamp = 0;   // monotonic timestamp based on sample count
};

// ---------------------------------------------------------------------------
// Shared memory
// ---------------------------------------------------------------------------

static bool OpenShm(DAWSource *s)
{
    if (s->shm) return true;

    s->shmHandle = OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, DAW_CAPTURE_SHM_NAME);
    if (!s->shmHandle)
        s->shmHandle = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr,
                           PAGE_READWRITE, 0, (DWORD)sizeof(DAWCaptureShm),
                           DAW_CAPTURE_SHM_NAME);
    if (!s->shmHandle) return false;

    s->shm = static_cast<DAWCaptureShm*>(
        MapViewOfFile(s->shmHandle, FILE_MAP_ALL_ACCESS, 0, 0, sizeof(DAWCaptureShm)));

    s->dataEvent = CreateEventW(nullptr, FALSE, FALSE, DAW_CAPTURE_EVENT_NAME);
    return s->shm != nullptr;
}

static void CloseShm(DAWSource *s)
{
    if (s->shm)       { UnmapViewOfFile(s->shm);    s->shm = nullptr; }
    if (s->shmHandle) { CloseHandle(s->shmHandle);  s->shmHandle = nullptr; }
    if (s->dataEvent) { CloseHandle(s->dataEvent);  s->dataEvent = nullptr; }
}

// ---------------------------------------------------------------------------
// Proxy DLL path — same folder as this plugin DLL
// ---------------------------------------------------------------------------

static std::wstring ProxyDllPath()
{
    wchar_t path[MAX_PATH] = {};
    HMODULE hSelf = nullptr;
    GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                       GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                       reinterpret_cast<LPCWSTR>(&ProxyDllPath), &hSelf);
    GetModuleFileNameW(hSelf, path, MAX_PATH);
    wchar_t *slash = wcsrchr(path, L'\\');
    if (slash)
        wcscpy_s(slash + 1, MAX_PATH - (DWORD)(slash - path) - 1,
                 L"asio-proxy.dll");
    return path;
}

// ---------------------------------------------------------------------------
// Install / remove proxy redirect for the selected driver
// ---------------------------------------------------------------------------

static void InstallProxy(DAWSource *s, const GUID &clsid)
{
    if (IsEqualGUID(clsid, GUID_NULL)) return;
    s->driverClsid = clsid;

    std::wstring clsidStr = GUIDToString(clsid);
    char clsidA[64] = {};
    WideCharToMultiByte(CP_UTF8, 0, clsidStr.c_str(), -1, clsidA, 64, nullptr, nullptr);

    if (!IsProxyInstalled(clsid)) {
        DAWLOG(LOG_INFO, "Proxy not installed for %s — installing now", clsidA);

        auto drivers = EnumerateASIODrivers();
        bool found = false;
        for (auto &d : drivers) {
            if (IsEqualGUID(d.clsid, clsid)) {
                found = true;
                char dllA[MAX_PATH] = {};
                WideCharToMultiByte(CP_UTF8, 0, d.dllPath.c_str(), -1,
                                    dllA, MAX_PATH, nullptr, nullptr);

                if (d.dllPath.empty()) {
                    DAWLOG(LOG_WARNING,
                        "Driver %s found in HKLM\\SOFTWARE\\ASIO but its "
                        "InprocServer32 DLL path is EMPTY — "
                        "check HKLM\\SOFTWARE\\Classes\\CLSID\\%s\\InprocServer32",
                        clsidA, clsidA);
                }

                std::wstring proxyPath = ProxyDllPath();
                char proxyA[MAX_PATH] = {};
                WideCharToMultiByte(CP_UTF8, 0, proxyPath.c_str(), -1,
                                    proxyA, MAX_PATH, nullptr, nullptr);
                DAWLOG(LOG_INFO, "Proxy DLL path: %s", proxyA);
                DAWLOG(LOG_INFO, "Original DLL path: %s", dllA);

                s->proxyInstalled = InstallProxyRedirect(d, proxyPath);
                if (s->proxyInstalled) {
                    DAWLOG(LOG_INFO, "Proxy redirect installed OK for %s", clsidA);
                } else {
                    DAWLOG(LOG_ERROR,
                        "InstallProxyRedirect FAILED for %s — "
                        "check that OBS is not running as a different user "
                        "and that HKCU\\SOFTWARE\\Classes\\CLSID is writable",
                        clsidA);
                }
                break;
            }
        }
        if (!found) {
            DAWLOG(LOG_WARNING,
                "CLSID %s not found in HKLM\\SOFTWARE\\ASIO driver list",
                clsidA);
        }
    } else {
        DAWLOG(LOG_INFO, "Proxy already installed for %s", clsidA);
        s->proxyInstalled = true;
    }
}

// ---------------------------------------------------------------------------
// Audio output thread — reads the shared ring (tape playback head)
// ---------------------------------------------------------------------------

static DWORD WINAPI AudioThread(LPVOID param)
{
    DAWSource *s = static_cast<DAWSource*>(param);
    std::vector<float> planeL, planeR;
    const uint8_t *planePtrs[MAX_AV_PLANES] = {};

    planeL.resize(READ_CHUNK_FRAMES);
    planeR.resize(READ_CHUNK_FRAMES);

    while (InterlockedCompareExchange(&s->threadRunning, 1, 1)) {

        if (s->dataEvent)
            WaitForSingleObject(s->dataEvent, 20);
        else
            Sleep(10);

        if (!s->shm || !s->shm->active) {
            s->synced = false;
            s->audioTimestamp = 0;
            continue;
        }

        const uint32_t sr  = s->shm->sampleRate;
        const uint32_t nch = s->shm->numChannels;
        if (sr == 0 || nch == 0 || nch > DAW_MAX_CHANNELS) continue;

        // Map the selected output pair to absolute channel indices
        const uint32_t pair     = (uint32_t)s->outputPair;
        const uint32_t chL      = pair * 2;
        const uint32_t chR      = pair * 2 + 1;
        const uint32_t srcChL   = (chL < nch) ? chL : 0;
        const uint32_t srcChR   = (chR < nch) ? chR : (nch > 1 ? 1 : 0);

        // Compute target lag in frames from sample rate (~40ms at any rate)
        const uint32_t targetLagFrames = (sr * TARGET_LAG_MS) / 1000;

        // Memory fence before reading writePos to see the latest value
        _ReadWriteBarrier();
        int64_t writePos = s->shm->writePos;
        int64_t gap = writePos - s->localReadPos;

        // Re-sync if: not yet synced, reader too far behind (writer lapped us),
        // or reader somehow ahead of writer
        if (!s->synced || gap > (int64_t)(RING_FRAMES - targetLagFrames) || gap < 0) {
            s->localReadPos = writePos - (int64_t)targetLagFrames;
            s->synced = true;
            // Reset timestamp on re-sync so OBS starts fresh
            s->audioTimestamp = 0;
            gap = targetLagFrames;
        }

        // Drain all available chunks in a loop — don't leave data piling up.
        // This prevents the reader from falling behind and causing timestamp
        // jumps when it catches up in bursts.
        const uint32_t mask = RING_FRAMES - 1;
        int chunksRead = 0;
        const int MAX_CHUNKS_PER_WAKE = 16; // safety limit

        while (gap >= (int64_t)READ_CHUNK_FRAMES && chunksRead < MAX_CHUNKS_PER_WAKE) {

            for (uint32_t f = 0; f < READ_CHUNK_FRAMES; ++f) {
                uint32_t     slot = (uint32_t)((s->localReadPos + f) & mask);
                const float *src  = &s->shm->data[slot * DAW_MAX_CHANNELS];
                planeL[f] = src[srcChL];
                planeR[f] = src[srcChR];
            }
            s->localReadPos += READ_CHUNK_FRAMES;

            memset(planePtrs, 0, sizeof(planePtrs));
            planePtrs[0] = reinterpret_cast<const uint8_t*>(planeL.data());
            planePtrs[1] = reinterpret_cast<const uint8_t*>(planeR.data());

            // Use a monotonic timestamp derived from sample count.
            // This eliminates pitch glitches caused by thread scheduling jitter.
            // On first chunk (or after re-sync), seed from wall clock.
            if (s->audioTimestamp == 0)
                s->audioTimestamp = hw_time_ns();

            obs_source_audio frame = {};
            for (int i = 0; i < MAX_AV_PLANES; ++i) frame.data[i] = planePtrs[i];
            frame.frames          = READ_CHUNK_FRAMES;
            frame.format          = AUDIO_FORMAT_FLOAT_PLANAR;
            frame.speakers        = SPEAKERS_STEREO;
            frame.samples_per_sec = sr;
            frame.timestamp       = s->audioTimestamp;

            obs_source_output_audio(s->obsSource, &frame);

            // Advance timestamp by exact sample duration — never drifts
            s->audioTimestamp += (uint64_t)READ_CHUNK_FRAMES * 1000000000ULL / sr;

            gap -= READ_CHUNK_FRAMES;
            ++chunksRead;
        }
    }
    return 0;
}

// ---------------------------------------------------------------------------
// OBS source callbacks
// ---------------------------------------------------------------------------

static const char *daw_source_name(void *) { return "DAW Audio Capture (ASIO)"; }

static void *daw_source_create(obs_data_t *settings, obs_source_t *source)
{
    auto *s = new DAWSource{};
    s->obsSource = source;

    OpenShm(s);

    s->outputPair = (int)obs_data_get_int(settings, "output_pair");

    const char *clsidUtf8 = obs_data_get_string(settings, "driver_clsid");
    if (clsidUtf8 && clsidUtf8[0]) {
        DAWLOG(LOG_INFO, "daw_source_create: saved CLSID = %s  pair=%d",
               clsidUtf8, s->outputPair);
        wchar_t clsidW[64];
        MultiByteToWideChar(CP_UTF8, 0, clsidUtf8, -1, clsidW, 64);
        GUID clsid;
        if (SUCCEEDED(CLSIDFromString(clsidW, &clsid)))
            InstallProxy(s, clsid);
    } else {
        DAWLOG(LOG_INFO, "daw_source_create: no saved CLSID — select a driver in Properties");
    }

    s->threadRunning = 1;
    s->thread = CreateThread(nullptr, 0, AudioThread, s, 0, nullptr);
    return s;
}

static void daw_source_destroy(void *data)
{
    auto *s = static_cast<DAWSource*>(data);
    InterlockedExchange(&s->threadRunning, 0);
    if (s->dataEvent) SetEvent(s->dataEvent);
    if (s->thread) { WaitForSingleObject(s->thread, 3000); CloseHandle(s->thread); }
    if (s->proxyInstalled) RemoveProxyRedirect(s->driverClsid);
    CloseShm(s);
    delete s;
}

static void daw_source_update(void *data, obs_data_t *settings)
{
    auto *s = static_cast<DAWSource*>(data);
    s->synced = false;

    const char *clsidUtf8 = obs_data_get_string(settings, "driver_clsid");
    if (!clsidUtf8 || !clsidUtf8[0]) {
        DAWLOG(LOG_INFO, "daw_source_update: no CLSID in settings");
        return;
    }

    s->outputPair = (int)obs_data_get_int(settings, "output_pair");
    DAWLOG(LOG_INFO, "daw_source_update: CLSID = %s  pair=%d", clsidUtf8, s->outputPair);

    wchar_t clsidW[64];
    MultiByteToWideChar(CP_UTF8, 0, clsidUtf8, -1, clsidW, 64);
    GUID newClsid;
    if (FAILED(CLSIDFromString(clsidW, &newClsid))) {
        DAWLOG(LOG_ERROR, "daw_source_update: CLSIDFromString failed for: %s", clsidUtf8);
        return;
    }

    if (!IsEqualGUID(newClsid, s->driverClsid)) {
        if (s->proxyInstalled) {
            DAWLOG(LOG_INFO, "daw_source_update: driver changed — removing old proxy");
            RemoveProxyRedirect(s->driverClsid);
            s->proxyInstalled = false;
        }
    }
    InstallProxy(s, newClsid);
}

// ---------------------------------------------------------------------------
// Properties — driver modified callback
// Fires immediately when the user changes the driver dropdown (before Apply).
// ---------------------------------------------------------------------------

static bool on_driver_changed(obs_properties_t *props, obs_property_t *,
                               obs_data_t *settings)
{
    auto *s = static_cast<DAWSource*>(obs_properties_get_param(props));
    if (!s) return true;

    const char *clsidUtf8 = obs_data_get_string(settings, "driver_clsid");
    if (!clsidUtf8 || !clsidUtf8[0]) return true;

    wchar_t clsidW[64];
    MultiByteToWideChar(CP_UTF8, 0, clsidUtf8, -1, clsidW, 64);
    GUID newClsid;
    if (FAILED(CLSIDFromString(clsidW, &newClsid))) return true;

    if (!IsEqualGUID(newClsid, s->driverClsid)) {
        if (s->proxyInstalled) {
            RemoveProxyRedirect(s->driverClsid);
            s->proxyInstalled = false;
        }
    }
    InstallProxy(s, newClsid);

    // Update the status text
    obs_property_t *statusProp = obs_properties_get(props, "proxy_status");
    if (statusProp) {
        const char *statusText = s->proxyInstalled
            ? "Proxy: ACTIVE — start your DAW now"
            : "Proxy: FAILED — check OBS log for details";
        obs_property_set_description(statusProp, statusText);
    }

    return true; // refresh properties display
}

static obs_properties_t *daw_source_get_properties(void *data)
{
    obs_properties_t *props = obs_properties_create();
    obs_properties_set_param(props, data, nullptr);

    obs_property_t *list = obs_properties_add_list(
        props, "driver_clsid", "ASIO Driver",
        OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
    obs_property_list_add_string(list, "(select a driver)", "");

    auto drivers = EnumerateASIODrivers();
    for (auto &d : drivers) {
        std::wstring clsidW = GUIDToString(d.clsid);
        char clsidA[64] = {}, nameA[256] = {}, dllA[MAX_PATH] = {};
        WideCharToMultiByte(CP_UTF8, 0, clsidW.c_str(), -1, clsidA, 64, nullptr, nullptr);
        WideCharToMultiByte(CP_UTF8, 0, d.name.c_str(), -1, nameA, 256, nullptr, nullptr);
        WideCharToMultiByte(CP_UTF8, 0, d.dllPath.c_str(), -1, dllA, MAX_PATH, nullptr, nullptr);
        obs_property_list_add_string(list, nameA, clsidA);
        DAWLOG(LOG_INFO, "ASIO driver: %s  %s  dll=%s", nameA, clsidA,
               dllA[0] ? dllA : "(path not found)");
    }

    obs_property_set_modified_callback(list, on_driver_changed);

    // Output pair selector — pick which stereo pair from the DAW output to send to OBS
    obs_property_t *pairList = obs_properties_add_list(
        props, "output_pair", "Output Pair",
        OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
    obs_property_list_add_int(pairList, "Out 1-2  (Master / Default)", 0);
    obs_property_list_add_int(pairList, "Out 3-4",  1);
    obs_property_list_add_int(pairList, "Out 5-6",  2);
    obs_property_list_add_int(pairList, "Out 7-8",  3);

    // Determine current proxy state for status display
    const char *statusText = "Proxy: not configured — select a driver above";
    if (auto *s = static_cast<DAWSource*>(data)) {
        if (s->proxyInstalled)
            statusText = "Proxy: ACTIVE — start your DAW now";
        else if (!IsEqualGUID(s->driverClsid, GUID_NULL))
            statusText = "Proxy: FAILED — check OBS log (Help > Log Files)";
    }

    obs_properties_add_text(props, "proxy_status", statusText, OBS_TEXT_INFO);

    obs_properties_add_text(props, "info",
        "Start OBS first, select your ASIO driver, then start your DAW.\n"
        "Set Audio Monitoring to 'Monitor Off' so only the stream hears it.",
        OBS_TEXT_INFO);

    return props;
}

static void daw_source_get_defaults(obs_data_t *settings)
{
    obs_data_set_default_string(settings, "driver_clsid", "");
    obs_data_set_default_int(settings, "output_pair", 0);
}

// ---------------------------------------------------------------------------
// Source info
// ---------------------------------------------------------------------------

struct obs_source_info daw_capture_source_info = {
    .id             = "daw_asio_capture",
    .type           = OBS_SOURCE_TYPE_INPUT,
    .output_flags   = OBS_SOURCE_AUDIO | OBS_SOURCE_DO_NOT_DUPLICATE,
    .get_name       = daw_source_name,
    .create         = daw_source_create,
    .destroy        = daw_source_destroy,
    .get_defaults   = daw_source_get_defaults,
    .get_properties = daw_source_get_properties,
    .update         = daw_source_update,
    .icon_type      = OBS_ICON_TYPE_AUDIO_OUTPUT,
};
