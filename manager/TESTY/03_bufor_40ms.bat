@echo off
rem  TEST 3 - bufor 40 ms. Bardzo stabilne, wyczuwalnie pozniejsze.
rem  Uzyj tylko wtedy, gdy 10 i 20 ms dalej szarpia.
call "%~dp0_uruchom.bat" "TEST 3: bufor 40 ms (najstabilniej, +30 ms)" Jabra --lag 40 --dluznosc 15
