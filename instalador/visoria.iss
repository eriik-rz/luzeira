; Instalador de Visoria (Inno Setup 6). Decisión D-12, fase 6.
;
; No se compila a mano: lo hace scripts/empaquetar.py, que monta antes build\Visoria y pasa
;   ISCC /DAppVersion=<versión de pyproject.toml> /DSourceDir=... /DOutputDir=... /DIconFile=...
;
; Qué hace:
; - Instala solo para el usuario actual, SIN admin ni UAC (PrivilegesRequired=lowest), en
;   %LOCALAPPDATA%\Programs\Visoria. La desinstalación queda registrada en HKCU.
; - La app es el Python embebible oficial: los accesos directos lanzan
;   python\pythonw.exe -m visoria (pythonw.exe va firmado por la Python Software Foundation).
; - Si Visoria está abierta, el instalador y el desinstalador lo detectan por su mutex
;   (la app crea «Local\Visoria»; aquí basta «Visoria»: sin prefijo, Windows lo busca en el
;   espacio de nombres de la sesión, que es «Local\»).
; - Al desinstalar borra el autoarranque y PREGUNTA antes de borrar temas y configuración
;   (por defecto, no se borran).

#ifndef AppVersion
  #error Falta /DAppVersion=X.Y.Z (lo pasa scripts/empaquetar.py)
#endif
#ifndef SourceDir
  #define SourceDir AddBackslash(SourcePath) + "..\build\Visoria"
#endif
#ifndef OutputDir
  #define OutputDir AddBackslash(SourcePath) + "..\dist"
#endif
#ifndef IconFile
  #define IconFile AddBackslash(SourcePath) + "..\src\visoria\recursos\visoria.ico"
#endif

; Estos cinco solo se cambian en las pruebas automáticas del instalador, para recorrer el
; ciclo instalar/desinstalar sin tocar el autoarranque, el menú Inicio ni los datos reales
; del usuario que prueba. En una build normal valen lo que se ve aquí.
#ifndef MutexName
  #define MutexName "Visoria"
#endif
#ifndef RunKey
  #define RunKey "Software\Microsoft\Windows\CurrentVersion\Run"
#endif
#ifndef RunValue
  #define RunValue "Visoria"
#endif
#ifndef ProgramsDir
  #define ProgramsDir "{userprograms}"
#endif
#ifndef UserDataDir
  #define UserDataDir "{userappdata}\Visoria"
#endif
#ifndef UserLogsDir
  #define UserLogsDir "{localappdata}\Visoria"
#endif

#define PythonW "{app}\python\pythonw.exe"
#define AppIcon "{app}\python\Lib\site-packages\visoria\recursos\visoria.ico"

