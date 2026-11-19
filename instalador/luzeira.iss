; Instalador de Luzeira (Inno Setup 6). Decisión D-12, fase 6.
;
; No se compila a mano: lo hace scripts/empaquetar.py, que monta antes build\Luzeira y pasa
;   ISCC /DAppVersion=<versión de pyproject.toml> /DSourceDir=... /DOutputDir=... /DIconFile=...
;
; Qué hace:
; - Instala solo para el usuario actual, SIN admin ni UAC (PrivilegesRequired=lowest), en
;   %LOCALAPPDATA%\Programs\Luzeira. La desinstalación queda registrada en HKCU.
; - La app es el Python embebible oficial: los accesos directos lanzan
;   python\pythonw.exe -m luzeira (pythonw.exe va firmado por la Python Software Foundation).
; - Si Luzeira está abierta, el instalador y el desinstalador lo detectan por su mutex
;   (la app crea «Local\Luzeira»; aquí basta «Luzeira»: sin prefijo, Windows lo busca en el
;   espacio de nombres de la sesión, que es «Local\»).
; - Al desinstalar borra el autoarranque y PREGUNTA antes de borrar temas y configuración
;   (por defecto, no se borran).

; Desde Inno 6.3 el .iss se lee como UTF-8 sin BOM (lleva acentos y «») y existe
; «x64compatible». Con una versión anterior los textos saldrían mal sin avisar.
#if Ver < EncodeVer(6, 3, 0)
  #error Hace falta Inno Setup 6.3 o posterior
#endif

#ifndef AppVersion
  #error Falta /DAppVersion=X.Y.Z (lo pasa scripts/empaquetar.py)
#endif
#ifndef SourceDir
  #define SourceDir AddBackslash(SourcePath) + "..\build\Luzeira"
#endif
#ifndef OutputDir
  #define OutputDir AddBackslash(SourcePath) + "..\dist"
#endif
#ifndef IconFile
  #define IconFile AddBackslash(SourcePath) + "..\src\luzeira\recursos\luzeira.ico"
#endif

; Estos solo se cambian en las pruebas automáticas del instalador, para recorrer el
; ciclo instalar/desinstalar sin tocar el autoarranque, el menú Inicio ni los datos reales
; del usuario que prueba. En una build normal valen lo que se ve aquí.
; AppGuid: las pruebas usan otro para no mezclarse con una Luzeira instalada de verdad
; (mismo AppId = Inno las trataría como la misma app y la prueba borraría su desinstalador).
#ifndef AppGuid
  #define AppGuid "5E01DD71-2E73-46B3-9498-C53B2D1E1AB7"
#endif
#ifndef MutexName
  #define MutexName "Luzeira"
#endif
#ifndef RunKey
  #define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"
#endif
#ifndef RunValue
  #define RunValue "Luzeira"
#endif
#ifndef ProgramsDir
  #define ProgramsDir "{userprograms}"
#endif
#ifndef DesktopDir
  #define DesktopDir "{userdesktop}"
#endif
#ifndef UserDataDir
  #define UserDataDir "{userappdata}\Luzeira"
#endif
#ifndef ReopenParams
  #define ReopenParams "-m luzeira"
#endif
; Medios segundos que una actualización espera a que la app suelte su mutex (60 s).
#ifndef UpdateWaitSteps
  #define UpdateWaitSteps "120"
#endif
#ifndef UserLogsDir
  #define UserLogsDir "{localappdata}\Luzeira"
#endif

#define PythonW "{app}\python\pythonw.exe"
#define AppIcon "{app}\python\Lib\site-packages\luzeira\recursos\luzeira.ico"

