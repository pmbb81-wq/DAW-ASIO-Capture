#include "direct-monitor.hpp"

#include <mmdeviceapi.h>
#include <audioclient.h>
#include <functiondiscoverykeys_devpkey.h>
#include <avrt.h>
#include <mmreg.h>
#include <cstring>
#include <cstdio>

#ifndef AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM
#define AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM  0x80000000
#endif
#ifndef AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY
#define AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY 0x08000000
#endif

// KSDATAFORMAT_SUBTYPE_IEEE_FLOAT (wbudowane, zeby nie wciagac ksmedia.h)
static const GUID SUBTYPE_FLOAT = {
    0x00000003, 0x0000, 0x0010, {0x80, 0x00, 0x00, 0xaa, 0x00, 0x38, 0x9b, 0x71}};

static void DirectLog(const char *fmt, ...)
{
    wchar_t tmp[MAX_PATH], path[MAX_PATH];
    GetTempPathW(MAX_PATH, tmp);
    swprintf_s(path, L"%sobs-asio-proxy.log", tmp);
    HANDLE f = CreateFileW(path, GENERIC_WRITE, FILE_SHARE_READ, nullptr,
                           OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) return;
    SetFilePointer(f, 0, nullptr, FILE_END);
    char buf[512];
    va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
    DWORD w; WriteFile(f, buf, (DWORD)strlen(buf), &w, nullptr);
    CloseHandle(f);
}

// writePos to volatile int64_t - na x86 (32 bity) odczyt nie jest atomowy,
// wiec czytamy dwa razy i wymagamy zgodnosci, zeby nie zlapac rozerwanej wartosci.
static int64_t readWritePos(const volatile int64_t *p)
{
    int64_t a = *p;
    for (int i = 0; i < 8; ++i) {
        int64_t b = *p;
        if (b == a) return a;
        a = b;
    }
    return a;
}

// ---------------------------------------------------------------------------
// Konstrukcja / destrukcja
// ---------------------------------------------------------------------------

DirectMonitor::DirectMonitor()
    : m_shm(nullptr), m_ctlMap(nullptr), m_ctl(nullptr)
    , m_hStopCtrl(nullptr), m_hStopRender(nullptr)
    , m_hControl(nullptr), m_hRender(nullptr)
{
    m_ctlMap = CreateFileMappingW(INVALID_HANDLE_VALUE, nullptr,
                                  PAGE_READWRITE, 0, sizeof(DirectCtl),
                                  DAW_DIRECT_CTL_NAME);
    if (!m_ctlMap) {
        DirectLog("[DIRECT] CreateFileMapping FAILED err=%lu\r\n", GetLastError());
        return;
    }
    m_ctl = static_cast<DirectCtl*>(MapViewOfFile(m_ctlMap, FILE_MAP_ALL_ACCESS,
                                                  0, 0, sizeof(DirectCtl)));
    if (!m_ctl) {
        DirectLog("[DIRECT] MapViewOfFile FAILED err=%lu\r\n", GetLastError());
        return;
    }
    if (m_ctl->magic != DIRECT_CTL_MAGIC)
        m_ctl->magic = DIRECT_CTL_MAGIC;
    DirectLog("[DIRECT] blok sterujacy otwarty\r\n");
}

DirectMonitor::~DirectMonitor()
{
    shutdown();
    if (m_ctl)      { UnmapViewOfFile(m_ctl);      m_ctl = nullptr; }
    if (m_ctlMap)   { CloseHandle(m_ctlMap);        m_ctlMap = nullptr; }
}

void DirectMonitor::setShm(DAWCaptureShm *shm)
{
    m_shm = shm;
}

// ---------------------------------------------------------------------------
// Watek kontrolny - jedyny, ktory startuje/zatrzymuje watek renderujacy.
// Co 100 ms patrzy, czy Manager chce direct monitoring i czy rate sie zmienil.
// ---------------------------------------------------------------------------

DWORD WINAPI DirectMonitor::controlThunk(LPVOID p)
{
    static_cast<DirectMonitor*>(p)->controlLoop();
    return 0;
}

