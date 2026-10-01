import winreg
for c in ["{1F390745-C530-4eec-9197-7375B06079DB}", "{62CF8386-3DF2-4334-8615-FBD96D6F9B88}"]:
    for view in [winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY]:
        try:
            h = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Classes\CLSID\{c}\InprocServer32", 0, winreg.KEY_READ | view)
            val, _ = winreg.QueryValueEx(h, "")
            print(c, view, val)
            winreg.CloseKey(h)
        except Exception as e:
            print(c, view, e)
