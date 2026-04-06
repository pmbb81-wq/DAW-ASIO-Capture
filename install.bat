@echo off
:: OBS DAW Audio Capture (ASIO) — Installer
:: Copies plugin DLLs to OBS and sets up the ASIO proxy redirect.
:: Run as Administrator.

setlocal

:: ---- Check admin ----
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo This installer needs administrator privileges.
    echo Right-click and select "Run as administrator".
    pause
    exit /b 1
)

:: ---- Find OBS ----
set "OBS_DIR=%ProgramFiles%\obs-studio"
if not exist "%OBS_DIR%\obs-plugins\64bit" (
    echo ERROR: OBS Studio not found at %OBS_DIR%
    echo Install OBS first, then re-run this.
    pause
    exit /b 1
)

:: ---- Find DLLs (same folder as this script, or build output) ----
set "SCRIPT_DIR=%~dp0"
set "PLUGIN_DLL="
set "PROXY_DLL="

if exist "%SCRIPT_DIR%obs-daw-capture.dll" (
    set "PLUGIN_DLL=%SCRIPT_DIR%obs-daw-capture.dll"
    set "PROXY_DLL=%SCRIPT_DIR%asio-proxy.dll"
) else if exist "%SCRIPT_DIR%build\obs-plugin\Release\obs-daw-capture.dll" (
    set "PLUGIN_DLL=%SCRIPT_DIR%build\obs-plugin\Release\obs-daw-capture.dll"
    set "PROXY_DLL=%SCRIPT_DIR%build\asio-proxy\Release\asio-proxy.dll"
)

if not defined PLUGIN_DLL (
    echo ERROR: Cannot find obs-daw-capture.dll
    echo Place the DLLs next to this script, or run from the project root.
    pause
    exit /b 1
)
if not exist "%PROXY_DLL%" (
    echo ERROR: Cannot find asio-proxy.dll
    pause
    exit /b 1
)

:: ---- Copy DLLs ----
echo Copying plugin files to OBS...
copy /y "%PLUGIN_DLL%" "%OBS_DIR%\obs-plugins\64bit\" >nul
copy /y "%PROXY_DLL%" "%OBS_DIR%\obs-plugins\64bit\" >nul
echo   obs-daw-capture.dll  OK
echo   asio-proxy.dll       OK

echo.
echo Installed. Open OBS, add a "DAW Audio Capture (ASIO)" source,
echo pick your ASIO driver, then start your DAW.
echo.
pause
