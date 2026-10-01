@echo off
rem  TEST 6 - latencja systemowa zamiast WASAPI "low" (22 ms).
rem  Zwykle gorzej (dluzszy bufor Windows), ale czasem jedyna rzecz,
rem  ktora wchodzi na zepsuty sterownik. Warto sprawdzic, czy wyjście
rem  w ogole dziala, zanim szukac przyczyny dalej.
call "%~dp0_uruchom.bat" "TEST 6: latencja systemowa (bufor Windows)" Jabra --lag 10 --latencja system --dluznosc 15
