#include <windows.h>
#include <objbase.h>
#include <shlwapi.h>
#include <shellapi.h>
#include <stdio.h>
#include "asio-registry.hpp"

#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "shell32.lib")

// ---------------------------------------------------------------------------
// GUID helpers
// ---------------------------------------------------------------------------

std::wstring GUIDToString(const GUID &g)
{
    wchar_t buf[64];
    StringFromGUID2(g, buf, 64);
    return std::wstring(buf);
}

bool StringToGUID(const std::wstring &s, GUID &out)
{
    return SUCCEEDED(CLSIDFromString(s.c_str(), &out));
}

// ---------------------------------------------------------------------------
// Run a command elevated (UAC prompt) and wait for it to finish.
// Returns true if the process exited with code 0.
// ---------------------------------------------------------------------------

static bool RunElevated(const wchar_t *exe, const wchar_t *args)
{
    SHELLEXECUTEINFOW sei = {};
    sei.cbSize       = sizeof(sei);
    sei.fMask        = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC;
    sei.lpVerb       = L"runas";
    sei.lpFile       = exe;
    sei.lpParameters = args;
    sei.nShow        = SW_HIDE;
    if (!ShellExecuteExW(&sei))
        return false;
    WaitForSingleObject(sei.hProcess, 10000);
    DWORD exitCode = 1;
    GetExitCodeProcess(sei.hProcess, &exitCode);
    CloseHandle(sei.hProcess);
    return (exitCode == 0);
}

// ---------------------------------------------------------------------------
// Enumerate ASIO drivers from HKLM\SOFTWARE\ASIO
// Each subkey name is the driver name; "CLSID" value holds the GUID string.
// The CLSID's InprocServer32 key holds the actual DLL path.
// ---------------------------------------------------------------------------

std::vector<ASIODriverInfo> EnumerateASIODrivers()
{
    std::vector<ASIODriverInfo> result;

    HKEY hAsio = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, L"SOFTWARE\\ASIO",
                      0, KEY_READ, &hAsio) != ERROR_SUCCESS)
        return result;

    wchar_t driverName[256];
    DWORD   nameLen = 256;
    DWORD   index   = 0;

    while (RegEnumKeyExW(hAsio, index++, driverName, &nameLen,
                          nullptr, nullptr, nullptr, nullptr) == ERROR_SUCCESS)
    {
        nameLen = 256;
        HKEY hDriver = nullptr;
        if (RegOpenKeyExW(hAsio, driverName, 0, KEY_READ, &hDriver) != ERROR_SUCCESS)
            continue;

        wchar_t clsidStr[64] = {};
        DWORD   cbClsid = sizeof(clsidStr);
        DWORD   type    = REG_SZ;
        if (RegQueryValueExW(hDriver, L"CLSID", nullptr, &type,
                             reinterpret_cast<BYTE*>(clsidStr), &cbClsid) == ERROR_SUCCESS)
        {
            GUID clsid;
            if (SUCCEEDED(CLSIDFromString(clsidStr, &clsid))) {
                wchar_t serverKey[200];
                swprintf_s(serverKey, L"SOFTWARE\\Classes\\CLSID\\%s\\InprocServer32",
                           clsidStr);

                HKEY hServer = nullptr;
                wchar_t dllPath[MAX_PATH] = {};
                DWORD   cbPath = sizeof(dllPath);
                type = REG_SZ;

                if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, serverKey,
                                  0, KEY_READ, &hServer) == ERROR_SUCCESS) {
                    // If OriginalServer exists, that's the real driver path
                    // (we previously installed our proxy as the default)
                    DWORD cbOrig = sizeof(dllPath);
                    if (RegQueryValueExW(hServer, L"OriginalServer", nullptr, &type,
                                        reinterpret_cast<BYTE*>(dllPath), &cbOrig) != ERROR_SUCCESS) {
                        // No OriginalServer — default value IS the real driver
                        RegQueryValueExW(hServer, nullptr, nullptr, &type,
                                        reinterpret_cast<BYTE*>(dllPath), &cbPath);
                    }
                    RegCloseKey(hServer);
                }

                ASIODriverInfo info;
                info.name    = driverName;
                info.clsid   = clsid;
                info.dllPath = dllPath;
                result.push_back(std::move(info));
            }
        }
        RegCloseKey(hDriver);
    }

    RegCloseKey(hAsio);
    return result;
}

