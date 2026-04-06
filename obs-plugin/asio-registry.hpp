#pragma once
#include <windows.h>
#include <string>
#include <vector>

struct ASIODriverInfo {
    std::wstring name;       // e.g. "Focusrite USB ASIO"
    GUID         clsid;      // driver CLSID
    std::wstring dllPath;    // original InprocServer32 path
};

// Enumerate all ASIO drivers installed on this machine
std::vector<ASIODriverInfo> EnumerateASIODrivers();

// Install / remove the per-user COM redirect so asio-proxy.dll intercepts the chosen driver.
// proxyDllPath = full path to asio-proxy.dll (alongside this OBS plugin)
bool InstallProxyRedirect(const ASIODriverInfo &driver, const std::wstring &proxyDllPath);
bool RemoveProxyRedirect(const GUID &clsid);

// Returns true if a proxy redirect is currently installed for this CLSID
bool IsProxyInstalled(const GUID &clsid);

// Utility: GUID <-> string
std::wstring GUIDToString(const GUID &g);
bool         StringToGUID(const std::wstring &s, GUID &out);
