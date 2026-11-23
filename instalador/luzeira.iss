; Instalador de Luzeira (Inno Setup 7). Decisión D-12, fase 6; Inno 7 desde la 0.1.7 (C-64).
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

; Inno 7: el .iss se lee como UTF-8 sin BOM (lleva acentos y «»), existe «x64compatible»
; y trae las traducciones oficiales de todos nuestros idiomas. empaquetar.py exige además
; la 7.1.1 exacta (INNO_SHA256).
#if Ver < EncodeVer(7, 0, 0)
  #error Hace falta Inno Setup 7
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
; Idioma (C-62): el de la interfaz de Windows si es uno de los nueve, sin preguntar. Solo si
; no lo es (p. ej. japonés) sale la lista, con el inglés marcado (el primero de [Languages]),
; igual que la app, que cae al inglés. Instalando en silencio (/VERYSILENT, actualizaciones)
; nunca pregunta: usa el de Windows o el inglés.
ShowLanguageDialog=auto
LanguageDetectionMethod=uilanguage

[Languages]
; Los mismos idiomas que la app (luzeira.idioma.NATIVE_NAMES), con las traducciones
; oficiales que trae Inno Setup 7.1.1. Los nombres cortos son los códigos de la app.
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "pl"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"

[CustomMessages]
es.GrupoAccesos=Accesos directos:
es.TareaEscritorio=Crear un acceso directo en el &escritorio
es.GrupoArranque=Arranque:
es.TareaAutoarranque=Iniciar Luzeira con &Windows (la pantalla se enciende sola)
es.AbrirAhora=Abrir Luzeira ahora
es.Descripcion=Luzeira: tu pantalla USB del PC
es.FaltaWebView2=Para el editor de temas hace falta «Microsoft Edge WebView2 Runtime» (gratuito, de Microsoft) y en este equipo no está.%n%nLa pantalla y el icono junto al reloj funcionan sin él.%n%n¿Abrir ahora la página oficial de descarga de Microsoft?
es.BorrarDatos=¿Borrar también tus temas, tu configuración y los registros de Luzeira?%n%nSi vas a volver a instalar Luzeira, pulsa «No» para conservarlos.

en.GrupoAccesos=Shortcuts:
en.TareaEscritorio=Create a &desktop shortcut
en.GrupoArranque=Startup:
en.TareaAutoarranque=Start Luzeira with &Windows (the screen turns on by itself)
en.AbrirAhora=Open Luzeira now
en.Descripcion=Luzeira: your PC's USB screen
en.FaltaWebView2=The theme editor needs "Microsoft Edge WebView2 Runtime" (free, from Microsoft), and it is not installed on this computer.%n%nThe screen and the icon next to the clock work without it.%n%nOpen Microsoft's official download page now?
en.BorrarDatos=Also delete your Luzeira themes, settings and logs?%n%nIf you are going to install Luzeira again, click "No" to keep them.

de.GrupoAccesos=Verknüpfungen:
de.TareaEscritorio=Verknüpfung auf dem &Desktop erstellen
de.GrupoArranque=Autostart:
de.TareaAutoarranque=Luzeira mit &Windows starten (das Display schaltet sich von selbst ein)
de.AbrirAhora=Luzeira jetzt öffnen
de.Descripcion=Luzeira: das USB-Display für deinen PC
de.FaltaWebView2=Für den Theme-Editor brauchst du „Microsoft Edge WebView2 Runtime“ (kostenlos, von Microsoft), und auf diesem Computer ist es nicht installiert.%n%nDas Display und das Symbol neben der Uhr funktionieren auch ohne.%n%nJetzt die offizielle Download-Seite von Microsoft öffnen?
de.BorrarDatos=Auch deine Themes, Einstellungen und Protokolle von Luzeira löschen?%n%nWenn du Luzeira wieder installieren willst, klicke auf „Nein“, um sie zu behalten.

fr.GrupoAccesos=Raccourcis :
fr.TareaEscritorio=Créer un raccourci sur le &Bureau
fr.GrupoArranque=Démarrage :
fr.TareaAutoarranque=Lancer Luzeira au démarrage de &Windows (l’écran s’allume tout seul)
fr.AbrirAhora=Ouvrir Luzeira maintenant
fr.Descripcion=Luzeira : l’écran USB de votre PC
fr.FaltaWebView2=L’éditeur de thèmes a besoin de « Microsoft Edge WebView2 Runtime » (gratuit, de Microsoft), qui n’est pas installé sur cet ordinateur.%n%nL’écran et l’icône près de l’horloge fonctionnent sans lui.%n%nOuvrir maintenant la page de téléchargement officielle de Microsoft ?
fr.BorrarDatos=Supprimer aussi vos thèmes, vos paramètres et les journaux de Luzeira ?%n%nSi vous comptez réinstaller Luzeira, cliquez sur « Non » pour les conserver.