void DirectMonitor::controlLoop()
{
    ULONGLONG lastStart = 0;
    bool everStarted = false;

    while (WaitForSingleObject(m_hStopCtrl, 100) == WAIT_TIMEOUT) {
        DirectCtl *ctl = m_ctl;
        DAWCaptureShm *shm = m_shm;
        if (!ctl) continue;

        // watek mogl sam umrzec (urzadzenie wypadlo) - posprzataj uchwyt
        if (m_hRender) {
            if (WaitForSingleObject(m_hRender, 0) == WAIT_OBJECT_0) {
                CloseHandle(m_hRender);
                m_hRender = nullptr;
                ctl->active = 0;
                DirectLog("[DIRECT] watek renderujacy zakonczyl sie sam\r\n");
            }
        }

        bool want = (ctl->direct == 1);
        bool have = (m_hRender != nullptr);

        if (want && !have) {
            // po padzie nie startuj w kolko - daj device odetchnac.
            // Brak urzadzenia to blad trwaly (nazwy z configu nie ma na
            // liscie), wiec probujemy rzadziej - inaczej status falowalby
            // START/NO_DEV i w GUI byloby widac ciagle "startuje".
            ULONGLONG now = GetTickCount64();
            ULONGLONG backoff = (ctl->status == DIRECT_ST_NO_DEV)
                                ? 10000ull : 3000ull;
            if (!everStarted || now - lastStart > backoff) {
                lastStart = now;
                everStarted = true;
                startRender();
            }
        } else if (!want && have) {
            stopRender();
            ctl->active = 0;
            ctl->status = DIRECT_ST_OFF;
        } else if (want && have && shm && ctl->rate != (int32_t)shm->sampleRate) {
            DirectLog("[DIRECT] zmiana zegara %d -> %d, restart renderu\r\n",
                      ctl->rate, (int)shm->sampleRate);
            stopRender();
            startRender();
        } else if (!want && !have) {
            ctl->active = 0;
            ctl->status = DIRECT_ST_OFF;
        }
    }

    stopRender();
    if (m_ctl) {
        m_ctl->active = 0;
        m_ctl->status = DIRECT_ST_OFF;
    }
}

void DirectMonitor::onStreamConfigured()
{
    if (!m_ctl || !m_shm) return;
    if (m_hControl) return;              // juz dziala
    if (!m_hStopCtrl)
        m_hStopCtrl = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!m_hStopRender)
        m_hStopRender = CreateEventW(nullptr, TRUE, FALSE, nullptr);
    if (!m_hStopCtrl || !m_hStopRender) return;
    m_hControl = CreateThread(nullptr, 0, controlThunk, this, 0, nullptr);
    DirectLog("[DIRECT] watek kontrolny start (shm=%p)\r\n", m_shm);
}

void DirectMonitor::onStreamClosing()
{
    shutdown();
}

void DirectMonitor::shutdown()
{
    if (m_hStopCtrl) SetEvent(m_hStopCtrl);
    if (m_hControl) {
        WaitForSingleObject(m_hControl, 4000);
        CloseHandle(m_hControl);
        m_hControl = nullptr;
    }
    if (m_hStopCtrl) { CloseHandle(m_hStopCtrl); m_hStopCtrl = nullptr; }
    stopRender();
    if (m_hStopRender) { CloseHandle(m_hStopRender); m_hStopRender = nullptr; }
    m_shm = nullptr;
    if (m_ctl) {
        m_ctl->active = 0;
        m_ctl->status = DIRECT_ST_OFF;
    }
}

// ---------------------------------------------------------------------------
// Start/stop watku renderujacego (wywolywane TYLKO z watku kontrolnego)
// ---------------------------------------------------------------------------

void DirectMonitor::startRender()
{
    if (m_hRender || !m_ctl || !m_hStopRender) return;
    ResetEvent(m_hStopRender);
    m_ctl->status = DIRECT_ST_START;
    m_hRender = CreateThread(nullptr, 0, renderThunk, this, 0, nullptr);
    if (!m_hRender) {
        m_ctl->status = DIRECT_ST_ERR;
        DirectLog("[DIRECT] CreateThread render FAILED err=%lu\r\n", GetLastError());
    }
}

void DirectMonitor::stopRender()
{
    if (!m_hRender) return;
    if (m_hStopRender) SetEvent(m_hStopRender);
    WaitForSingleObject(m_hRender, 5000);
    CloseHandle(m_hRender);
    m_hRender = nullptr;
    if (m_hStopRender) ResetEvent(m_hStopRender);
}

