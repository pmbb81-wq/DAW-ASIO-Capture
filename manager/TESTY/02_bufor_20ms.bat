@echo off
rem  TEST 2 - bufor 20 ms. Dluzszy, ale lepszy na slabszym sprzecie
rem  (zwlaszcza Bluetooth - mniej szarpniecia, kosztem ~10 ms opoznienia).
call "%~dp0_uruchom.bat" "TEST 2: bufor 20 ms (stabilniej, +10 ms)" Jabra --lag 20 --dluznosc 15