it.GrupoAccesos=Collegamenti:
it.TareaEscritorio=Crea un collegamento sul &desktop
it.GrupoArranque=Avvio:
it.TareaAutoarranque=Avvia Luzeira con &Windows (lo schermo si accende da solo)
it.AbrirAhora=Apri Luzeira ora
it.Descripcion=Luzeira: lo schermo USB del tuo PC
it.FaltaWebView2=Per l'editor dei temi serve «Microsoft Edge WebView2 Runtime» (gratuito, di Microsoft), che non è installato su questo computer.%n%nLo schermo e l'icona accanto all'orologio funzionano anche senza.%n%nAprire ora la pagina ufficiale di download di Microsoft?
it.BorrarDatos=Eliminare anche i tuoi temi, le impostazioni e i registri di Luzeira?%n%nSe pensi di reinstallare Luzeira, premi «No» per conservarli.

pt.GrupoAccesos=Atalhos:
pt.TareaEscritorio=Criar um atalho na área de &trabalho
pt.GrupoArranque=Inicialização:
pt.TareaAutoarranque=Iniciar a Luzeira com o &Windows (a tela liga sozinha)
pt.AbrirAhora=Abrir a Luzeira agora
pt.Descripcion=Luzeira: a tela USB do seu PC
pt.FaltaWebView2=O editor de temas precisa do “Microsoft Edge WebView2 Runtime” (gratuito, da Microsoft), que não está instalado neste computador.%n%nA tela e o ícone ao lado do relógio funcionam sem ele.%n%nAbrir agora a página oficial de download da Microsoft?
pt.BorrarDatos=Apagar também seus temas, suas configurações e os registros da Luzeira?%n%nSe você for instalar a Luzeira de novo, clique em “Não” para mantê-los.

pl.GrupoAccesos=Skróty:
pl.TareaEscritorio=Utwórz skrót na &pulpicie
pl.GrupoArranque=Uruchamianie:
pl.TareaAutoarranque=Uruchamiaj Luzeirę razem z systemem &Windows (ekran włączy się sam)
pl.AbrirAhora=Otwórz Luzeirę teraz
pl.Descripcion=Luzeira: ekran USB do Twojego komputera
pl.FaltaWebView2=Edytor motywów potrzebuje środowiska „Microsoft Edge WebView2 Runtime” (bezpłatnego, od Microsoftu), którego nie ma na tym komputerze.%n%nEkran i ikona obok zegara działają bez niego.%n%nCzy otworzyć teraz oficjalną stronę pobierania Microsoftu?
pl.BorrarDatos=Czy usunąć również Twoje motywy, ustawienia i dzienniki Luzeiry?%n%nJeśli zamierzasz ponownie zainstalować Luzeirę, kliknij „Nie”, aby je zachować.

tr.GrupoAccesos=Kısayollar:
tr.TareaEscritorio=&Masaüstünde kısayol oluştur
tr.GrupoArranque=Başlangıç:
tr.TareaAutoarranque=Luzeira'yı &Windows ile başlat (ekran kendiliğinden açılır)
tr.AbrirAhora=Luzeira'yı şimdi aç
tr.Descripcion=Luzeira: bilgisayarının USB ekranı
tr.FaltaWebView2=Tema düzenleyicisi için “Microsoft Edge WebView2 Runtime” (Microsoft'tan, ücretsiz) gerekiyor ve bu bilgisayarda yüklü değil.%n%nEkran ve saatin yanındaki simge onsuz da çalışır.%n%nMicrosoft'un resmî indirme sayfası şimdi açılsın mı?
tr.BorrarDatos=Temaların, ayarların ve Luzeira kayıtları da silinsin mi?%n%nLuzeira'yı yeniden kuracaksan, saklamak için “Hayır”a tıkla.

ru.GrupoAccesos=Ярлыки:
ru.TareaEscritorio=Создать ярлык на &рабочем столе
ru.GrupoArranque=Автозапуск:
ru.TareaAutoarranque=Запускать Luzeira вместе с &Windows (экран включается сам)
ru.AbrirAhora=Открыть Luzeira сейчас
ru.Descripcion=Luzeira: USB-экран для вашего ПК
ru.FaltaWebView2=Для редактора тем нужен «Microsoft Edge WebView2 Runtime» (бесплатно, от Microsoft), а на этом компьютере его нет.%n%nЭкран и значок рядом с часами работают и без него.%n%nОткрыть официальную страницу загрузки Microsoft?
ru.BorrarDatos=Удалить также ваши темы, настройки и журналы Luzeira?%n%nЕсли вы собираетесь снова установить Luzeira, нажмите «Нет», чтобы сохранить их.

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
; Los dos abren también el editor (si Luzeira ya está abierta, se lo piden: bandeja.main). Quien
; pulsa el icono quiere ver algo; lo que se ancla a Inicio o a la barra copia estos argumentos.
; AppUserModelID = luzeira.sistema.identidad.APP_ID: al anclar la ventana sale «Luzeira», no «pythonw».
Name: "{#ProgramsDir}\Luzeira"; Filename: "{#PythonW}"; Parameters: "-m luzeira --abrir-editor"; WorkingDir: "{%USERPROFILE}"; IconFilename: "{#AppIcon}"; Comment: "{cm:Descripcion}"; AppUserModelID: "Luzeira.Luzeira"
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
