// daw-source.cpp
// OBS audio source: installs a per-user COM redirect so asio-proxy.dll
// intercepts the DAW's ASIO driver, captures its output buffers into shared
// memory, and feeds OBS with ~40ms lag. Pure DAW software output — no
// hardware mix bleed, no input monitoring.

#include <windows.h>
#include <objbase.h>
#include <obs-module.h>
#include <util/platform.h>
#include <media-io/audio-io.h>
#include <cstring>
#include <cstdint>
#include <cstdio>
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

// OBS expects obs_source_audio.timestamp in the same timebase as
// os_gettime_ns() — nanoseconds since the Unix epoch. QueryPerformanceCounter
// counts from boot, which is a different epoch entirely; feeding that to
// obs_source_output_audio makes the mixer treat every frame as decades stale
// and drop it, so nothing reaches the output (stream or monitoring).
static uint64_t hw_time_ns()
{
    return os_gettime_ns();
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
    int            captureLagMs   = 4;   // read lag behind the writer, in ms
    uint64_t       audioTimestamp = 0;   // monotonic timestamp based on sample count
    float          diagPeak       = 0.0f;   // peak handed to OBS, for logging
    uint64_t       diagLastLogNs  = 0;
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

        // Short wake-up: the proxy signals on every ASIO buffer, but if a
        // signal is ever missed/coalesced we must not sleep longer than a
        // fraction of the target lag. 2ms keeps the worst case tiny while
        // costing nothing (WaitForSingleObject blocks, it does not spin).
        if (s->dataEvent)
            WaitForSingleObject(s->dataEvent, 2);
        else
            Sleep(2);

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

        // Configurable target lag (ms) — the reader stays pinned this far
        // behind the writer so the delay is constant, never growing.
        // Cached: updated on create/update, never queried from the audio
        // thread (keeps the hot path free of OBS data lookups).
        const int lagMs = s->captureLagMs < 0 ? 0 : s->captureLagMs;
        const int64_t targetLagFrames =
            (int64_t)(sr * (uint32_t)lagMs) / 1000;

        // Snap threshold must scale with the writer's own ASIO buffer: the
        // reader trails the head by roughly one buffer plus one chunk, so a
        // fixed READ_CHUNK_FRAMES*4 window would drop audio whenever the ASIO
        // buffer is larger than the chunk. Two buffers of slack is plenty.
        const int64_t snapThreshold =
            (int64_t)s->shm->bufferFrames * 2 + (int64_t)READ_CHUNK_FRAMES * 2;

        _ReadWriteBarrier();
        const int64_t writePos = s->shm->writePos;

        // First lock-on for a streaming session (writer just started)
        if (!s->synced) {
            s->localReadPos = writePos - targetLagFrames;
            s->synced = true;
        } else {
            // Keep the lag pinned. If the writer ever jumps far (new session,
            // big stall, or JAM VOX pause), snap the read head to the target
            // instead of slowly accumulating delay. The timestamp clock is
            // NEVER reset during streaming, so OBS never re-buffers and the
            // perceived latency stays constant.
            const int64_t targetHead = writePos - targetLagFrames;
            const int64_t skip = targetHead - s->localReadPos;
            if (skip > snapThreshold)
                s->localReadPos = targetHead;           // drop stale audio
            else if (skip < -snapThreshold)
                s->localReadPos = writePos;             // wait for writer
        }
        if (s->localReadPos < 0) s->localReadPos = writePos - targetLagFrames;

        // Read chunks until the head reaches the target position. The reader
        // always trails writePos by exactly targetLagFrames (to within one
        // chunk), so the output delay is constant instead of creeping up.
        const uint32_t mask = RING_FRAMES - 1;
        const int64_t targetHead = writePos - targetLagFrames;
        int chunksRead = 0;
        // Safety limit only — the loop exits as soon as it reaches the read
        // head. Sized to drain a full catch-up (a few ASIO buffers) even with
        // the small 64-frame chunks; at 64 frames this covers ~340ms @48k.
        const int MAX_CHUNKS_PER_WAKE = 256;

        while (chunksRead < MAX_CHUNKS_PER_WAKE &&
               s->localReadPos + (int64_t)READ_CHUNK_FRAMES <= targetHead) {

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

            // Diagnostic: track the peak actually handed to OBS so we can
            // tell "capture works" apart from "OBS is playing it".
            for (uint32_t f = 0; f < READ_CHUNK_FRAMES; ++f) {
                float a = planeL[f] < 0.0f ? -planeL[f] : planeL[f];
                float b = planeR[f] < 0.0f ? -planeR[f] : planeR[f];
                if (a > s->diagPeak) s->diagPeak = a;
                if (b > s->diagPeak) s->diagPeak = b;
            }

            // Advance timestamp by exact sample duration — never drifts
            s->audioTimestamp += (uint64_t)READ_CHUNK_FRAMES * 1000000000ULL / sr;

            ++chunksRead;
        }

        // Emit a heartbeat every ~2 s: proves the reader keeps up and shows
        // that timestamps now sit in the Unix-epoch range OBS expects.
        const uint64_t nowNs = os_gettime_ns();
        if (nowNs - s->diagLastLogNs > 2000000000ULL) {
            s->diagLastLogNs = nowNs;
            DAWLOG(LOG_INFO,
                   "AudioThread: chunks=%d readPos=%lld writePos=%lld lag=%dms "
                   "outPeak=%.6f ts=%llu now=%llu",
                   chunksRead, (long long)s->localReadPos,
                   (long long)writePos, s->captureLagMs, (double)s->diagPeak,
                   (unsigned long long)s->audioTimestamp,
                   (unsigned long long)nowNs);
            s->diagPeak = 0.0f;
        }
    }
    return 0;
}

