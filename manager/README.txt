AXE I/O ONE - NAGRYWARKA (DAW)
==============================

Co to robi:
  Nagrywa dokladnie to, co DAW / JAM VOX wysyla przez ASIO, prosto ze wspolnej
  pamieci proxy (Local\OBSDAWCapture_Shm). Bez kabli wirtualnych i bez
  domieszki monitoringu sprzetowego. Dziala rownoczesnie ze zrodlem OBS -
  kazdy ma wlasna pozycje odczytu, wiec nagrywanie nie zakloca streamu.


WYMAGANIE (jednorazowo):
  Sterownik ASIO musi byc przekierowany na proxy. Ustawia to instalator
  "AXE IO ONE - OBS Audio Capture" (AXE-IO-ONE-OBS-Audio-Capture-Setup.exe),
  ktory instaluje wtyczke OBS oraz przekierowanie 64-bit / 32-bit.
  Gdy przekierowanie jest gotowe, proxy tworzy wspolna pamiec za kazdym razem,
  gdy host (DAW / JAM VOX) otwiera sterownik ASIO.


JAK UZYC:
  1. Uruchom DAW (lub JAM VOX) i wlacz odtwarzanie przez sterownik ASIO.
  2. Uruchom AXE_IO_ONE_OBS_Manager.exe.
  3. Kliknij "Sprawdz proxy". Status powinien pokazac:
       "Proxy aktywne - mozesz nagrywac."
     (Pokazuje tez fs, liczbe kanalow i bufor ASIO.)
  4. Ustaw:
       Folder zapisu    - gdzie zapisac plik,
       prefiks pliku    - poczatek nazwy pliku (domyslnie daw_),
       format           - WAV albo MP3,
       para wyjscia     - ktore wyjscia ASIO nagrywac (domyslnie Out 1-2),
       ms opoznienia    - bufor resync (domyslnie 10; nie wplywa na brzmienie).
  5. START ... STOP. Gotowy plik pojawi sie w wybranym folderze.


MP3:
  Wymaga programu ffmpeg w PATH (albo C:\ffmpeg\bin\ffmpeg.exe).
  Bez niego nagranie zapisze sie jako WAV.


PARA WYJSCIA:
  "Out 1-2" to zwykle glowny master. Jezeli DAW kieruje dzwiek na inne wyjscia,
  wybierz wlasciwa pare. Kanaly sa mapowane na numer kanalu ASIO (Out 1 -> kanal 0).


ROZMIAR OKNA / USTAWIENIA:
  Ustawienia zapisuja sie w pliku recorder_config.json obok .exe.


BUDOWA ZE ZRODEL (opcjonalnie):
  pip install pyinstaller
  pyinstaller AXE_IO_ONE_OBS_Manager.spec
  Wynik: dist\AXE_IO_ONE_OBS_Manager.exe. Nie wymaga dodatkowych bibliotek
  (tylko Tkinter).


LICENCJA:
  MIT (patrz LICENSE w repozytorium). Oryginalny plugin: Monte Emerson
  (emersound/DAW-ASIO-Capture).