// ---------------------------------------------------------------------------
// Install proxy redirect — modifies HKLM InprocServer32
//
// FL Studio and most ASIO hosts load drivers by reading the DLL path directly
// from HKLM\SOFTWARE\Classes\CLSID\{clsid}\InprocServer32 and calling
// LoadLibrary + DllGetClassObject. They do NOT use CoCreateInstance, so an
// HKCU override is invisible. We must modify HKLM.
//
// Steps:
//   1. Save the original DLL path as "OriginalServer" in the same key
//   2. Set the default value to our proxy DLL
//   3. If we lack HKLM write access, elevate via UAC (reg.exe runas)
//
// Cleaned up by RemoveProxyRedirect when OBS closes.
// ---------------------------------------------------------------------------

bool InstallProxyRedirect(const ASIODriverInfo &driver, const std::wstring &proxyDllPath)
{
    std::wstring clsidStr = GUIDToString(driver.clsid);
    std::wstring keyPath  = L"SOFTWARE\\Classes\\CLSID\\" + clsidStr + L"\\InprocServer32";

    // Try direct write first (works if OBS is running elevated)
    HKEY hk = nullptr;
    bool directOk = false;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, keyPath.c_str(),
                      0, KEY_READ | KEY_WRITE, &hk) == ERROR_SUCCESS) {
        // Check if already proxied (default value already points to our proxy)
        wchar_t curDefault[MAX_PATH] = {};
        DWORD   cbDef = sizeof(curDefault);
        DWORD   type  = REG_SZ;
        RegQueryValueExW(hk, nullptr, nullptr, &type,
                        reinterpret_cast<BYTE*>(curDefault), &cbDef);

        // If already pointing at our proxy, nothing to do
        if (_wcsicmp(curDefault, proxyDllPath.c_str()) == 0) {
            RegCloseKey(hk);
            return true;
        }

        // Save original path (only if OriginalServer doesn't already exist)
        wchar_t origCheck[MAX_PATH] = {};
        DWORD   cbOrigCheck = sizeof(origCheck);
        if (RegQueryValueExW(hk, L"OriginalServer", nullptr, nullptr,
                             reinterpret_cast<BYTE*>(origCheck), &cbOrigCheck) != ERROR_SUCCESS) {
            // OriginalServer doesn't exist yet — save the current default as original
            RegSetValueExW(hk, L"OriginalServer", 0, REG_SZ,
                          reinterpret_cast<const BYTE*>(curDefault),
                          (DWORD)((wcslen(curDefault) + 1) * sizeof(wchar_t)));
        }

        // Set default value to proxy DLL
        RegSetValueExW(hk, nullptr, 0, REG_SZ,
                      reinterpret_cast<const BYTE*>(proxyDllPath.c_str()),
                      (DWORD)((proxyDllPath.size() + 1) * sizeof(wchar_t)));

        RegCloseKey(hk);
        directOk = true;
    }

    if (directOk)
        return true;

    // --- No direct HKLM access: use elevated reg.exe commands ---
    // First, read the current default value so we can save it
    wchar_t origPath[MAX_PATH] = {};
    {
        HKEY hRead = nullptr;
        if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, keyPath.c_str(),
                          0, KEY_READ, &hRead) == ERROR_SUCCESS) {
            DWORD cbOrig = sizeof(origPath);
            DWORD type   = REG_SZ;
            // Check if OriginalServer already exists
            if (RegQueryValueExW(hRead, L"OriginalServer", nullptr, &type,
                                reinterpret_cast<BYTE*>(origPath), &cbOrig) != ERROR_SUCCESS) {
                // Read current default
                cbOrig = sizeof(origPath);
                RegQueryValueExW(hRead, nullptr, nullptr, &type,
                                reinterpret_cast<BYTE*>(origPath), &cbOrig);
            }
            RegCloseKey(hRead);
        } else {
            // Can't even read — use the driver path from enumeration
            wcsncpy_s(origPath, driver.dllPath.c_str(), MAX_PATH - 1);
        }
    }

    // Build reg.exe command to save OriginalServer and set proxy as default.
    // We run a single cmd /c with chained reg commands to get only one UAC prompt.
    std::wstring fullKey = L"HKLM\\" + keyPath;

    wchar_t cmdArgs[2048];
    swprintf_s(cmdArgs, sizeof(cmdArgs)/sizeof(wchar_t),
        L"/c "
        L"reg add \"%s\" /v OriginalServer /t REG_SZ /d \"%s\" /f && "
        L"reg add \"%s\" /ve /t REG_SZ /d \"%s\" /f",
        fullKey.c_str(), origPath,
        fullKey.c_str(), proxyDllPath.c_str());

    return RunElevated(L"cmd.exe", cmdArgs);
}