// ---------------------------------------------------------------------------
// OBS source callbacks
// ---------------------------------------------------------------------------

static const char *daw_source_name(void *) { return "DAW Audio Capture (ASIO)"; }

// ---------------------------------------------------------------------------
// Monitoring
// Exposes OBS source monitoring as a property so it can be switched without
// digging through Advanced Audio Properties. Off is the default because the
// intended monitoring path is the ASIO interface itself, at zero latency.
// ---------------------------------------------------------------------------

static void ApplyMonitoring(DAWSource *s)
{
    if (!s || !s->obsSource) return;

    int mode = (int)obs_data_get_int(obs_source_get_settings(s->obsSource),
                                     "monitor_mode");
    if (mode < 0) mode = 0;
    if (mode > 2) mode = 2;

    obs_source_set_monitoring_type(s->obsSource,
                                   (enum obs_monitoring_type)mode);
    DAWLOG(LOG_INFO, "ApplyMonitoring: mode=%d", mode);
}

// ---------------------------------------------------------------------------
// Monitoring device
// OBS keeps the monitoring device as ONE global setting
// (obs_set_audio_monitoring_device) - there is no per-source API - so this
// source drives that global from its own properties. With the ASIO proxy
// routing you normally listen through the interface, but when you do enable
// monitoring you usually want a specific device (headphones) without digging
// through Settings > Audio.
// ---------------------------------------------------------------------------

struct MonQuery {
    bool            listing;    // true: fill a property list
    obs_property_t *prop;
    const char     *wantId;     // find mode: id to look up
    char            foundName[256];
    bool            found;
};

static bool mon_query_cb(void *data, const char *name, const char *id)
{
    auto *q = static_cast<MonQuery *>(data);
    if (q->listing) {
        obs_property_list_add_string(q->prop, name, id);
        return true;
    }
    if (q->wantId && id && strcmp(id, q->wantId) == 0) {
        size_t n = strlen(name);
        if (n >= sizeof(q->foundName)) n = sizeof(q->foundName) - 1;
        memcpy(q->foundName, name, n);
        q->foundName[n] = '\0';
        q->found = true;
        return false;   // stop enumerating
    }
    return true;
}

static void ApplyMonitoringDevice(obs_data_t *settings)
{
    if (!settings) return;
    if (!obs_audio_monitoring_available()) return;

    const char *wantId = obs_data_get_string(settings, "monitoring_device_id");
    if (!wantId || !wantId[0]) return;   // "(keep OBS default)"

    const char *curName = nullptr, *curId = nullptr;
    obs_get_audio_monitoring_device(&curName, &curId);
    if (curId && strcmp(curId, wantId) == 0) return;   // already active

    MonQuery q = {};
    q.wantId = wantId;
    obs_enum_audio_monitoring_devices(mon_query_cb, &q);

    if (!q.found) {
        DAWLOG(LOG_WARNING, "monitoring device id '%s' not found - not applied",
               wantId);
        return;
    }
    if (obs_set_audio_monitoring_device(q.foundName, wantId))
        DAWLOG(LOG_INFO, "monitoring device set to '%s'", q.foundName);
    else
        DAWLOG(LOG_WARNING, "failed to set monitoring device '%s'",
               q.foundName);
}

