@echo off
rem  TEST 1 - zwykly odsluch: WASAPI, 22 ms, bufor 10 ms.
rem  To jest ustawienie domyslne. Jesli tu gra plynnie, nie ma po co
rem  zmieniac ustawien - zostaw tak.
call "%~dp0_uruchom.bat" "TEST 1: standard (bufor 10 ms, WASAPI 22 ms)" Jabra --lag 10 --dluznosc 15