bool RemoveProxyRedirect(const GUID &clsid)
{
    std::wstring clsidStr = GUIDToString(clsid);
    std::wstring keyPath  = L"SOFTWARE\\Classes\\CLSID\\" + clsidStr + L"\\InprocServer32";

    // Read OriginalServer so we can restore the default value
    wchar_t origPath[MAX_PATH] = {};
    HKEY hk = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, keyPath.c_str(),
                      0, KEY_READ, &hk) == ERROR_SUCCESS) {
        DWORD cbOrig = sizeof(origPath);
        DWORD type   = REG_SZ;
        RegQueryValueExW(hk, L"OriginalServer", nullptr, &type,
                        reinterpret_cast<BYTE*>(origPath), &cbOrig);
        RegCloseKey(hk);
    }

    if (origPath[0] == L'\0')
        return false; // no original to restore

    // Try direct write
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, keyPath.c_str(),
                      0, KEY_WRITE, &hk) == ERROR_SUCCESS) {
        // Restore original DLL as default
        RegSetValueExW(hk, nullptr, 0, REG_SZ,
                      reinterpret_cast<const BYTE*>(origPath),
                      (DWORD)((wcslen(origPath) + 1) * sizeof(wchar_t)));
        // Remove OriginalServer value
        RegDeleteValueW(hk, L"OriginalServer");
        RegCloseKey(hk);
        return true;
    }

    // Elevate
    std::wstring fullKey = L"HKLM\\" + keyPath;
    wchar_t cmdArgs[2048];
    swprintf_s(cmdArgs, sizeof(cmdArgs)/sizeof(wchar_t),
        L"/c "
        L"reg add \"%s\" /ve /t REG_SZ /d \"%s\" /f && "
        L"reg delete \"%s\" /v OriginalServer /f",
        fullKey.c_str(), origPath,
        fullKey.c_str());

    return RunElevated(L"cmd.exe", cmdArgs);
}

bool IsProxyInstalled(const GUID &clsid)
{
    std::wstring clsidStr = GUIDToString(clsid);
    std::wstring keyPath  = L"SOFTWARE\\Classes\\CLSID\\" + clsidStr + L"\\InprocServer32";

    HKEY hk = nullptr;
    if (RegOpenKeyExW(HKEY_LOCAL_MACHINE, keyPath.c_str(),
                      0, KEY_READ, &hk) != ERROR_SUCCESS)
        return false;

    // Check if OriginalServer exists — that means we installed the proxy
    wchar_t tmp[4];
    DWORD cb = sizeof(tmp);
    bool installed = (RegQueryValueExW(hk, L"OriginalServer", nullptr, nullptr,
                                        reinterpret_cast<BYTE*>(tmp), &cb) == ERROR_SUCCESS)
                     || (cb > sizeof(tmp)); // value exists but buffer too small = still present

    RegCloseKey(hk);
    return installed;
}
