import winreg

clsids = [
    "{1F390745-C530-4eec-9197-7375B06079DB}",
    "{62CF8386-3DF2-4334-8615-FBD96D6F9B88}"
]
proxy64 = r"C:\Program Files\obs-studio\obs-plugins\64bit\asio-proxy.dll"

for c in clsids:
    path = rf"SOFTWARE\Classes\CLSID\{c}\InprocServer32"
    # Update 64-bit view (KEY_WOW64_64KEY = 256)
    try:
        hKey = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WRITE | winreg.KEY_WOW64_64KEY)
        cur, _ = winreg.QueryValueEx(hKey, "")
        print(f"HKLM\{path} (64-bit): {cur}")
        if cur.lower() != proxy64.lower():
            winreg.SetValueEx(hKey, "OriginalServer", 0, winreg.REG_SZ, cur)
            winreg.SetValueEx(hKey, "", 0, winreg.REG_SZ, proxy64)
            print(f"  -> Updated 64-bit to {proxy64}")
        winreg.CloseKey(hKey)
    except Exception as e:
        print(f"Error 64-bit {c}: {e}")
