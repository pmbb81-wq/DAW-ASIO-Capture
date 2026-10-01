; =====================================================================
;  AXE IO ONE -> OBS Audio Capture
;  Instalator: wtyczka OBS przechwytujaca wyjscie ASIO z AXE IO ONE
;  (uzywane przez JAM VOX) i przekazujaca je na stream / nagranie.
;
; Instaluje:
;   - obs-daw-capture.dll, asio-proxy.dll, asio-proxy-x86.dll
;     do <OBS>\obs-plugins\64bit
;   - przekierowanie rejestru 32-bit (WOW6432Node) dla CLSID AXE IO ONE
;     na asio-proxy-x86.dll, z zapamietaniem OriginalServer
;   - opcjonalnie FlexASIO (alternatywny tor audio)
;
; NIE modyfikuje plikow scen OBS - zrodlo dodaje sie recznie,
; instrukcja jest w INSTRUKCJA.txt i na ostatniej stronie instalatora.
; =====================================================================

#define AppName        "AXE IO ONE - OBS Audio Capture"
#define AppVersion     "1.2.2"
#define AppIdGuid      "{{7C4B1E92-3D5A-4F18-9B62-1A8E0D3C74F1}"
#define AppExeName     "AXE-IO-ONE-OBS-Audio-Capture-Setup"
#define PublisherName  "AXE IO ONE OBS Capture"
#define DLLNames       "obs-daw-capture.dll,asio-proxy.dll,asio-proxy-x86.dll"

; CLSID sterownika AXE IO ONE (IK Multimedia) - wartosc zapasowa,
; w razie gdyby nie dalo sie go odczytac z HKLM\SOFTWARE\ASIO
#define CLSIDFallback  "{{62CF8386-3DF2-4334-8615-FBD96D6F9B88}}"
#define ASIOKeyName    "AXE IO ONE"
; natywny sterownik VOX/JAM VOX (C:\Windows\System32\JVOXAsio.dll,
; x86 = SysWOW64\JVOXAsio32.dll) - oba eksportuja DllGetClassObject
; bez dekoracji, wiec proxy dziala
#define CLSIDJamVox    "{{1F390745-C530-4eec-9197-7375B06079DB}}"


