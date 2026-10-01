AXE I/O ONE -> OBS - BRIDGE (WASAPI -> wirtualny kabel)
========================================================

Co to robi:
  Most przechwytuje dzwiek z urzadzenia Wejscia (AXE I/O ONE) przez WASAPI i
  odtwarza go do wirtualnego kabla (VB-CABLE). W OBS nagrywasz wtedy "CABLE
  Output" jako OSOBNA sciezke (Track), niezaleznie od dzwieku systemu.

WYMAGANE (jednorazowo):
  1. Zainstaluj darmowy VB-CABLE z https://vb-audio.com/Cable/  (wystarczy
     "VBCABLE_Driver_Pack64.zip" dla Windows x64) i ZRESTARTUJ system.
  2. Python 3.13+ (u Ciebie jest 3.14) + biblioteka:
        pip install sounddevice

APLIKACJA EXE (NAJPROSTSZA - WSZYSTKO W JEDNYM):
  Uruchom:  AXE_IO_ONE_OBS_Manager.exe   (nie wymaga Pythona; 37 MB)
  JEDNO okno, piec zakladek:
    [ MOST (ONE -> kabel) ]     - tak jak poprzedni Bridge: wybierz Wejscie
                                  = AXE I/O ONE i Wyjscie = CABLE Input,
                                  przycisk WLACZ/WYLACZ ROUTING, VU, wzmocnienie,
                                  fs. OBS nagrywa "CABLE Output".
    [ FLEXASIO (ASIO) ]         - wirtualny sterownik ASIO w tym samym oknie:
                                  * status instalacji + przycisk "Zainstaluj
                                    FlexASIO" (wbudowany instalator, UAC),
                                  * listy wyboru Wejscie/Wyjscie ASIO (nazwy
                                    dokladnie jak widzi FlexASIO),
                                  * przyciski profili: "Profil: JAM VOX" i
                                    "Profil: OBS" - zapisuja %USERPROFILE%\
                                    FlexASIO.toml (bufor/kanaly do ustawienia),
                                  * "Zapisz .toml" (recznie) i "Pokaz .toml".
    [ ASIO Capture (wtyczka) ]  - przechwyt wyjscia ASIO do OBS (patrz sekcja
                                  "WYCZKA OBS" nizej): status, instalacja DLL,
                                  redirect 64- i 32-bit (JAM VOX), przywracanie.
    [ Nagrywarka (DAW) ]        - zapis zrodla ASIO (to, co gra DAW/JAM VOX)
                                  prosto na dysk: WAV/FLAC/MP3, bez OBS.
    [ Monitoring (bez OBS) ]    - ODSLUCH z regulacja latencji bufora bez
                                  OBS i bez DAW - patrz sekcja "MONITORING"
                                  nizej.
  WERSJA PYTHON do edycji: axeio_obs_manager.py (ten sam kod).
  Starszy Bridge (AXE_IO_ONE_OBS_Bridge.exe) jest wpelni zastepowany przez
  zakladke MOST w Managerze - mozesz go usunac.

WERSJA PYTHON (do wlascicieli kodu):
  Wejdz do folderu axeio_obs i:
    python axeio_obs_bridge.py --list     # pokaze listy urzadzen
    python axeio_obs_bridge.py            # start mostu (auto-wykrywanie)
    python axeio_obs_bridge.py --in "axe io" --out "cable input" --gain -3 --vu
        --gain  - wzmocnienie w dB (np. -6)
        --vu    - wskaznik poziomu w konsoli
        --latency 0.05  - mniejsze opoznienie (gdy potrzebne)
  Albo kliknij dwukrotnie: run_bridge.bat
  Jest tez wersja GUI w Pythonie: axeio_obs_bridge_gui.py

  Most musi dzialac caly czas nagrywania (okno konsoli zostaw otwarte).

OBS - USTAWIENIA (sciezka tylko dla gitary):
  1. Usec -> Dodaj zrodlo -> "Audio Input Capture" -> wybierz "CABLE Output".
  2. W Audio Mixer kliknij kolko zebate / prawym przyciskiem na zrodlo ->
     "Advanced Audio Properties":
        - zrodlo "CABLE Output": odhacz Tylko na sciezce 2 (Track 2),
          a "Desktop Audio"/mikrofon np. na sciezce 1.
  3. Ustawienia -> Output -> Recording -> "Track 1" i "Track 2" zaznaczone.
  4. Po nagraniu: sciezka 1 = cale audio, sciezka 2 = gitara/AXE I/O ONE.

