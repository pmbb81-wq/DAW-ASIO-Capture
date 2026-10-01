@echo off
rem  TEST 7 - czysty ton 440 Hz, bez TH-U i bez SHM.
rem  Sprawdza sam sprzet: zeby wiedziec, czy szarpie w odsluchu, czy
rem  moze sluchawki/sterownik. Nie potrzebujesz TH-U.
call "%~dp0_uruchom.bat" "TEST 7: ton 440 Hz bez TH-U" Jabra --ton 440 --dluznosc 10