[Setup]
AppId={#AppIdGuid}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#PublisherName}
VersionInfoVersion={#AppVersion}
; wartosc domyslna - w InitializeSetup() jest nadpisywana wykryta sciezka OBS
DefaultDirName={commonpf}\obs-studio
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename={#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=110
; wymagamy 64-bit Windows - DLL-e sa x64, a JAM VOX jest 32-bitowy
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; instalacja maszynowa: zapis do Program Files i HKLM
PrivilegesRequired=admin
UninstallDisplayName={#AppName}
SetupLogging=yes
CloseApplications=yes
RestartApplications=no
MinVersion=10.0
ShowLanguageDialog=no

[Languages]
Name: "pl"; MessagesFile: "compiler:Languages\Polish.isl"

[Tasks]
Name: "drv_axe";       Description: "AXE IO ONE - przechwytywanie wprost ze sterownika IKM (zalecane)"; GroupDescription: "Sterownik ASIO przechwytywany przez proxy:"; Flags: checkedonce exclusive
Name: "drv_flexasio";  Description: "FlexASIO - przechwytywanie przez FlexASIO"; GroupDescription: "Sterownik ASIO przechwytywany przez proxy:"; Flags: unchecked exclusive
Name: "drv_jamvox";    Description: "JamVOX ASIO Drv - natywny sterownik JAM VOX (C:\Windows\System32\JVOXAsio.dll)"; GroupDescription: "Sterownik ASIO przechwytywany przez proxy:"; Flags: unchecked exclusive
Name: "redirect32";    Description: "Przekieruj rejestr 32-bit (wymagane dla 32-bitowego JAM VOX)"; GroupDescription: "Dodatkowe komponenty:"; Flags: checkedonce
Name: "flexasio";      Description: "Zainstaluj FlexASIO (wymagane przy trybie FlexASIO)"; GroupDescription: "Dodatkowe komponenty:"; Flags: unchecked
Name: "flexasio_toml"; Description: "Zapisz %USERPROFILE%\FlexASIO.toml (wejscie = AXE I/O ONE)"; GroupDescription: "Dodatkowe komponenty:"; Flags: unchecked

[Files]
; --- wtyczka OBS + proxy ---
Source: "..\installers\obs-daw-capture.dll"; DestDir: "{app}\obs-plugins\64bit"; Flags: ignoreversion
Source: "..\installers\asio-proxy.dll";      DestDir: "{app}\obs-plugins\64bit"; Flags: ignoreversion
Source: "..\installers\asio-proxy-x86.dll";  DestDir: "{app}\obs-plugins\64bit"; Flags: ignoreversion
; --- FlexASIO: wypakowany do {tmp}, uruchamiany i usuwany po instalacji ---
Source: "..\installers\FlexASIO-1.10b.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; Tasks: flexasio
; --- Manager: zakladka "Nagrywarka (DAW)" + FlexASIO + redirecty ---
Source: "..\AXE_IO_ONE_OBS_Manager.exe"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Odinstaluj {#AppName}"; Filename: "{uninstallexe}"
Name: "{group}\AXE IO ONE Manager (nagrywarka DAW)"; Filename: "{app}\AXE_IO_ONE_OBS_Manager.exe"
Name: "{group}\Instrukcja obslugi";   Filename: "{app}\INSTRUKCJA.txt"

[Run]
Filename: "{tmp}\FlexASIO-1.10b.exe"; Parameters: "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-"; \
    StatusMsg: "Instalowanie FlexASIO, prosze czekac..."; Flags: runhidden waituntilterminated; Tasks: flexasio

[Code]
var
  ObsDir: String;
  PluginsDir: String;
  BackupDir: String;
  ProxyX86: String;
  RegKey: String;
  Clsid: String;
  DriverName: String;
  TmpDir: String;
  RedirectMade: Boolean;
  RedirectMsg: String;
  BackedUp: Boolean;

const
  DLL1 = 'obs-daw-capture.dll';
  DLL2 = 'asio-proxy.dll';
  DLL3 = 'asio-proxy-x86.dll';

{ NL - koniec linii (CR LF). Definiowany jako funkcja, poniewaz literal
  sterowniowy w sekcji [Code] bylby przetwarzany przez preprocesor Inno
  jako dyrektywa preprocesora. }
function NL: String;
begin
  Result := Chr(13) + Chr(10);
end;


{ ------------------------------------------------------------------------ }
{  Wykrycie katalogu OBS                                                   }
{ ------------------------------------------------------------------------ }

function DetectObsDir(): String;
var
  InstDir: String;
  Candidates: array[0..3] of String;
  I: Integer;
begin
  Result := '';

  { 1. klucz rejestru instalatora OBS }
  if RegQueryStringValue(HKLM64, 'SOFTWARE\OBS Studio', 'InstallDir', InstDir) then
    if DirExists(AddBackslash(InstDir) + 'obs-plugins') then
    begin
      Result := InstDir;
      Exit;
    end;

  { 2. typowe lokalizacje }
  Candidates[0] := ExpandConstant('{commonpf}\obs-studio');
  Candidates[1] := ExpandConstant('{commonpf32}\obs-studio');
  Candidates[2] := ExpandConstant('{localappdata}\Programs\obs-studio');
  Candidates[3] := ExpandConstant('{userappdata}\obs-studio');

  for I := 0 to 3 do
    if (Result = '') and (DirExists(AddBackslash(Candidates[I]) + 'obs-plugins')) then
      Result := Candidates[I];
end;

{ ------------------------------------------------------------------------ }
{  Wykrycie CLSID wybranego sterownika ASIO                                 }
{ ------------------------------------------------------------------------ }

function FindDriverClsid(const DriverName: String): String;
var
  V: String;
  Keys: array[0..2] of String;
  I: Integer;
begin
  Result := '';
  if DriverName = '' then Exit;

  { szukamy pozycji w HKLM\SOFTWARE\ASIO - widok 64 i 32 bit }
  Keys[0] := 'SOFTWARE\ASIO\' + DriverName;
  Keys[1] := 'SOFTWARE\Classes\ASIO\' + DriverName;
  Keys[2] := 'SOFTWARE\WOW6432Node\ASIO\' + DriverName;

  for I := 0 to 2 do
  begin
    if (Result = '') and
       RegQueryStringValue(HKLM64, Keys[I], 'CLSID', V) and (V <> '') then
      Result := V;
  end;
end;

{ sterownik wybrany przez uzytkownika na stronie zadan }
function ChosenDriver(): String;
begin
  if WizardIsTaskSelected('drv_flexasio') then
    Result := 'FlexASIO'
  else if WizardIsTaskSelected('drv_jamvox') then
    Result := 'JamVOX ASIO Drv'
  else
    Result := 'AXE IO ONE';
end;

{ ------------------------------------------------------------------------ }
{  Inicjalizacja - wykrycie i weryfikacja                                  }
{ ------------------------------------------------------------------------ }

procedure SetupPaths;
begin
  PluginsDir := AddBackslash(ObsDir) + 'obs-plugins\64bit';
  BackupDir  := ObsDir + '_axeio_obs_backup';
  ProxyX86   := AddBackslash(PluginsDir) + DLL3;
end;

{ rozwiązuje sterownik -> CLSID -> klucz InprocServer32 w widoku 32-bit.
  DriverName pusty = jeszcze nie wybrano (InitializeSetup). }
procedure ResolveDriver(const DName: String);
begin
  if DName = '' then DName := 'AXE IO ONE';
  DriverName := DName;
  Clsid := FindDriverClsid(DName);
  if Clsid = '' then
  begin
    if DName = 'AXE IO ONE' then
      Clsid := ExpandConstant('{#CLSIDFallback}')
    else if DName = 'JamVOX ASIO Drv' then
      Clsid := ExpandConstant('{#CLSIDJamVox}');
  end;
  RegKey := 'SOFTWARE\Classes\CLSID\' + Clsid + '\InprocServer32';
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  BackedUp := False;

  ObsDir := DetectObsDir();
  if ObsDir = '' then
  begin
    MsgBox('Nie znaleziono instalacji OBS Studio.' + NL + NL +
           'Zainstaluj OBS Studio (wersja 64-bit) i uruchom ten instalator ponownie.' +
           NL + NL + 'Szukane lokalizacje:' + NL +
           '  ' + ExpandConstant('{commonpf}\obs-studio') + NL +
           '  ' + ExpandConstant('{commonpf32}\obs-studio') + NL +
           '  ' + ExpandConstant('{localappdata}\Programs\obs-studio'),
           mbError, MB_OK);
    Result := False;
    Exit;
  end;

  { OBS musi byc 64-bitowy - nasze DLL sa x64 }
  if not FileExists(AddBackslash(ObsDir) + 'bin\64bit\obs64.exe') then
  begin
    if FileExists(AddBackslash(ObsDir) + 'bin\32bit\obs32.exe') then
      MsgBox('Znaleziono 32-bitowe OBS Studio.' + NL + NL +
             'Ta wtyczka wymaga OBS 64-bit. Zainstaluj wersje 64-bit.',
             mbError, MB_OK)
    else
      MsgBox('W katalogu OBS nie znaleziono obs64.exe.' + NL + NL + ObsDir,
             mbError, MB_OK);
    Result := False;
    Exit;
  end;

  SetupPaths;
  ResolveDriver('');          { wstepna wartosc, zostanie nadpiszona w PrepareToInstall }
end;

{ Ustawienie domyslnego katalogu instalacji musi byc w InitializeWizard -
  w InitializeSetup formularz wizardu nie jest jeszcze utworzony. }
procedure InitializeWizard();
begin
  WizardForm.DirEdit.Text := ObsDir;
end;

{ ------------------------------------------------------------------------ }
{  Deinstalacja: ponowne wykrycie sciezek (InitializeSetup tu nie dziala) }
{ ------------------------------------------------------------------------ }

function InitializeUninstall(): Boolean;
begin
  Result := True;
  ObsDir := DetectObsDir();
  if ObsDir = '' then
    ObsDir := ExpandConstant('{commonpf}\obs-studio');
  SetupPaths;
  ResolveDriver('');        { nieistotne - uninstaller skanuje wszystkie sterowniki }
end;

{ ------------------------------------------------------------------------ }
{  Kopia zapasowa istniejacych DLL-i (przed nadpisaniem)                   }
{ ------------------------------------------------------------------------ }

procedure BackupExistingDlls;
var
  Names: array[0..2] of String;
  I: Integer;
begin
  if BackedUp then Exit;
  BackedUp := True;

  Names[0] := DLL1;
  Names[1] := DLL2;
  Names[2] := DLL3;

  for I := 0 to 2 do
    if FileExists(AddBackslash(PluginsDir) + Names[I]) then
      CopyFile(AddBackslash(PluginsDir) + Names[I],
               AddBackslash(BackupDir) + Names[I] + '.orig', True);
end;

{ ------------------------------------------------------------------------ }
{  Przekierowanie rejestru 32-bit                                          }
{ ------------------------------------------------------------------------ }

procedure DoRedirect32;
var
  Cur: String;
  ExistingOrig: String;
  Verify: String;
  HaveCur: Boolean;
  HaveOrig: Boolean;
begin
  RedirectMade := False;
  RedirectMsg  := '';

  HaveCur := RegQueryStringValue(HKLM32, RegKey, '', Cur);
  HaveOrig := RegQueryStringValue(HKLM32, RegKey, 'OriginalServer', ExistingOrig);

  if not HaveCur then
  begin
    RedirectMsg := 'Brak 32-bitowego wpisu InprocServer32 dla ' + Clsid + '.' + NL +
                   'Sterownik ASIO nie jest zarejestrowany w widoku 32-bit, wiec' + NL +
                   'przekierowanie nie zostalo ustawione. JAM VOX nie bedzie' + NL +
                   'przechwytywany przez OBS.';
    Log(RedirectMsg);
    Exit;
  end;

  { juz przekierowany - nie nadpisujemy OriginalServer }
  if HaveOrig and (ExistingOrig <> '') and
     (Pos(Lowercase(DLL3), Lowercase(Cur)) > 0) then
  begin
    RedirectMade := True;
    RedirectMsg  := 'Przekierowanie 32-bit juz bylo aktywne (bez zmian).';
    Log(RedirectMsg);
    Exit;
  end;

  RegWriteStringValue(HKLM32, RegKey, 'OriginalServer', Cur);
  RegWriteStringValue(HKLM32, RegKey, '', ProxyX86);

  if not (RegQueryStringValue(HKLM32, RegKey, '', Verify) and
          (Pos(Lowercase(DLL3), Lowercase(Verify)) > 0)) then
  begin
    RedirectMsg := 'Nie udalo sie zapisac przekierowania w rejestrze.';
    Log(RedirectMsg);
    Exit;
  end;

  RedirectMade := True;
  RedirectMsg  := 'Przekierowanie 32-bit ustawione:' + NL +
                  '  byl:  ' + Cur + NL +
                  '  jest: ' + ProxyX86;
  Log(RedirectMsg);
end;

{ ------------------------------------------------------------------------ }
{  Plik instrukcji                                                        }
{ ------------------------------------------------------------------------ }

procedure WriteReadme;
var
  S: String;
begin
  S :=
    'AXE IO ONE -> OBS Audio Capture ' + '{#AppVersion}' + NL + NL +
    'OBS: ' + ObsDir + NL +
    'Sterownik ASIO: ' + DriverName + NL +
    'CLSID: ' + Clsid + NL +
    'Dostepne w instalatorze: AXE IO ONE, JamVOX ASIO Drv, FlexASIO.' + NL +
    'OBS i JAM VOX musza uzywac tego samego sterownika.' + NL + NL +
    '--- CO ZROBIC PO INSTALACJI ---' + NL + NL +
    '1. Uruchom OBS.' + NL +
    '2. Zrodla > Dodaj > Wejscia > "DAW Audio Capture (ASIO)".' + NL +
    '3. Dodaj zrodlo do sceny.' + NL +
    '4. Wlasciwosci zrodla:' + NL +
    '     ASIO Driver       = ' + DriverName + NL +
    '     Output Pair       = Out 1-2 (Master)' + NL +
    '     Capture lag (ms)  = 20' + NL +
    '     Audio Monitoring  = Monitor Off' + NL +
    '     Monitoring Device = dowolne (przy Monitor Off nie jest uzywane)' + NL +
    '5. Dopiero teraz uruchom JAM VOX.' + NL + NL +
    'Kolejnosc jest istotna: OBS musi dzialac przed JAM VOX.' + NL + NL +
    'OBS i JAM VOX musza uzywac TEGO SAMEGO sterownika ASIO: ' + DriverName + '.' + NL + NL +
    '--- WERYFIKACJA ---' + NL + NL +
    'W logu OBS (Pomniki, Pliki, Dzienniki, Pokaz dziennik) powinna' + NL +
    'pojubic sie linia:' + NL +
    '  [DAW Capture] AudioThread: ... outPeak=0.xxxxxx' + NL + NL +
    'Miernik zrodla w mikserze OBS powinien sie ruszac, gdy JAM VOX' + NL +
    'odtwarza dzwiek. Plik logu:' + NL +
    '  %APPDATA%\obs-studio\logs' + NL + NL +
    '--- UWAGA ---' + NL + NL +
    'Instalator NIE zmienia zadnych plikow scen OBS. Kazdy dodaje' + NL +
    'zrodlo recznie we wlasnej kolekcji.' + NL + NL +
    'JAM VOX (32-bit) musi zostac zamkniety i uruchomiony ponownie,' + NL +
    'aby zobaczyc nowe przekierowanie rejestru.' + NL + NL +
    '--- JAM VOX: TRZEBA PRZELACZYC WYJSCIE NA ASIO ---' + NL + NL +
    'Proxy przechwytuje TYLKO strumien ASIO. Dopoki JAM VOX gra przez' + NL +
    '"Windows Audio" (tak jest domyslnie), w OBS bedzie cisza - redirecty' + NL +
    'moga byc w pelni poprawne, a i tak nie ma czego przechwycic.' + NL + NL +
    'Manager pokazuje to w polu "JAM VOX - wyjscie audio". Jesli pisze' + NL +
    'PROBLEM, uzyj przycisku "Przelacz JAM VOX na ASIO" (Manager zrobi kopie' + NL +
    'pliku %APPDATA%\VOX\JamVOX\Preferences3.xml).' + NL + NL +
    'Alternatywnie recznie w JAM VOX: Sterowniki audio > ASIO > ten sam' + NL +
    'sterownik co w OBS.' + NL + NL +
    'JAM VOX musi byc wylaczony (takze proces InitJam.exe), inaczej plik' + NL +
    'konfiguracyjny zostanie nadpisany przy wyjsciu z programu.' + NL + NL +
    '--- NAGRYWARKA (DAW) ---' + NL + NL +
    'AXE_IO_ONE_OBS_Manager.exe to nagrywarka strumienia ASIO.' + NL +
    'Nagrywa dokladnie to, co DAW/JAM VOX wysyla przez ASIO, prosto z' + NL +
    'tej samej wspolnej pamieci co zrodlo OBS - bez kabli wirtualnych i' + NL +
    'bez domieszki monitoringu sprzetowego. Dziala rownoczesnie z' + NL +
    'zrodlem OBS (kazdy ma wlasna pozycje odczytu).' + NL + NL +
    '1. Uruchom nagrywarke - przycisk "Sprawdz proxy".' + NL +
    '2. Ustaw folder, prefiks, format (WAV, opcjonalnie MP3) i pare' + NL +
    '   wyjscia (domyslnie Out 1-2).' + NL +
    '3. Wymagane: redirect ASIO aktywny (ustawiony przez ten instalator)' + NL +
    '   i uruchomiony host grajacy przez ASIO.' + NL +
    '4. START ... STOP. Gotowy plik pojawi sie w wybranym folderze.' + NL + NL +
    'Formaty: WAV (natywnie) oraz MP3, FLAC, OGG, M4A - ffmpeg jest' + NL +
    'wlaczony w aplikacje, wiec wszystkie dzialaja od razu.' + NL + NL +
    '--- ODINSTALOWANIE ---' + NL + NL +
    'Panel sterowania > Programy > AXE IO ONE - OBS Audio Capture.' + NL +
    'Przywracany jest oryginalny sterownik ASIO z rejestru oraz' + NL +
    'poprzednie wersje plikow DLL (jesli byly).' + NL;

  SaveStringToFile(AddBackslash(ObsDir) + 'INSTRUKCJA.txt', S, False);
end;

{ ------------------------------------------------------------------------ }
{  FlexASIO.toml - konfiguracja wejscia/wyjscia sterownika FlexASIO          }
{  Urzadzenia WASAPI sa wykrywane uruchomieniem PortAudioDevices.exe.      }
{ ------------------------------------------------------------------------ }

function FindPortAudioDevices(): String;
var
  Dirs: array[0..1] of String;
  I: Integer;
  C: String;
begin
  Dirs[0] := ExpandConstant('{commonpf}\FlexASIO');
  Dirs[1] := ExpandConstant('{commonpf32}\FlexASIO');
  Result := '';
  for I := 0 to 1 do
  begin
    C := AddBackslash(AddBackslash(Dirs[I]) + 'x64') + 'PortAudioDevices.exe';
    if FileExists(C) then
    begin
      Result := C;
      Exit;
    end;
  end;
end;

{ Zwraca nazwe pierwszego urzadzenia WASAPI zawierajacej Needle
  (bez rozroznienia wielkosci liter), pomijajac [Loopback]. }
function FindWasapiDevice(const Needle: String): String;
var
  Lines: TArrayOfString;
  PadExe, OutFile, BatchFile, Line, Nm, LowNeedle: String;
  I, P, Q: Integer;
  ResultCode: Integer;
begin
  Result := '';
  PadExe := FindPortAudioDevices;
  if PadExe = '' then Exit;

  TmpDir := AddBackslash(ExpandConstant('{tmp}'));
  OutFile := AddBackslash(TmpDir) + 'axeio_pa.txt';
  BatchFile := AddBackslash(TmpDir) + 'axeio_pa.cmd';
  DeleteFile(OutFile);

  SaveStringToFile(BatchFile,
    '@echo off' + NL + '"' + PadExe + '" > "' + OutFile + '" 2>&1' + NL, False);
  if not Exec('cmd.exe', '/c "' + BatchFile + '"', '', SW_HIDE,
              ewWaitUntilTerminated, ResultCode) then
    Exit;

  if not LoadStringsFromFile(OutFile, Lines) then Exit;

  LowNeedle := Lowercase(Needle);
  for I := 0 to GetArrayLength(Lines) - 1 do
  begin
    Line := Lines[I];
    P := Pos('name[', Line);
    if P = 0 then Continue;
    Line := Copy(Line, P + 5, MaxInt);
    Q := Pos(']', Line);
    if Q = 0 then Continue;
    Nm := Copy(Line, 1, Q - 1);
    if (Pos(Lowercase(Nm), LowNeedle) > 0) and
       (Pos('[loopback]', Lowercase(Nm)) = 0) then
    begin
      Result := Nm;
      Exit;
    end;
  end;
end;

procedure WriteFlexAsioToml;
var
  Dev, TomlPath, Toml, Home: String;
begin
  Home := GetEnv('USERPROFILE');
  if Home = '' then Home := ExpandConstant('{userappdata}') + '\..';
  TomlPath := AddBackslash(Home) + 'FlexASIO.toml';

  Dev := FindWasapiDevice('AXE');
  if Dev = '' then Dev := FindWasapiDevice('IO ONE');
  if Dev = '' then
  begin
    Log('FlexASIO: nie znaleziono urzadzenia wejsciowego AXE - '
        + 'plik FlexASIO.toml NIE zostal zapisany.');
    Exit;
  end;

  Toml := '# Wygenerowane przez instalator AXE IO ONE - OBS Audio Capture' + NL +
         '# Wejscie: surowe wejscie AXE I/O ONE (bez petli zwrotnej).' + NL +
         '# Wyjscie: celowo puste - dzwiek ma byc przechwytywany przez' + NL +
         '# proxy asio do OBS, nie odtwarzany lokalnie.' + NL +
         '# Chcesz sluchac lokalnie, dodaj sekcje [output] ponizej, np.:' + NL +
         '#' + NL +
         '# [output]' + NL +
         '# device = "Sluchawki"' + NL +
         '# channels = 2' + NL + NL +
         'backend = "Windows WASAPI"' + NL +
         'bufferSizeSamples = 480' + NL + NL +
         '[input]' + NL +
         'device = "' + Dev + '"' + NL +
         'channels = 2' + NL;

  SaveStringToFile(TomlPath, Toml, False);
  Log('FlexASIO: zapisano ' + TomlPath + ' (wejscie = ' + Dev + ')');
end;

{ ------------------------------------------------------------------------ }
{  Kroki instalacji                                                        }
{ ------------------------------------------------------------------------ }

{ Kopia zapasowa istniejacych DLL-i + rozpoznanie wybranego sterownika.
  Wywolywana tu, aby na pewno wykonac sie PRZED zapisem nowych plikow. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  ResolveDriver(ChosenDriver());

  if (DriverName = 'FlexASIO') and not WizardIsTaskSelected('flexasio') then
  begin
    Result := 'Wybrano tryb FlexASIO, ale nie zaznaczono zadania "Zainstaluj FlexASIO".' + NL +
              'Wroc do poprzedniej strony i zaznacz instalacje FlexASIO, albo wroc i wybierz tryb AXE IO ONE.';
    Exit;
  end;

  if Clsid = '' then
  begin
    if DriverName = 'FlexASIO' then
      Result := 'Nie znaleziono sterownika FlexASIO w HKLM\SOFTWARE\ASIO.' + NL +
                'Zainstaluj FlexASIO (zaznacz zadanie), a nastepnie uruchom instalator ponownie.'
    else
      Result := 'Nie znaleziono sterownika "' + DriverName + '" w HKLM\SOFTWARE\ASIO.' + NL +
                'Zainstaluj sterownik AXE IO ONE (Control Panel) i uruchom instalator ponownie.';
    Exit;
  end;

  BackupExistingDlls;
  Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    WriteReadme;
    if WizardIsTaskSelected('redirect32') then
      DoRedirect32
    else
      RedirectMsg := 'Przekierowanie 32-bit pominiete (odznaczono zadanie).';
  end
  else if CurStep = ssDone then
  begin
    { tu FlexASIO jest juz zainstalowany przez wpis [Run] }
    if WizardIsTaskSelected('flexasio_toml') then
      WriteFlexAsioToml;
  end;
end;

{ ------------------------------------------------------------------------ }
{  Strona koncowa - instrukcja                                             }
{ ------------------------------------------------------------------------ }

procedure CurPageChanged(CurPageID: Integer);
var
  S: String;
begin
  if CurPageID <> wpFinished then Exit;

  S := 'Instalacja zakonczona.' + NL + NL +
       'OBS: ' + ObsDir + NL +
       'Sterownik ASIO (CLSID): ' + Clsid + NL;

  if RedirectMsg <> '' then
    S := S + NL + RedirectMsg + NL;

    S := S + NL + '--- CO TERAZ ZROBIC ---' + NL + NL +
       '1. Uruchom OBS.' + NL +
       '2. Zrodla > Dodaj > Wejscia > "DAW Audio Capture (ASIO)".' + NL +
       '3. Dodaj zrodlo do sceny.' + NL +
       '4. Wlasciwosci zrodla:' + NL +
       '     ASIO Driver       = ' + DriverName + NL +
       '     Output Pair       = Out 1-2 (Master)' + NL +
       '     Capture lag (ms)  = 20' + NL +
       '     Audio Monitoring  = Monitor Off' + NL +
       '     Monitoring Device = dowolne (przy Monitor Off nie jest uzywane)' + NL +
       '5. Dopiero teraz uruchom JAM VOX.' + NL + NL +
       'Sterownik musi byc TYM SAMYM w OBS i w JAM VOX: ' + DriverName + '.' + NL + NL +
       'Chcesz nagrywac strumien DAW do WAV/MP3? Uruchom z menu Start:' + NL +
       '  AXE IO ONE Manager (nagrywarka DAW)' + NL + NL +
       'Kolejnosc jest istotna: OBS musi dzialac przed JAM VOX.' + NL + NL +
       'Weryfikacja: w logu OBS szukaj linii' + NL +
       '  [DAW Capture] AudioThread: ... outPeak=0.xxxxxx' + NL + NL +
       'JAM VOX musi zostac zamkniety i uruchomiony ponownie, zeby' + NL +
       'zobaczyc nowe przekierowanie rejestru.' + NL + NL +
       'Pelna instrukcja: ' + AddBackslash(ObsDir) + 'INSTRUKCJA.txt';

  MsgBox(S, mbInformation, MB_OK);
end;

{ ------------------------------------------------------------------------ }
{  Deinstalacja - przywrocenie oryginalnego sterownika                    }
{ ------------------------------------------------------------------------ }

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Orig, Clsid, CurDll, RegKey: String;
  Names: array[0..2] of String;
  I: Integer;
  Bck: String;
begin
  { INSTRUKCJA.txt powstaje dopiero w trakcie instalacji, wiec Inno go nie
    zna i nie usunie sam. Kasujemy go na POCZATKU uninstallu - przy
    usPostUninstall katalog instalacji jest juz oczyszczany, a decyzja
    o jego usunieciu zapada wczesniej. }
  if CurUninstallStep = usUninstall then
  begin
    if DeleteFile(AddBackslash(ObsDir) + 'INSTRUKCJA.txt') then
      Log('Deinstalacja: usunieto INSTRUKCJA.txt');
    Exit;
  end;

  if CurUninstallStep <> usPostUninstall then Exit;

  { 1. Przywroc oryginalny serwer COM w widoku 32-bit. Szukamy wszystkich
       sterownikow, ktore obecnie wskazuja na asio-proxy-x86.dll - nie
       wiemy, ktory sterownik zostal wybrany przy instalacji, wiec cofamy
       wszystkie aktywne redirecty wtyczki. }
  Clsid := FindDriverClsid('AXE IO ONE');
  if Clsid = '' then
    Clsid := ExpandConstant('{#CLSIDFallback}');
  RegKey := 'SOFTWARE\Classes\CLSID\' + Clsid + '\InprocServer32';
  if RegQueryStringValue(HKLM32, RegKey, '', CurDll) and
     (Pos(Lowercase(DLL3), Lowercase(CurDll)) > 0) then
  begin
    if RegQueryStringValue(HKLM32, RegKey, 'OriginalServer', Orig) and (Orig <> '') then
      RegWriteStringValue(HKLM32, RegKey, '', Orig);
    RegDeleteValue(HKLM32, RegKey, 'OriginalServer');
    Log('Deinstalacja: cofnieto redirect dla ' + Clsid);
  end;

  Clsid := FindDriverClsid('FlexASIO');
  if Clsid <> '' then
  begin
    RegKey := 'SOFTWARE\Classes\CLSID\' + Clsid + '\InprocServer32';
    if RegQueryStringValue(HKLM32, RegKey, '', CurDll) and
       (Pos(Lowercase(DLL3), Lowercase(CurDll)) > 0) then
    begin
      if RegQueryStringValue(HKLM32, RegKey, 'OriginalServer', Orig) and (Orig <> '') then
        RegWriteStringValue(HKLM32, RegKey, '', Orig);
      RegDeleteValue(HKLM32, RegKey, 'OriginalServer');
      Log('Deinstalacja: cofnieto redirect dla FlexASIO');
    end;
  end;

  Clsid := FindDriverClsid('JamVOX ASIO Drv');
  if Clsid = '' then
    Clsid := ExpandConstant('{#CLSIDJamVox}');
  RegKey := 'SOFTWARE\Classes\CLSID\' + Clsid + '\InprocServer32';
  if RegQueryStringValue(HKLM32, RegKey, '', CurDll) and
     (Pos(Lowercase(DLL3), Lowercase(CurDll)) > 0) then
  begin
    if RegQueryStringValue(HKLM32, RegKey, 'OriginalServer', Orig) and (Orig <> '') then
      RegWriteStringValue(HKLM32, RegKey, '', Orig);
    RegDeleteValue(HKLM32, RegKey, 'OriginalServer');
    Log('Deinstalacja: cofnieto redirect dla JamVOX ASIO Drv');
  end;

  { 2. przywroc poprzednie wersje DLL, jesli ktos je mial }
  Names[0] := DLL1;
  Names[1] := DLL2;
  Names[2] := DLL3;
  for I := 0 to 2 do
  begin
    Bck := AddBackslash(BackupDir) + Names[I] + '.orig';
    if FileExists(Bck) then
      CopyFile(Bck, AddBackslash(PluginsDir) + Names[I], True);
  end;

  { 3. posprzataj katalog kopii zapasowych }
  DelTree(BackupDir, True, True, True);
end;