UWAGI:
  - NIE wlaczaj "Desktop Audio" i ONE jednoczesnie na jednej sciezce,
    bo wtedy ONE bedzie 2x (stala faza = dwie kopie do miksu).
  - Jesli OBS ma bled "device not found" na CABLE Output - zamknij i otworz
    OBS PO starcie mostu.
  - Alternatywnie zamiast VB-CABLE mozna uzyc Voicemeeter Banana -
    wtedy most uruchom z:  --out "voicemeeter input"

================================================================================
WIRTUALNY STEROWNIK ASIO (dla DAW - np. Reaper, Studio One, Ableton)
================================================================================
Cel: DAW-wy widza nasz tory/tworzacz jako urzadzenie ASIO "FlexASIO".
Jest zarejestrowany user-mode sterownik (bez sterownika kerna, bez restartu).

CO ZROBIONO:
  - Zainstalowano FlexASIO 1.10b (C:\Program Files\FlexASIO) + rejestracja ASIO.
  - Wygenerowano konfiguracje: %USERPROFILE%\FlexASIO.toml
        wejscie  = "CABLE Output (VB-Audio Virtual Cable)"   (to co routuje most)
        wyjscie  = "Glosniki / Sluchawki"                    (monitoring w DAW)
  - Test sterownika (FlexASIOTest.exe) przeszedl: 2 kin + 2 out, Float32,
    bufor 480 probek (10 ms @48 kHz), streaming OK.

JAK UZYC (w DAW, np. Reaper):
  1. Uruchom nasz most (WLACZ ROUTING w AXE_IO_ONE_OBS_Bridge.exe),
     zeby dzwiek z ONE szedl do kabla.
  2. W DAW: Opcje/Ustawienia audio -> tryb sterownika = ASIO,
     wybierz sterownik "FlexASIO".
  3. Nowa sciezka -> Wejscia (Input) = ch 1/2 (IN 0 / IN 1) = gitara.
  4. Wlacz monitoring sciezki (monitor/REC) - slyszysz siebie bez opoznienia.
  5. OBS dalej nagrywa "CABLE Output" na osobnej sciezce - bez konfliktu
     (WASAPI shared pozwala wielu programom).

TRYW - wejscie bezpośrednio z ONE (bez mostu):
  python setup_flexasio.py --input-hint "axe io one"
  Wtedy DAW bierze probe prosto z ONE; most NIE jest wtedy potrzebny do DAW
  (ale OBS nidy raczej caly czas przez most).

POMOC / REKONFIGURACJA:
  python setup_flexasio.py --dry-run       # podglad konfiguracji
  python setup_flexasio.py --output-hint "" # wylacz wyjscie (sam monitoring z DAW i tak dziala)
  Usun %USERPROFILE%\FlexASIO.toml gdy chcesz przywrocic domyslne ustawienia.
  Logi diagnostyczne FlexASIO: stwórz pusty plik %USERPROFILE%\FlexASIO.log.

================================================================================
SYNTEZA: DZWIEK Z SEPARATY JAM VOX DO OBS (bez DAW)
================================================================================
Chcesz, zeby przetwarzany dzwiek z JAM VOX (wybrany w nim sterownik FlexASIO)
trafil do OBS. Sciezka audio:

  ONE (mikrofon/gitara) -> JAM VOX (obrobka) -> CABLE Input -> kabel
  -> CABLE Output (OBS "Audio Input Capture")

USTAWIENIA (podepnij ONE, potem):
  python setup_flexasio.py --profile jamvox
  Wygeneruje %USERPROFILE%\FlexASIO.toml z:
     [input]  = AXE I/O ONE   (wejscie surowe, NIE z kabla - brak petli)
     [output] = CABLE Input   (obrobiony dzwiek idzie do kabla)

  WAZNE: taki tryb dziala z JAM VOX zamiast mostu (nie razem z mostem) -
  inaczej lecialo by do kabla i surowe, i obrobione.

W JAM VOX: Sterowniki audio -> ASIO -> wybierz "FlexASIO".
W OBS: zrodlo "Audio Input Capture" = CABLE Output (jak wczesniej) ->
  w Audio Mixer wlacz na tym zrodle MONITORING ("Monitor and Output") -
  wtedy slyszysz obrobiony dzwiek przez glosniki i nagrywasz go w OBS.

