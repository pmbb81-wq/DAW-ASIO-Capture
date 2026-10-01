#pragma once
#include <windows.h>
#include <cstdint>
#include "shared-memory.hpp"

// ---------------------------------------------------------------------------
// Direct monitoring: proxy sam odtwarza dzwiek do sluchawek.
//
// Zwykla droga:  ASIO -> SHM -> Manager (osobny proces, Python) -> WASAPI
// Direct:       ASIO -> SHM -> watek renderujacy proxy -> WASAPI
//
// Proxy nie zmienia NIC w przechwytywaniu - OBS czyta SHM tak samo.
// Readahead bierzemy z tego samego pierscienia, algorytm ten sam co w
// daw-source.cpp i w _cb() Managera. Odpada: proces Pythona, GIL, jego
// blok 256 klatek i 10 ms stalego zapasu - zostaje sam sprzet.
// ---------------------------------------------------------------------------

#define DAW_DIRECT_CTL_NAME L"Local\\OBSDAWCapture_DirectCtl"
#define DIRECT_CTL_MAGIC    0x54435844u  // 'DXCT'

// status proxy (pole status) - Manager pokazuje to w GUI
#define DIRECT_ST_OFF     0  // nie gra
#define DIRECT_ST_START   1  // watek renderujacy startuje
#define DIRECT_ST_RUN     2  // gra
#define DIRECT_ST_NO_DEV  3  // nie ma urzadzenia o podanej nazwie
#define DIRECT_ST_ERR     4  // blad WASAPI (szczegoly w logu proxy)

// Kto co zapisuje:
//   direct, deviceName  -> tylko Manager (GUI), z watkow GUI/co 150 ms
//   reszta               -> tylko proxy (watek renderujacy/kontrolny)
// Odczyt na zimno, bez blokad - obie strony tylko patrza na liczby.
#pragma pack(push, 1)
struct DirectCtl {
    uint32_t         magic;        // DIRECT_CTL_MAGIC, zeby wiedziec ze zyje
    volatile int32_t direct;       // 1 = Manager chce, zebym gral
    volatile int32_t active;       // 1 = trzymam urzadzenie wyjsciowe
    volatile int32_t status;       // DIRECT_ST_*
    volatile int32_t underruns;
    volatile int32_t lagFrames;    // aktualny readahead w klatkach
    volatile int32_t periodFrames; // okres WASAPI w klatkach
    volatile int32_t rate;
    wchar_t          deviceName[128]; // do tego urzadzenia graj
};
#pragma pack(pop)

// Manager liczy te offsety sam (ctypes) - gdyby sie rozjechaly, obie strony
// czytalby sobie nawzajem inne pola. Lepiej niech sie nie zbuduje.
static_assert(sizeof(DirectCtl) == 288,
              "DirectCtl: Manager oczekuje offsetow 0/4/8/12/16/20/24/28/32");

class DirectMonitor {
public:
    DirectMonitor();
    ~DirectMonitor();

    DirectMonitor(const DirectMonitor &) = delete;
    DirectMonitor &operator=(const DirectMonitor &) = delete;

    void setShm(DAWCaptureShm *shm);  // wywolywane z createBuffers
    void onStreamConfigured();        // ASIO buffory gotowe
    void onStreamClosing();           // ASIO buffory znika
    void shutdown();                  // destruktor proxy

private:
    static DWORD WINAPI controlThunk(LPVOID p);
    static DWORD WINAPI renderThunk(LPVOID p);

    void controlLoop();
    void renderLoop();
    void startRender();
    void stopRender();

    DAWCaptureShm *m_shm;      // wskaznik z drivera (ten sam proces)
    HANDLE         m_ctlMap;
    DirectCtl     *m_ctl;
    HANDLE         m_hStopCtrl;
    HANDLE         m_hStopRender;
    HANDLE         m_hControl;
    HANDLE         m_hRender;
};
