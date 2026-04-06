@echo off
:: OBS DAW Audio Capture (ASIO) — Uninstaller
:: Removes plugin DLLs and restores any proxied ASIO drivers.
:: Run as Administrator.

setlocal enabledelayedexpansion

:: ---- Check admin ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo This script needs administrator privileges.
    echo Right-click and select "Run as administrator".
    pause
    exit /b 1
)

set "OBS_DIR=%ProgramFiles%\obs-studio"
set "PLUGIN_DIR=%OBS_DIR%\obs-plugins\64bit"

:: ---- Restore any proxied ASIO drivers ----
echo Checking for proxied ASIO drivers...
for /f "tokens=*" %%D in ('reg query "HKLM\SOFTWARE\ASIO" 2^>nul') do (
    for /f "tokens=2*" %%A in ('reg query "%%D" /v CLSID 2^>nul ^| findstr CLSID') do (
        set "CLSID=%%B"
        set "KEY=HKLM\SOFTWARE\Classes\CLSID\!CLSID!\InprocServer32"
        for /f "tokens=2*" %%X in ('reg query "!KEY!" /v OriginalServer 2^>nul ^| findstr OriginalServer') do (
            set "ORIG=%%Y"
            echo   Restoring !CLSID! to !ORIG!
            reg add "!KEY!" /ve /t REG_SZ /d "!ORIG!" /f >nul
            reg delete "!KEY!" /v OriginalServer /f >nul
        )
    )
)

:: ---- Remove DLLs ----
if exist "%PLUGIN_DIR%\obs-daw-capture.dll" (
    del /f "%PLUGIN_DIR%\obs-daw-capture.dll"
    echo Removed obs-daw-capture.dll
)
if exist "%PLUGIN_DIR%\asio-proxy.dll" (
    del /f "%PLUGIN_DIR%\asio-proxy.dll"
    echo Removed asio-proxy.dll
)

echo.
echo Uninstalled. ASIO drivers restored to original state.
echo.
pause
