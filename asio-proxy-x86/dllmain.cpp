// asio-proxy/dllmain.cpp
//
// COM in-process server that wraps any real ASIO driver.
//
// Registration (done by the OBS plugin, not this DLL):
//   HKCU\SOFTWARE\Classes\CLSID\{real-driver-clsid}\InprocServer32 = path\to\asio-proxy.dll
//   HKCU\SOFTWARE\Classes\CLSID\{real-driver-clsid}\OriginalServer  = original DLL path
//
// When the DAW calls CoCreateInstance({real-driver-clsid}), Windows finds the
// per-user override first, loads this DLL, and creates a ProxyASIODriver that
// wraps the original driver loaded from OriginalServer.

#include <windows.h>
#include <objbase.h>
#include <shlwapi.h>
#include <stdio.h>
#include "proxy-driver.hpp"

#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "ole32.lib")

// ---------------------------------------------------------------------------
// Read OriginalServer path from HKCU (where we stored it during registration)
// ---------------------------------------------------------------------------

static bool GetOriginalServerPath(const GUID &clsid, wchar_t *pathOut, DWORD cbPath)
{
    wchar_t clsidStr[64];
    StringFromGUID2(clsid, clsidStr, 64);

    // OriginalServer is stored inside the InprocServer32 subkey, not the CLSID root
    wchar_t keyPath[256];
    swprintf_s(keyPath, 256, L"SOFTWARE\\Classes\\CLSID\\%s\\InprocServer32", clsidStr);

    // OriginalServer is now stored in HKLM (we modify HKLM directly
    // because ASIO hosts load DLLs from HKLM, not via COM/HKCU)
    HKEY hk = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, keyPath, 0, KEY_READ, &hk) != ERROR_SUCCESS)
        if (RegOpenKeyExW(HKEY_CURRENT_USER, keyPath, 0, KEY_READ, &hk) != ERROR_SUCCESS)
            return false;

    DWORD type = REG_SZ;
    LONG  res  = RegQueryValueExW(hk, L"OriginalServer", nullptr, &type,
                                   reinterpret_cast<BYTE*>(pathOut), &cbPath);
    RegCloseKey(hk);
    return (res == ERROR_SUCCESS);
}

// ---------------------------------------------------------------------------
// IClassFactory — creates ProxyASIODriver instances
// ---------------------------------------------------------------------------

