@echo off
rem  TEST 5 - blok 512 klatek zamiast 256. Czasem pomaga na Bluetooth,
rem  bo zmniejsza liczbe wywolan callbacku (mieszanego z systemem).
rem  Koszt: +5.8 ms opoznienia.
call "%~dp0_uruchom.bat" "TEST 5: blok 512 klatek (+5.8 ms)" Jabra --lag 10 --blok 512 --dluznosc 15