[Setup]
; AppId fijo: identifica a Visoria entre versiones (actualizar = instalar encima). NO cambiarlo.
AppId={{75B00440-8495-4923-A848-A5B3982D17C3}
AppName=Visoria
AppVersion={#AppVersion}
AppVerName=Visoria {#AppVersion}
AppPublisher=Visoria contributors
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Instalador de Visoria
; Por usuario y sin elevación, aunque lo abra un administrador. Sin opción de elevar.
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\Visoria
; Sin página de carpeta ni de grupo del menú Inicio: para un usuario no técnico no aportan
; nada (no hay que elegir disco: es por usuario). Quien quiera otra ruta tiene /DIR=.
DisableDirPage=yes
DisableProgramGroupPage=yes
AppMutex={#MutexName}
SetupMutex=VisoriaInstalador
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#OutputDir}
OutputBaseFilename=Visoria-{#AppVersion}-instalador
SetupIconFile={#IconFile}
UninstallDisplayIcon={#AppIcon}
UninstallDisplayName=Visoria
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
es.TareaAutoarranque=Iniciar Visoria con &Windows (la pantalla se enciende sola)
es.AbrirAhora=Abrir Visoria ahora
es.Descripcion=Visoria: tu pantalla USB del PC
es.FaltaWebView2=Para el editor de temas hace falta «Microsoft Edge WebView2 Runtime» (gratuito, de Microsoft) y en este equipo no está.%n%nLa pantalla y el icono junto al reloj funcionan sin él.%n%n¿Abrir ahora la página oficial de descarga de Microsoft?
es.BorrarDatos=¿Borrar también tus temas, tu configuración y los registros de Visoria?%n%nSi vas a volver a instalar Visoria, pulsa «No» para conservarlos.

[Tasks]
; Escritorio desmarcado: Visoria vive junto al reloj y arranca con Windows; el acceso del
; menú Inicio siempre se crea y la propia app puede crear el del escritorio desde su menú.
Name: "escritorio"; Description: "{cm:TareaEscritorio}"; GroupDescription: "{cm:GrupoAccesos}"; Flags: unchecked
; Autoarranque marcado: una pantalla de monitorización que no arranca sola se queda en
; negro tras cada reinicio. Se ve y se desmarca en el asistente, y luego desde la bandeja.
Name: "autoarranque"; Description: "{cm:TareaAutoarranque}"; GroupDescription: "{cm:GrupoArranque}"

[InstallDelete]
; Al actualizar, fuera el Python anterior entero: no deben quedar módulos de otra versión.
Type: filesandordirs; Name: "{app}\python"

[Files]
; Visoria.cmd es el lanzador del zip portable: en la instalada están los accesos directos.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "Visoria.cmd"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; AppUserModelID = visoria.sistema.identidad.APP_ID: al anclar la ventana sale «Visoria», no «pythonw».
Name: "{#ProgramsDir}\Visoria"; Filename: "{#PythonW}"; Parameters: "-m visoria"; WorkingDir: "{%USERPROFILE}"; IconFilename: "{#AppIcon}"; Comment: "{cm:Descripcion}"; AppUserModelID: "Visoria.Visoria"
Name: "{userdesktop}\Visoria"; Filename: "{#PythonW}"; Parameters: "-m visoria"; WorkingDir: "{%USERPROFILE}"; IconFilename: "{#AppIcon}"; Comment: "{cm:Descripcion}"; AppUserModelID: "Visoria.Visoria"; Tasks: escritorio

[Registry]
; La misma línea que escribe la app (visoria.sistema.autoarranque.default_command):
; "<carpeta>\python\pythonw.exe" -m visoria
; Sin «uninsdeletevalue»: Inno borraría el valor sin mirarlo, aunque ya apuntase a otra
; Visoria (portable, de desarrollo). Lo borra [Code] solo si apunta aquí (PointsHere).
Root: HKCU; Subkey: "{#RunKey}"; ValueType: string; ValueName: "{#RunValue}"; ValueData: """{#PythonW}"" -m visoria"; Tasks: autoarranque

[Run]
; skipifsilent: en una instalación silenciosa no se abre nada. --abrir-editor: la primera
; vez se abre también el editor, para que se vea qué se puede hacer.
Filename: "{#PythonW}"; Parameters: "-m visoria --abrir-editor"; WorkingDir: "{%USERPROFILE}"; Description: "{cm:AbrirAhora}"; Flags: postinstall nowait skipifsilent

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

procedure CurStepChanged(CurStep: TSetupStep);
var
  ErrorCode: Integer;
begin
  if (CurStep = ssPostInstall) and not WizardSilent and not WebView2Installed then
    if MsgBox(CustomMessage('FaltaWebView2'), mbInformation, MB_YESNO) = IDYES then
      ShellExec('open', WebView2Url, '', '', SW_SHOWNORMAL, ewNoWait, ErrorCode);
end;

{ ¿Apunta esta línea de Run a ESTA instalación? Solo entonces se borra: si el usuario
  tiene otra Visoria (portable, de desarrollo) con autoarranque, no se le toca. }
function PointsHere(Command: String): Boolean;
begin
  Result := Pos(Lowercase(ExpandConstant('{app}\python\')), Lowercase(Command)) > 0;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Command, DataDir, LogsDir: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    { El valor puede haberlo creado la app (opción de la bandeja) y no la tarea del
      instalador: entonces Inno no lo tiene apuntado y hay que borrarlo aquí. }
    if RegQueryStringValue(HKCU, '{#RunKey}', '{#RunValue}', Command) and PointsHere(Command) then
      RegDeleteValue(HKCU, '{#RunKey}', '{#RunValue}');
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