// ---------------------------------------------------------------------------
// Szukanie urzadzenia - TYLKO po nazwie z configu.
// Nie ma nazwy albo nie ma pasujacego endpointu = nie gramy. Nigdy nie
// przeskakujemy na "urzadzenie domyslne", bo to wlasnie tak trafila
// wczesniej muzyka do zlego wyjscia (MME zamiast WASAPI).
// ---------------------------------------------------------------------------

static IMMDevice* findDevice(const wchar_t *want)
{
    IMMDevice *result = nullptr;
    IMMDeviceEnumerator *en = nullptr;
    HRESULT hr = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr,
                                  CLSCTX_ALL, __uuidof(IMMDeviceEnumerator),
                                  reinterpret_cast<void**>(&en));
    if (FAILED(hr) || !en) return nullptr;

    if (!want || !want[0]) {
        en->Release();
        return nullptr;   // bez nazwy nie zgadujemy
    }

    IMMDeviceCollection *coll = nullptr;
    if (SUCCEEDED(en->EnumAudioEndpoints(eRender, DEVICE_STATE_ACTIVE, &coll)) && coll) {
        UINT n = 0;
        coll->GetCount(&n);
        for (UINT i = 0; i < n && !result; ++i) {
            IMMDevice *d = nullptr;
            if (FAILED(coll->Item(i, &d)) || !d) continue;
            IPropertyStore *ps = nullptr;
            wchar_t name[256] = {0};
            if (SUCCEEDED(d->OpenPropertyStore(STGM_READ, &ps)) && ps) {
                PROPVARIANT pv;
                PropVariantInit(&pv);
                if (SUCCEEDED(ps->GetValue(PKEY_Device_FriendlyName, &pv)) && pv.pwszVal)
                    wcsncpy_s(name, pv.pwszVal, _TRUNCATE);
                PropVariantClear(&pv);
                ps->Release();
            }
            if (name[0] && _wcsicmp(name, want) == 0)
                result = d;
            else
                d->Release();
        }
        coll->Release();
    }
    en->Release();
    return result;
}

// ---------------------------------------------------------------------------
// Watek renderujacy: SHM -> WASAPI
// ---------------------------------------------------------------------------

DWORD WINAPI DirectMonitor::renderThunk(LPVOID p)
{
    static_cast<DirectMonitor*>(p)->renderLoop();
    return 0;
}

