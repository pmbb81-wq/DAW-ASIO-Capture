@echo off
title AXE IO ONE - testy odsluchu
rem ===========================================================================
rem  AXE IO ONE - testy odsluchu
rem
rem  GRAJ W TH-U, ZANIM ODALISZ TEST - inaczej nie bedzie dzwieku
rem  (SHM powstaje dopiero gdy TH-U gra przez ASIO).
rem
rem  Kazdy test zapisuje wynik do:  TESTY\wyniki.txt
rem  Na koncu wpisz, ktory test najlepiej brzmial - wpisze to do
rem  pliku USTAWienia.txt, ktore wystarczy potem przepisac w Managerze.
rem ===========================================================================

echo.
echo  ==================================================================
echo    AXE IO ONE - testy odsluchu
echo  ==================================================================
echo.
echo  WAZNE:
echo    1. TH-U musi GRAC (przez ASIO) - inaczej cisza.
echo    2. ZAMKNIJ Managera - inaczej urzadzenie jest zajete.
echo    3. Sluchawki na Jabra, nie na glosniki.
echo.
echo  Co sprawdzic przy kazdym teście:
echo    - czy dzwiek jest ciagle, czy szarpie / trzeszczy
echo    - jak duze jest opoznienie (grasz i czujesz)
echo    - liczba "przerwy" (im mniej, tym lepiej; 0 = idealnie)
echo.

choice /C 1234567T /N /M "  Wybierz test (T = koniec): "

if errorlevel 8 goto koniec
if errorlevel 7 call "%~dp007_ton_440Hz_bez_THU.bat" & goto menu
if errorlevel 6 call "%~dp006_latencja_systemowa.bat" & goto menu
if errorlevel 5 call "%~dp005_blok_512.bat" & goto menu
if errorlevel 4 call "%~dp004_zegar_48kHz.bat" & goto menu
if errorlevel 3 call "%~dp003_bufor_40ms.bat" & goto menu
if errorlevel 2 call "%~dp002_bufor_20ms.bat" & goto menu
if errorlevel 1 call "%~dp001_standard_10ms.bat"

:menu
echo.
echo  Ktory test najlepiej brzmial? Wpisz numer 1-7 (T = koniec):
set /p "WYB="
if "%WYB%"=="T" goto koniec
if "%WYB%"=="t" goto koniec
if "%WYB%"=="7" echo 7 > "%~dp0USTAWienia.txt" & echo  zapisano: ton 440 Hz & goto menu
if "%WYB%"=="6" echo 6 > "%~dp0USTAWienia.txt" & echo  zapisano: latencja systemowa & goto menu
if "%WYB%"=="5" echo 5 > "%~dp0USTAWienia.txt" & echo  zapisano: blok 512 & goto menu
if "%WYB%"=="4" echo 4 > "%~dp0USTAWienia.txt" & echo  zapisano: 48000 Hz & goto menu
if "%WYB%"=="3" echo 3 > "%~dp0USTAWienia.txt" & echo  zapisano: bufor 40 ms & goto menu
if "%WYB%"=="2" echo 2 > "%~dp0USTAWienia.txt" & echo  zapisano: bufor 20 ms & goto menu
if "%WYB%"=="1" echo 1 > "%~dp0USTAWienia.txt" & echo  zapisano: bufor 10 ms & goto menu
echo  Nieznana odpowiedz, wpisz 1-7 albo T.
goto menu

:koniec
echo.
echo  Koniec. Najlepszy test zapisany w: %~dp0USTAWienia.txt
echo  Pelne wyniki:                      %~dp0wyniki.txt
echo.
echo  Jak cos zapiszesz do USTAWIENIA.txt, wklej mi to - ustawie
echo  Manager na te wartosci na stale.
echo.
pause