Wybor sciezek w OBS bez zmian: CABLE Output = Track 2, reszta = Track 1.

PRZELACZANIE TRYBU:
  - tryb "JAM VOX do OBS":  python setup_flexasio.py --profile jamvox
  - tryb "zwykly monitoring": python setup_flexasio.py --profile obs
  (FlexASIO przechodzi na nowa konfiguracje po restarcie aplikacji, ktora go
  otwiera, np. po zamknieciu i otwarciu JAM VOX).

================================================================================
WYCZKA OBS: ASIO CAPTURE (DAW/JAM VOX -> OBS bezpośrednio)
================================================================================
Trzecia zakladka Managera: [ ASIO Capture (wtyczka) ].
Bazuje na projekcie https://github.com/emersound/DAW-ASIO-Capture
(jego sterownik-asio-proxy.dll + zrodlo OBS obs-daw-capture.dll zbudowane
lokalnie pod OBS 32.2.2 i wgrare do C:\Program Files\obs-studio\obs-plugins\64bit\).

CO ROBI:
  - Przechwytuje wyjscie audio DOWOLNEGO programu korzystajacego z ASIO
    (DAW: Reaper/Studio One/Ableton, lub JAM VOX ze sterownikiem ASIO)
    i podaje je jako zrodlo "DAW Audio Capture (ASIO)" w OBS.
  - NIE wymaga wirtualnego kabla dla tego zrodla: asio-proxy.dll przekierowuje
    (redirect w HKLM) wskazany sterownik ASIO do wspolnej pamieci, a zrodlo
    OBS czyta z niej. Opoznienie czytania domyslnie 4 ms (ustawiane w
    wlasciwosciach zrodla: "Capture lag (ms)", zakres 0-250).

  - WERSJA NISKOLATENCYJNA (2026-10): czytnik OBS pobiera teraz porcje po
    64 klatki zamiast 256 (twarda dolna granica opoznienia spadla z ~5.8 ms
    do ~1.3 ms @48 kHz). Budzenie czytnika skrocono z 20 ms do 2 ms, a prog
    "snap" skaluje sie z buforem ASIO (koniec gubienia dzwieku przy duzych
    buforach). Dlatego MINIMALNA latencja zrodla to teraz ok.
        bufor ASIO + "Capture lag" + ~1.3 ms
    np. bufor 64 klatki (1.3 ms) + Capture lag 4 ms + 1.3 ms ~= 6.6 ms.

ZAKLADKA UMOZLIWIA:
  - Status wtyczki (czy asio-proxy.dll i obs-daw-capture.dll sa w OBS).
  - "Zainstaluj / aktualizuj wtyczke" - kopiuje wbudowane DLL do OBS (UAC).
  - Lista sterownikow ASIO (HKLM\SOFTWARE\ASIO) ze stanem redirectu
    (kolumna "Stan": wtyczka / zwykly), w tym FlexASIO.
  - "Przywroc zaznaczony" / "Przywroc wszystkie" - cofa redirect (przywraca
    oryginalna DLL sterownika w rejestrze), gdyby DAW mial problemy.

JAK UZYC:
  1. Zakladka ASIO Capture -> "Zainstaluj / aktualizuj wtyczke" (UAC).
  2. OBS: Dodaj zrodlo -> "DAW Audio Capture (ASIO)" -> wybierz sterownik
     (np. "FlexASIO" gdy JAM VOX gra na nim, albo "AXE IO ONE").
  3. Uruchom DAW/JAM VOX z tym samym sterownikiem ASIO - dzwiek trafia do OBS.

================================================================================
JAM VOX = PROGRAM 32-BITOWY (redirect 32-bit)
================================================================================
JAM VOX (32-bit) czytuje rejestr ASIO z galezi WOW6432Node (widok 32-bit).
Redirect 64-bit, który ustawia wtyczka w OBS, NIE lapie JAM VOX.
Dlatego na zakladce ASIO Capture jest przycisk:

  - "Zainstaluj redirect 32-bit (JAM VOX)"  (po zaznaczeniu sterownika
    np. "AXE IO ONE" na liscie):
      * kopiuje asio-proxy-x86.dll (32-bitowe proxy) do
        C:\Program Files\obs-studio\obs-plugins\64bit\,
      * w widoku 32-bit (HKLM\SOFTWARE\WOW6432Node\Classes\CLSID\...
        \InprocServer32) ustawia proxy i zapisuje oryginalna DLL jako
        "OriginalServer".
  - "Przywroc redirect 32-bit" - cofa to: przywraca oryginal i usuwa
    "OriginalServer".