class ProxyClassFactory : public IClassFactory {
    GUID  m_clsid;
    LONG  m_ref;
public:
    explicit ProxyClassFactory(const GUID &clsid) : m_clsid(clsid), m_ref(1) {}

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void **ppv) override {
        if (IsEqualIID(riid, IID_IUnknown) || IsEqualIID(riid, IID_IClassFactory)) {
            *ppv = static_cast<IClassFactory*>(this);
            AddRef(); return S_OK;
        }
        *ppv = nullptr; return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef()  override { return InterlockedIncrement(&m_ref); }
    ULONG STDMETHODCALLTYPE Release() override {
        LONG r = InterlockedDecrement(&m_ref);
        if (!r) delete this;
        return r;
    }

    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown *outer, REFIID riid, void **ppv) override
    {
        if (outer) return CLASS_E_NOAGGREGATION;
        *ppv = nullptr;

        // Open log early so every failure path is recorded
        wchar_t _tmp[MAX_PATH], logPath[MAX_PATH];
        GetTempPathW(MAX_PATH, _tmp);
        swprintf_s(logPath, L"%sobs-asio-proxy.log", _tmp);

        HANDLE logFile = CreateFileW(logPath,
            GENERIC_WRITE, FILE_SHARE_READ, nullptr,
            CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);

        auto log = [&](const wchar_t *msg) {
            if (logFile == INVALID_HANDLE_VALUE) return;
            char buf[512];
            int n = WideCharToMultiByte(CP_UTF8, 0, msg, -1, buf, 511, nullptr, nullptr);
            if (n > 1) { DWORD w; WriteFile(logFile, buf, n-1, &w, nullptr); }
            DWORD w; WriteFile(logFile, "\r\n", 2, &w, nullptr);
        };

        wchar_t origPath[MAX_PATH] = {};
        if (!GetOriginalServerPath(m_clsid, origPath, sizeof(origPath))) {
            log(L"FAIL: GetOriginalServerPath returned false — OriginalServer value missing from registry");
            if (logFile != INVALID_HANDLE_VALUE) CloseHandle(logFile);
            return E_FAIL;
        }
        log(origPath); // log what path we're trying to load

        // LOAD_WITH_ALTERED_SEARCH_PATH: Windows uses the real driver's own directory
        // when resolving *its* dependencies, not our proxy's directory.
        HMODULE realDll = LoadLibraryExW(origPath, nullptr, LOAD_WITH_ALTERED_SEARCH_PATH);
        if (!realDll) {
            wchar_t msg[512];
            swprintf_s(msg, L"FAIL: LoadLibraryExW error %lu for path: %s",
                       GetLastError(), origPath);
            log(msg);
            if (logFile != INVALID_HANDLE_VALUE) CloseHandle(logFile);
            return E_FAIL;
        }
        log(L"LoadLibraryExW OK");

        using FnGetClass = HRESULT(WINAPI*)(REFCLSID, REFIID, LPVOID*);
        auto fn = reinterpret_cast<FnGetClass>(
            GetProcAddress(realDll, "DllGetClassObject"));
        if (!fn) {
            log(L"FAIL: DllGetClassObject export not found in real driver");
            FreeLibrary(realDll);
            if (logFile != INVALID_HANDLE_VALUE) CloseHandle(logFile);
            return E_FAIL;
        }
        log(L"DllGetClassObject found");

        IClassFactory *realFactory = nullptr;
        if (FAILED(fn(m_clsid, IID_IClassFactory,
                      reinterpret_cast<void**>(&realFactory)))) {
            log(L"FAIL: DllGetClassObject call failed");
            FreeLibrary(realDll);
            if (logFile != INVALID_HANDLE_VALUE) CloseHandle(logFile);
            return E_FAIL;
        }
        log(L"IClassFactory obtained");

        IASIO *realDriver = nullptr;
        HRESULT hr = realFactory->CreateInstance(
            nullptr, m_clsid, reinterpret_cast<void**>(&realDriver));
        realFactory->Release();

        if (FAILED(hr) || !realDriver) {
            wchar_t msg[128];
            swprintf_s(msg, L"FAIL: real driver CreateInstance hr=0x%08X", (unsigned)hr);
            log(msg);
            FreeLibrary(realDll);
            if (logFile != INVALID_HANDLE_VALUE) CloseHandle(logFile);
            return E_FAIL;
        }
        log(L"Real driver instance created — proxy wrapping now");

        auto *proxy = new ProxyASIODriver(realDriver, realDll, m_clsid);
        hr = proxy->QueryInterface(riid, ppv);
        proxy->Release();

        log(L"SUCCESS: proxy active, audio will flow to OBS");
        if (logFile != INVALID_HANDLE_VALUE) CloseHandle(logFile);
        return hr;
    }

    HRESULT STDMETHODCALLTYPE LockServer(BOOL) override { return S_OK; }
};

// ---------------------------------------------------------------------------
// DLL exports
// ---------------------------------------------------------------------------

extern "C" HRESULT WINAPI DllGetClassObject(REFCLSID rclsid, REFIID riid, LPVOID *ppv)
{
    // Log that we were called — if this file appears, the DLL loaded OK
    wchar_t _t[MAX_PATH], logPath[MAX_PATH];
    GetTempPathW(MAX_PATH, _t);
    swprintf_s(logPath, L"%sobs-asio-proxy.log", _t);
    HANDLE f = CreateFileW(logPath,
        GENERIC_WRITE, FILE_SHARE_READ, nullptr,
        CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f != INVALID_HANDLE_VALUE) {
        const char msg[] = "DllGetClassObject called\r\n";
        DWORD w; WriteFile(f, msg, sizeof(msg)-1, &w, nullptr);
        CloseHandle(f);
    }

    *ppv = nullptr;
    auto *factory = new ProxyClassFactory(rclsid);
    HRESULT hr = factory->QueryInterface(riid, ppv);
    factory->Release();
    return hr;
}

extern "C" HRESULT WINAPI DllCanUnloadNow()
{
    return S_FALSE; // keep loaded while DAW is running
}

BOOL WINAPI DllMain(HINSTANCE, DWORD, LPVOID)
{
    return TRUE;
}