[Setup]
; AppGuid nuevo el 2026-11-18 al pasar de «Visoria» a «Luzeira» (D-14): es otra app.
; AppId fijo: identifica a Luzeira entre versiones (actualizar = instalar encima). NO cambiar
; AppGuid (solo en pruebas). «{{» es una llave literal en Inno.
AppId={{{#AppGuid}}
AppName=Luzeira
AppVersion={#AppVersion}
AppVerName=Luzeira {#AppVersion}
AppPublisher=Luzeira contributors
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Instalador de Luzeira
; Por usuario y sin elevación, aunque lo abra un administrador. Sin opción de elevar.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Luzeira
; Sin página de carpeta ni de grupo del menú Inicio: para un usuario no técnico no aportan
; nada (no hay que elegir disco: es por usuario). Quien quiera otra ruta tiene /DIR=.
DisableDirPage=yes
DisableProgramGroupPage=yes
AppMutex={#MutexName}
SetupMutex=LuzeiraInstalador
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Luzeira-{#AppVersion}-instalador
SetupIconFile={#IconFile}
UninstallDisplayIcon={#AppIcon}
UninstallDisplayName=Luzeira
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
TimeStampsInUTC=yes
; La licencia es GPL: no hay que «aceptarla» para usar el programa, así que no hay página
; de licencia. Va como LICENSE.txt en la carpeta del programa.

[Languages]
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"

[CustomMessages]
es.GrupoAccesos=Accesos directos:
es.TareaEscritorio=Crear un acceso directo en el &escritorio
es.GrupoArranque=Arranque:
es.TareaAutoarranque=Iniciar Luzeira con &Windows (la pantalla se enciende sola)
es.AbrirAhora=Abrir Luzeira ahora
es.Descripcion=Luzeira: tu pantalla USB del PC
es.FaltaWebView2=Para el editor de temas hace falta «Microsoft Edge WebView2 Runtime» (gratuito, de Microsoft) y en este equipo no está.%n%nLa pantalla y el icono junto al reloj funcionan sin él.%n%n¿Abrir ahora la página oficial de descarga de Microsoft?
es.BorrarDatos=¿Borrar también tus temas, tu configuración y los registros de Luzeira?%n%nSi vas a volver a instalar Luzeira, pulsa «No» para conservarlos.

[Tasks]
; Escritorio desmarcado: Luzeira vive junto al reloj y arranca con Windows; el acceso del
; menú Inicio siempre se crea y la propia app puede crear el del escritorio desde su menú.
Name: "escritorio"; Description: "{cm:TareaEscritorio}"; GroupDescription: "{cm:GrupoAccesos}"; Flags: unchecked
; Autoarranque marcado: una pantalla de monitorización que no arranca sola se queda en
; negro tras cada reinicio. Se ve y se desmarca en el asistente, y luego desde la bandeja.
Name: "autoarranque"; Description: "{cm:TareaAutoarranque}"; GroupDescription: "{cm:GrupoArranque}"

[InstallDelete]
; Al actualizar, fuera el Python anterior entero: no deben quedar módulos de otra versión.
Type: filesandordirs; Name: "{app}\python"

[Files]
; Luzeira.cmd es el lanzador del zip portable: en la instalada están los accesos directos.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "Luzeira.cmd"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; El del escritorio abre también el editor (si Luzeira ya está abierta, se lo pide: bandeja.main).
; AppUserModelID = luzeira.sistema.identidad.APP_ID: al anclar la ventana sale «Luzeira», no «pythonw».
Name: "{#ProgramsDir}\Luzeira"; Filename: "{#PythonW}"; Parameters: "-m luzeira"; WorkingDir: "{%USERPROFILE}"; IconFilename: "{#AppIcon}"; Comment: "{cm:Descripcion}"; AppUserModelID: "Luzeira.Luzeira"
Name: "{#DesktopDir}\Luzeira"; Filename: "{#PythonW}"; Parameters: "-m luzeira --abrir-editor"; WorkingDir: "{%USERPROFILE}"; IconFilename: "{#AppIcon}"; Comment: "{cm:Descripcion}"; AppUserModelID: "Luzeira.Luzeira"; Tasks: escritorio

[Registry]
; La misma línea que escribe la app (luzeira.sistema.autoarranque.default_command):
; "<carpeta>\python\pythonw.exe" -m luzeira
; Sin «uninsdeletevalue»: Inno borraría el valor sin mirarlo, aunque ya apuntase a otra
; Luzeira (portable, de desarrollo). Lo borra [Code] solo si apunta aquí (PointsHere).
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "{#RunValue}"; ValueData: """{#PythonW}"" -m luzeira"; Tasks: autoarranque

[Run]
; skipifsilent: en una instalación silenciosa no se abre nada. --abrir-editor: la primera
; vez se abre también el editor, para que se vea qué se puede hacer.
Filename: "{#PythonW}"; Parameters: "-m luzeira --abrir-editor"; WorkingDir: "{%USERPROFILE}"; Description: "{cm:AbrirAhora}"; Flags: postinstall nowait skipifsilent
; Actualización pedida desde la app (/ACTUALIZAR, ADR-006): silenciosa, y al terminar se
; vuelve a abrir Luzeira (sin el editor), como estaba antes de actualizar.
Filename: "{#PythonW}"; Parameters: "{#ReopenParams}"; WorkingDir: "{%USERPROFILE}"; Flags: nowait; Check: IsUpdate

[UninstallDelete]
; Python escribe __pycache__ si le falta algún .pyc: que no quede nada en la carpeta.
Type: filesandordirs; Name: "{app}\python"
Type: dirifempty; Name: "{app}"

[Code]
const
  WebView2Client = 'Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}';
  WebView2Url = 'https://developer.microsoft.com/microsoft-edge/webview2/';

function HasVersion(RootKey: Integer; SubKey: String): Boolean;
var
  Version: String;
begin
  Result := RegQueryStringValue(RootKey, SubKey, 'pv', Version) and (Version <> '') and (Version <> '0.0.0.0');
end;

{ Cómo detectarlo, según Microsoft («Distribute your app and the WebView2 Runtime»):
  valor «pv» del cliente de EdgeUpdate, para toda la máquina (vista de 32 bits) o por usuario. }
function WebView2Installed: Boolean;
begin
  Result := HasVersion(HKLM32, 'SOFTWARE\' + WebView2Client) or HasVersion(HKCU, 'Software\' + WebView2Client);
end;

{ ¿Apunta esta línea de Run (o este destino) a ESTA instalación? Solo entonces se borra:
  si el usuario tiene otra Luzeira (portable, de desarrollo) con autoarranque, no se toca. }
function PointsHere(Command: String): Boolean;
begin
  Result := Pos(Lowercase(ExpandConstant('{app}\python\')), Lowercase(Command)) > 0;
end;

{ Borra el autoarranque solo si es de esta instalación. }
procedure RemoveRunValueIfHere;
var
  Command: String;
begin
  if RegQueryStringValue(HKCU, '{#RunKey}', '{#RunValue}', Command) and PointsHere(Command) then
    RegDeleteValue(HKCU, '{#RunKey}', '{#RunValue}');
end;

{ ¿Este acceso directo abre ESTA instalación? (lo pudo crear la app desde la bandeja) }
function ShortcutPointsHere(Path: String): Boolean;
var
  Shell, Link: Variant;
begin
  Result := False;
  if not FileExists(Path) then
    exit;
  try
    Shell := CreateOleObject('WScript.Shell');
    Link := Shell.CreateShortcut(Path);
    Result := PointsHere(Link.TargetPath);
  except
    Result := False;
  end;
end;

var
  { «Iniciar con Windows» tal como estaba antes de una actualización desde la app. }
  HadRunValue: Boolean;
  PreviousRunCommand: String;
  { Se llegó a instalar (ssPostInstall). Si una actualización aborta antes, se reabre la
    Luzeira que ya había: la app se cerró para dejar paso a este instalador (C-58). }
  Installed: Boolean;

{ ¿Lo ha lanzado la propia app para actualizarse? (luzeira.actualizar.INSTALLER_ARGS) }
function IsUpdate: Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/ACTUALIZAR') = 0 then
      Result := True;
end;

{ Al actualizar, la app lanza este instalador y sale enseguida: hay que esperar a que
  suelte su mutex. Sin esperar, AppMutex aborta en silencio (medido 2026-11-18: rc=1 en
  0,2 s; esperando aquí, que va antes de la comprobación de AppMutex, instala bien). }
function InitializeSetup: Boolean;
var
  I: Integer;
begin
  Result := True;
  if not IsUpdate then
    exit;
  I := 0;
  while CheckForMutexes('{#MutexName}') and (I < {#UpdateWaitSteps}) do
  begin
    Sleep(500);
    I := I + 1;
  end;
  HadRunValue := RegQueryStringValue(HKCU, '{#RunKey}', '{#RunValue}', PreviousRunCommand);
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if CurStep = ssPostInstall then
    Installed := True;
  { Una actualización desde la app no cambia «Iniciar con Windows»: el usuario pudo
    cambiarlo en la app después de instalar, y la tarea recordada ya no dice la verdad. }
  if (CurStep = ssPostInstall) and IsUpdate then
  begin
    if HadRunValue then
      RegWriteStringValue(HKCU, '{#RunKey}', '{#RunValue}', PreviousRunCommand)
    else
      RemoveRunValueIfHere;
  end
  { Al actualizar con «Iniciar con Windows» desmarcado, Inno no toca [Registry] y el valor
    anterior seguiría ahí: se quita aquí (solo si apunta a esta instalación). }
  else if (CurStep = ssPostInstall) and not WizardIsTaskSelected('autoarranque') then
    RemoveRunValueIfHere;
  if (CurStep = ssPostInstall) and not WizardSilent and not WebView2Installed then
    if MsgBox(CustomMessage('FaltaWebView2'), mbInformation, MB_YESNO) = IDYES then
      ShellExec('open', WebView2Url, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

{ Una actualización que aborta (otro instalador abierto, Luzeira sin cerrar a tiempo,
  disco lleno…) con /SUPPRESSMSGBOXES no avisa a nadie, y la app ya se había cerrado:
  sin esto, el usuario se quedaría sin Luzeira ni pantalla (revisor de ADR-006, C-58).
  Inno llama a DeinitializeSetup también al abortar (medido 2026-11-18). La carpeta sale
  del registro de desinstalación: la constante app puede no estar lista todavía. Si sigue
  abierta otra Luzeira (su mutex), no se abre otra. }
procedure DeinitializeSetup;
var
  Dir: String;
  Code: Integer;
begin
  if not IsUpdate or Installed or CheckForMutexes('{#MutexName}') then
    exit;
  if not RegQueryStringValue(HKCU, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{' + '{#AppGuid}' + '}_is1', 'InstallLocation', Dir) then
    exit;
  Dir := AddBackslash(Dir);
  if FileExists(Dir + 'python\pythonw.exe') then
  begin
    Log('La actualización no se ha instalado: se vuelve a abrir la Luzeira que había.');
    Exec(Dir + 'python\pythonw.exe', '{#ReopenParams}', ExpandConstant('{%USERPROFILE}'), SW_SHOWNORMAL, ewNoWait, Code);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir, LogsDir, Desktop: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { El valor puede haberlo creado la app (opción de la bandeja) y no la tarea del
      instalador: entonces Inno no lo tiene apuntado y hay que borrarlo aquí. }
    RemoveRunValueIfHere;
    { Igual con el acceso del escritorio que crea la app: sin esto quedaría roto. }
    Desktop := ExpandConstant('{#DesktopDir}\Luzeira.lnk');
    if ShortcutPointsHere(Desktop) then
      DeleteFile(Desktop);
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{#UserDataDir}');
    LogsDir := ExpandConstant('{#UserLogsDir}');
    { Pregunta con «No» por defecto; en una desinstalación silenciosa, «No». }
    if DirExists(DataDir) or DirExists(LogsDir) then
      if SuppressibleMsgBox(CustomMessage('BorrarDatos'), mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
      begin
        DelTree(DataDir, True, True, True);
        DelTree(LogsDir, True, True, True);
      end;
  end;
end;