UWAGI:
  - JAM VOX musi ladowac STEROWNIK przez ten sam CLSID co OBS (opisywane jako
    "AXE IO ONE" bez [OBS] - to normalne; nie mylic z brakiem proxy).
  - Jesli JAM VOX po redirect pokaze "error to open device": najczesciej
    znaczy ze proxy ma zle eksporty DllGetClassObject (32-bit musi exposowac
    NAZWY UNDECORATED jak oryginalna DLL IKM) - sprawdz dumpbin /exports.
- Latencja: JAM VOX/sterownik narzuca wlasny bufor (256 klatek ~5.8 ms
     @44.1 kHz); dodatkowo czytnik OBS ma suwak "Capture lag (ms)" (domyslnie
     4 ms). Aby zejsc jeszcze nizej: zmniejsz bufor ASIO w programie/sterowniku
     (np. FlexASIO: setup_flexasio.py --buffer-ms 4) oraz ustaw "Capture lag"
     na 2-3 ms. Pelna sciezka to: bufor ASIO + "Capture lag" + ~1.3 ms.

================================================================================
MONITORING BEZ OBS: ODSLUCH Z REGULACJA BUFORA (zakladka "Monitoring (bez OBS)")
================================================================================
Odsluch tego, co gra program ASIO (JAM VOX / DAW), bez uruchamiania OBS.
To nie jest nagrywanie - tylko SLUCHANIE w czasie rzeczywistym.

CO ROBI:
  - Uzywa DOKLADNIE tego samego proxy co OBS (ten sam asio-proxy.dll, ten sam
    redirect, ta sama wspolna pamiec) - nie trzeba OBS do odsluchu.
  - Wyjscie otwierane w ZEGARZE ASIO (samplerate = zegar zrodla), wiec klatki
    leca 1:1: ZERO konwersji, zero resamplingu, zero regulacji - prosty wzor z
    czytnika DAW-ASIO-Capture (daw-source.cpp).
  - Pokretlo "ms opoznienia" (4-250, domyslnie 4) to NIE bufor-dodatkowy, tylko
    odleglosc, w jakiej czytnik trzyma sie ZA piszacym: im mniejsza, tym blizej
    zywej krawedzi (krotsza droga, wieksze ryzyko plucia); wieksza = bezpieczniej.
    Dolna granica spadla z 8 ms do 4 ms, bo odczyt idzie teraz blokami po 128
    klatek (~2.9 ms @44.1 kHz) zamiast 256 (~5.8 ms).
  - "przerwy" = klatki, ktorych zrodlo nie zdazylo dostarczyc (zasklepiane
    ostatnia probka, zeby nie trzaskalo); "przeskoki" = momenty, gdy pisarz
    przeskoczyl daleko (pauza/restart) i glowka skaczze za nim. Oba liczniki
    rosna tylko od NEW ZDAR ZEGARA - przy rownych zegarach powinno byc 0/0.

WYMAGA:
  - Aktywny redirect wskazanego sterownika ASIO (najprosciej: raz dodac zrodlo
    "DAW Audio Capture (ASIO)" w OBS, albo uzyc przyciskow na zakladce
    ASIO Capture) ORAZ program ASIO, ktory rzeczywiscie gra - dopiero wtedy
    istnieje wspolna pamiec (inaczej blad "pusta wspolna pamiec").