void DirectMonitor::renderLoop()
{
    DirectCtl *ctl = m_ctl;
    DAWCaptureShm *shm = m_shm;
    if (!ctl || !shm) return;

    HRESULT hrc = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    bool comOk = SUCCEEDED(hrc) || hrc == RPC_E_CHANGED_MODE;
    if (!comOk) {
        ctl->status = DIRECT_ST_ERR;
        DirectLog("[DIRECT] CoInitializeEx hr=0x%08X\r\n", (unsigned)hrc);
        return;
    }

    // MMCSS - watek audio dostaje priorytet, inaczej zdarzy sie to samo,
    // co kiedys z Pythonem: opozniony callback = przerwy.
    DWORD mmcssTask = 0;
    HANDLE mmcss = AvSetMmThreadCharacteristicsW(L"Pro Audio", &mmcssTask);

    wchar_t want[128] = {0};
    wcsncpy_s(want, ctl->deviceName, _TRUNCATE);

    IMMDevice *dev = findDevice(want);
    if (!dev) {
        ctl->status = DIRECT_ST_NO_DEV;
        ctl->active = 0;
        DirectLog("[DIRECT] brak urzadzenia '%S'\r\n", want);
        if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
        if (comOk) CoUninitialize();
        return;
    }

    DWORD rate = shm->sampleRate ? (DWORD)shm->sampleRate : 44100;
    int nch = (int)(shm->numChannels ? shm->numChannels : 2);
    if (nch < 1) nch = 1;
    if (nch > 2) nch = 2;
    long asioFrames = (long)(shm->bufferFrames ? shm->bufferFrames : 256);
    ctl->rate = (int32_t)rate;

    IAudioClient *client = nullptr;
    IAudioRenderClient *render = nullptr;
    HANDLE hEvent = nullptr;
    HRESULT hr = dev->Activate(__uuidof(IAudioClient), CLSCTX_ALL, nullptr,
                               reinterpret_cast<void**>(&client));
    if (FAILED(hr) || !client) {
        ctl->status = DIRECT_ST_ERR;
        DirectLog("[DIRECT] Activate IAudioClient hr=0x%08X\r\n", (unsigned)hr);
        dev->Release();
        if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
        if (comOk) CoUninitialize();
        return;
    }

    WAVEFORMATEXTENSIBLE fx = {};
    fx.Format.wFormatTag      = WAVE_FORMAT_EXTENSIBLE;
    fx.Format.nChannels       = 2;
    fx.Format.nSamplesPerSec  = rate;
    fx.Format.wBitsPerSample  = 32;
    fx.Format.nBlockAlign     = (WORD)(2 * 4);
    fx.Format.nAvgBytesPerSec = rate * fx.Format.nBlockAlign;
    fx.Format.cbSize          = 22;
    fx.Samples.wValidBitsPerSample = 32;
    fx.dwChannelMask          = 0x3;
    fx.SubFormat              = SUBTYPE_FLOAT;

    REFERENCE_TIME defPeriod = 100000, minPeriod = 30000;
    client->GetDevicePeriod(&defPeriod, &minPeriod);

    // Probuje od najmniejszego okresu: 5 ms -> min -> domyslny.
    REFERENCE_TIME tries[4] = {50000, minPeriod, defPeriod, 2 * defPeriod};
    int usedTry = -1;
    const DWORD streamFlags =
        AUDCLNT_STREAMFLAGS_EVENTCALLBACK |
        AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM |
        AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY;
    for (int i = 0; i < 4; ++i) {
        hr = client->Initialize(AUDCLNT_SHAREMODE_SHARED, streamFlags,
                                tries[i], 0, &fx.Format, nullptr);
        if (SUCCEEDED(hr)) { usedTry = i; break; }
    }
    if (usedTry < 0) {
        ctl->status = DIRECT_ST_ERR;
        DirectLog("[DIRECT] Initialize hr=0x%08X\r\n", (unsigned)hr);
        client->Release(); dev->Release();
        if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
        if (comOk) CoUninitialize();
        return;
    }

    hEvent = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    UINT32 bufFrames = 0;
    hr = client->GetBufferSize(&bufFrames);
    if (FAILED(hr) || !hEvent) {
        ctl->status = DIRECT_ST_ERR;
        client->Release(); dev->Release();
        if (hEvent) CloseHandle(hEvent);
        if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
        if (comOk) CoUninitialize();
        return;
    }
    client->SetEventHandle(hEvent);
    client->GetService(__uuidof(IAudioRenderClient),
                       reinterpret_cast<void**>(&render));
    if (!render) {
        ctl->status = DIRECT_ST_ERR;
        client->Release(); dev->Release(); CloseHandle(hEvent);
        if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
        if (comOk) CoUninitialize();
        return;
    }

    // Wypelniam caly bufor cisza przed Start - inaczej pierwszy event
    // moze trafic w pusty bufor.
    {
        BYTE *p = nullptr;
        if (SUCCEEDED(render->GetBuffer(bufFrames, &p)) && p) {
            memset(p, 0, (size_t)bufFrames * 2 * sizeof(float));
            render->ReleaseBuffer(bufFrames, AUDCLNT_BUFFERFLAGS_SILENT);
        }
    }

    hr = client->Start();
    if (FAILED(hr)) {
        ctl->status = DIRECT_ST_ERR;
        DirectLog("[DIRECT] Start hr=0x%08X\r\n", (unsigned)hr);
        render->Release(); client->Release(); dev->Release(); CloseHandle(hEvent);
        if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
        if (comOk) CoUninitialize();
        return;
    }

    // Okres WASAPI w klatkach: tyle musi starczyc readahead, zeby kazdy
    // event dostal komplet. Bez tego beda przerwy co event.
    UINT32 periodFrames = (UINT32)((double)tries[usedTry] * rate / 10000000.0);
    if (periodFrames < 32) periodFrames = 32;
    if (asioFrames > (long)periodFrames) periodFrames = (UINT32)asioFrames;
    // pullSize: proponowana liczba klatek na jedno pobranie (uczy sie przy
    // BUFFER_TOO_LARGE - udalo sie trafilismy w okres silnika)
    UINT32 pullSize = periodFrames;

    int64_t src = 0;
    bool synced = false;
    int64_t lagFrames = periodFrames;   // staly readahead za pisarzem
    const int64_t BAND = (int64_t)asioFrames * 4;  // jak w _cb()/daw-source
    const int64_t lagFloor = periodFrames;
    const int64_t lagMax = periodFrames * 8;
    ULONGLONG stableSince = GetTickCount64();
    int underruns = 0;

    ctl->active = 1;
    ctl->status = DIRECT_ST_RUN;
    ctl->lagFrames = (int32_t)lagFrames;
    ctl->periodFrames = (int32_t)periodFrames;
    DirectLog("[DIRECT] GRA: dev='%S' rate=%lu okres=%u klatek (dur=%lld) "
              "bufor=%u readahead=%lld\r\n",
              want, rate, periodFrames, (long long)tries[usedTry],
              bufFrames, (long long)lagFrames);

    HANDLE waits[2] = { hEvent, m_hStopRender };

    while (true) {
        DWORD w = WaitForMultipleObjects(2, waits, FALSE, 3000);
        if (w == WAIT_OBJECT_0 + 1) break;          // stop
        if (w == WAIT_TIMEOUT) continue;            // DAW nie gra - nic
        if (w != WAIT_OBJECT_0) break;

        // ile silnik chce
        UINT32 n = pullSize;
        BYTE *data = nullptr;
        hr = render->GetBuffer(n, &data);
        if (hr == AUDCLNT_E_BUFFER_TOO_LARGE && n > 1) {
            n = n / 2;
            if (n < 32) n = 32;
            pullSize = n;
            hr = render->GetBuffer(n, &data);
        }
        if (FAILED(hr)) {
            if (hr == AUDCLNT_E_DEVICE_INVALIDATED) {
                ctl->status = DIRECT_ST_ERR;
                DirectLog("[DIRECT] urzadzenie wypadlo hr=0x%08X\r\n", (unsigned)hr);
                break;
            }
            continue;
        }

        UINT32 filled = 0;
        if (data) {
            float *out = reinterpret_cast<float*>(data);
            memset(out, 0, (size_t)n * 2 * sizeof(float));   // w razie czego

            bool live = (shm->active == 1);
            if (live) {
                int64_t wp = readWritePos(&shm->writePos);
                int64_t target = wp - lagFrames;

                if (!synced || wp < src
                    || target - src > (int64_t)RING_FRAMES / 2
                    || target - src < -(int64_t)RING_FRAMES / 2) {
                    src = target;          // pierwszy raz / nowa sesja (writePos=0)
                    synced = true;
                } else {
                    int64_t skip = target - src;
                    if (skip > BAND)       src = target;   // pisarz uciekl: wyrzuc
                    else if (skip < -BAND) src = wp;       // czekamy na pisarza
                }

                int64_t avail = target - src;
                int64_t take = (avail >= (int64_t)n) ? (int64_t)n
                           : ((avail > 0) ? avail : 0);
                if (take > 0) {
                    const uint32_t mask = RING_FRAMES - 1;
                    const float *base = shm->data;
                    for (int64_t i = 0; i < take; ++i) {
                        uint32_t slot = (uint32_t)((src + i) & mask);
                        const float *s = base + (size_t)slot * DAW_MAX_CHANNELS;
                        out[i * 2]      = s[0];
                        out[i * 2 + 1]  = (nch > 1) ? s[1] : s[0];
                    }
                    src += take;
                    filled = (UINT32)take;
                }
                if (filled < n) {
                    ++underruns;
                    if (lagFrames < lagMax) {
                        lagFrames += periodFrames;
                        stableSince = GetTickCount64();
                    }
                } else if (GetTickCount64() - stableSince > 3000 && lagFrames > lagFloor) {
                    // stabilnie - zmniejszamy zapas z powrotem
                    lagFrames -= periodFrames;
                    if (lagFrames < lagFloor) lagFrames = lagFloor;
                    stableSince = GetTickCount64();
                }
            } else {
                synced = false;              // DAW stop -> przy starcie re-sync
                ++underruns;
            }
        }

        render->ReleaseBuffer(n,
            (filled == 0) ? AUDCLNT_BUFFERFLAGS_SILENT : 0);

        ctl->underruns = underruns;
        ctl->lagFrames = (int32_t)lagFrames;
    }

    client->Stop();
    ctl->active = 0;
    ctl->status = DIRECT_ST_OFF;
    DirectLog("[DIRECT] watek renderujacy konczy (underruns=%d)\r\n", underruns);

    render->Release();
    client->Release();
    dev->Release();
    if (hEvent) CloseHandle(hEvent);
    if (mmcss) AvRevertMmThreadCharacteristics(mmcss);
    if (comOk) CoUninitialize();
}
