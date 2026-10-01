@echo off
rem  TEST 4 - wymuszenie 48 kHz. Ten endpoint (Jabra) obsluguje 44.1 kHz,
rem  wiec ten test ma pokazac, czy Manager prawidlowo bierze 44.1 kHz
rem  z konwersja zamiast rzucac bledem.
rem  Uzyteczne tylko do diagnostyki - do grania lepszy jest 44.1 kHz.
call "%~dp0_uruchom.bat" "TEST 4: zegar 48000 Hz (konwersja na 44.1)" Jabra --rate 48000 --lag 10 --dluznosc 15