JAK UZYC:
   0. (raz na zawsze) Redirect dla sterownika uzywanego przez program -
      64-bit: dodaj raz zrodlo "DAW Audio Capture (ASIO)" w OBS; 32-bit:
      zakladka ASIO Capture -> "Zainstaluj redirect 32-bit (JAM VOX)".
      JAM VOX jest 32-BITOWY: sam redirect 64-bit z OBS go nie lapie.
   1. Zakladka Monitoring (bez OBS) -> zaznacz "Routing automatyczny
      (jak zrodlo w OBS)" i wybierz urzadzenie wyjsciowe oraz pare kanalow.
   2. Uruchom JAM VOX / DAW i w programie wybierz ASIO (JAM VOX: Sterowniki
      audio > ASIO > ten sam sterownik co redirect) - gdy program zacznie
      grac, routing PODEPNIE SIE SAM (bez zadnych przyciskow).
   3. Jak sie pluje -> zwieksz "ms opoznienia". Jak chcesz mocniej
      monitorowac na zywo -> zmniejsz. Po wyjsciu programu routing sam sie
      rozpie i poczeka na nastepny (watek tla, GUI nie zamula).

   KLUCZOWE: wspolna pamiec NIE istnieje, dopoki program ASIO nie otworzyl
   przekierowanego sterownika I nie gra; wtedy routing nie ma czego czytac.
   Panel u gory zakladki (odswiezany w tle) pokazuje ktore ogniwo nie gra:
     lancuch [redirect -> ASIO program -> pamiec]
     redirect x64/32-bit : TAK/NIE  (brak -> przycisk "Zainstaluj redirect...")
     program ASIO        : nazwy uruchomionych hostow ASIO
     routing             : PODPIETE - leci 1:1 / czeka na proxy / BLAD startu
     JAM VOX wyjscie audio: "Windows Audio / ..." = zle, => wybierz ASIO;
     status logu proxy i tego, czy writePos rosnie.
   Najczestsza przyczyna bledu "pusta wspolna pamiec": program gra przez
   Windows Audio, nie przez ASIO - wtedy redirect jest "wylaczony z gry".

UWAGI:
  - Monitorowanie NIE zapisuje readPos we wspolnej pamieci, wiec Proxy i zrodlo
    OBS moga dzialac rownoczesnie z odsluchem.
  - Zachowanie przy rozjezdzie zegarow (naturalne): zrodlo wolniejsze od wyjscia
    -> okresowe przerwy; szybsze -> okresowe przeskoki. To cecha czytnika "lag
    za piszacym" (jak w daw-source.cpp) - celowo bez konwertera.
  - Bluetooth (Jabra) i HDMI (LG TV) maja w Windows twardy floor ~22 ms -
    odsluch nie bedzie szybszy niz to.

================================================================================
PRZENIESIENIE NA DRUGI KOMPUTER
================================================================================
Tak - dziala identycznie na drugim komputerze. Potrzebne:

  1. Skopiuj AXE_IO_ONE_OBS_Manager.exe na drugi komputer.
  2. Zainstaluj na nim: OBS Studio (najlepiej 32.2.2 / wersja 32.x),
     sterownik AXE IO ONE (Control Panel) i JAM VOX.
     UWAGA: wtyczka jest zbudowana pod OBS API 32 (32.2.x). Jesli drugi OBS
     ma inny glowny numer API (np. 33), wtyczka sie nie zaladuje - trzeba ja
     przebudowac na tym komputerze (patrz sekcja DAW-ASIO-Capture).
  3. OBS: Dodaj zrodlo -> "DAW Audio Capture (ASIO)" -> ASIO Driver =
     "AXE IO ONE". To automatycznie ustawi redirect 64-bit.
  4. Manager -> zakladka ASIO Capture -> zaznacz "AXE IO ONE" ->
     kliknij "Zainstaluj redirect 32-bit (JAM VOX)"  (UAC).
     (Ten krok jest wazny - JAM VOX jest 32-bitowy i sam redirect z OBS go nie
     obejmuje.)
  5. JAM VOX -> Options -> Preferences... -> Hardware/Performance ->
     Audio Device = AXE IO ONE. Graj - metr w OBS powinien drgnac.
  6. (Opcjonalnie) Capture lag w zrodle ustaw np. 2-4 ms (domyslne 4 ms).

  W jakim plikach jest cala magia (do recznego skopiowania gdyby nie chcialo
  sie uzywac Managera):
    C:\Program Files\obs-studio\obs-plugins\64bit\obs-daw-capture.dll
    C:\Program Files\obs-studio\obs-plugins\64bit\asio-proxy.dll       (x64)
    C:\Program Files\obs-studio\obs-plugins\64bit\asio-proxy-x86.dll   (x86/JAM VOX)
  plus redirecty w rejestrze (64-bit robi wtyczka w OBS, 32-bit -> przycisk
  w Managerze lub recznie reg.exe na WOW6432Node).