static void *daw_source_create(obs_data_t *settings, obs_source_t *source)
{
    auto *s = new DAWSource{};
    s->obsSource = source;

    OpenShm(s);

    s->outputPair = (int)obs_data_get_int(settings, "output_pair");
    s->captureLagMs = (int)obs_data_get_int(settings, "capture_lag_ms");
    ApplyMonitoring(s);
    ApplyMonitoringDevice(settings);

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
    ApplyMonitoring(s);
    ApplyMonitoringDevice(settings);

    const char *clsidUtf8 = obs_data_get_string(settings, "driver_clsid");
    if (!clsidUtf8 || !clsidUtf8[0]) {
        DAWLOG(LOG_INFO, "daw_source_update: no CLSID in settings");
        return;
    }

    s->outputPair = (int)obs_data_get_int(settings, "output_pair");
    s->captureLagMs = (int)obs_data_get_int(settings, "capture_lag_ms");
    DAWLOG(LOG_INFO, "daw_source_update: CLSID = %s  pair=%d  lag=%dms",
           clsidUtf8, s->outputPair, s->captureLagMs);

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

static bool on_monitor_device_changed(obs_properties_t *, obs_property_t *,
                                      obs_data_t *settings)
{
    ApplyMonitoringDevice(settings);
    return false;   // nothing to re-render
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

    obs_property_t *lagProp = obs_properties_add_int(
        props, "capture_lag_ms", "Capture lag (ms)",
        0, 250, 1);
    obs_property_int_set_suffix(lagProp, " ms");
    obs_property_set_long_description(lagProp,
        "How far behind the DAW's write head the OBS reader stays "
        "(tape-style cushion). Lower = less latency. 0-20 ms is a good "
        "low-latency range; increase only if you hear stutter/crackle.");

    obs_property_t *monProp = obs_properties_add_list(
        props, "monitor_mode", "Audio Monitoring",
        OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_INT);
    obs_property_list_add_int(monProp, "Monitor Off", 0);
    obs_property_list_add_int(monProp, "Monitor Only (headphones)", 1);
    obs_property_list_add_int(monProp, "Monitor and Output", 2);
    obs_property_set_long_description(monProp,
        "Monitor Off = you monitor the DAW directly through the audio "
        "interface at zero latency (recommended).\n"
        "On = OBS additionally plays the captured copy on the monitoring "
        "device chosen below, delayed by 'Capture lag'. That double-hears "
        "the DAW and adds that delay, so only enable it if you deliberately "
        "want to judge the OBS signal.");

    // --- Monitoring device -------------------------------------------------
    obs_property_t *monDev = obs_properties_add_list(
        props, "monitoring_device_id", "Monitoring Device",
        OBS_COMBO_TYPE_LIST, OBS_COMBO_FORMAT_STRING);
    obs_property_list_add_string(monDev, "(keep OBS default)", "");

    if (obs_audio_monitoring_available()) {
        MonQuery q = {};
        q.listing = true;
        q.prop = monDev;
        obs_enum_audio_monitoring_devices(mon_query_cb, &q);
    } else {
        obs_property_set_enabled(monDev, false);
    }
    obs_property_set_long_description(monDev,
        "Which output device OBS plays the monitored copy on. With the ASIO "
        "proxy routing you normally listen through the interface itself; pick "
        "a device here only when you enable monitoring above.\n"
        "OBS keeps this as ONE global setting, so it applies to every source "
        "with monitoring enabled, not just this one.\n"
        "Ignored while 'Audio Monitoring' is 'Monitor Off'.");
    obs_property_set_modified_callback(monDev, on_monitor_device_changed);

    {   // report what is actually active right now
        const char *curName = nullptr, *curId = nullptr;
        obs_get_audio_monitoring_device(&curName, &curId);
        char monStatus[320];
        if (obs_audio_monitoring_available() && curName && curName[0])
            snprintf(monStatus, sizeof(monStatus),
                     "Active OBS monitoring device: %s", curName);
        else
            snprintf(monStatus, sizeof(monStatus),
                     "Active OBS monitoring device: (none - monitoring "
                     "unavailable or not set)");
        obs_properties_add_text(props, "monitor_status", monStatus,
                                OBS_TEXT_INFO);
    }

    obs_properties_add_text(props, "info",
        "Start OBS first, select your ASIO driver, then start your DAW.\n"
        "Keep 'Audio Monitoring' on Monitor Off: you already monitor the "
        "DAW at zero latency through the interface.\n"
        "Lower 'Capture lag' for minimum latency; raise it if audio stutters.",
        OBS_TEXT_INFO);

    return props;
}

static void daw_source_get_defaults(obs_data_t *settings)
{
    obs_data_set_default_string(settings, "driver_clsid", "");
    obs_data_set_default_int(settings, "output_pair", 0);
    obs_data_set_default_int(settings, "capture_lag_ms", 4);
    obs_data_set_default_int(settings, "monitor_mode", 0);
    obs_data_set_default_string(settings, "monitoring_device_id", "");
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
