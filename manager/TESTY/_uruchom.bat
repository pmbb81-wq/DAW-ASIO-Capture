@echo off
rem ===========================================================================
rem  Wspolny pomocnik dla testow odsluchu. Uzycie:
rem      _uruchom.bat "opis" [args...]
rem
rem  Wynik dopisuje SAM Manager (--raport), a nie ten skrypt: przekierowanie
rem  wyjscia programu onefile przez bat dalo pusty wynik i kod -1.
rem  (katalog _MEIxxx w %TEMP% koliduje z plikiem wynikowym)
rem ===========================================================================

set "EXE=%~dp0AXE_IO_ONE_OBS_Manager.exe"
if not exist "%EXE%" set "EXE=%~dp0..\dist\AXE_IO_ONE_OBS_Manager.exe"
if not exist "%EXE%" (
    echo.
    echo   NIE ZNALEZIONO AXE_IO_ONE_OBS_Manager.exe
    echo   Szukam tutaj: %EXE%
    echo.
    echo   Skopiuj ten folder obok pliku AXE_IO_ONE_OBS_Manager.exe
    echo   albo uruchom skrypt z folderu dist.
    echo.
    pause
    exit /b 1
)

echo.
echo ==================================================================
echo   %~1
echo ==================================================================
echo.

"%EXE%" --audiotest %2 %3 %4 %5 %6 %7 %8 %9 --tytul "%~1" --raport "%~dp0wyniki.txt"

echo.
echo   Wynik dopisany do:
echo     %~dp0wyniki.txt
echo.
pause
