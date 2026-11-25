; Instalador del módulo opcional «Luzeira Sensores» (Inno Setup 7). ADR-007 y ADR-008.
;
; Se compila después de publicar el servicio:
;   dotnet publish modulo-sensores/src/LuzeiraSensores -c Release -o modulo-sensores/publish
;   ISCC /DAppVersion=<versión de modulo-sensores/Directory.Build.props> instalador\luzeira-sensores.iss
;
; Qué hace (y por qué, en el ADR-008):
; - Pide administrador y no deja elegir otra cosa (PrivilegesRequired=admin y
;   PrivilegesRequiredOverridesAllowed vacío). Es un instalador de 64 bits.
; - Instala siempre en «Archivos de programa\Luzeira Sensores» (sin página de carpeta),
;   que crea con una DACL propia y protegida: SYSTEM y Administradores, control total;
;   Usuarios, leer y ejecutar. Si ya existía con otros permisos, se niega.
; - Crea «%ProgramData%\Luzeira Sensores», donde el servicio publica sensores.bin, con
;   la DACL D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FR;;;BU) y propietario
;   Administradores. Si ya existía con otro propietario o permisos (alguien se adelantó),
;   se niega. El servicio nunca crea esa carpeta.
; - Registra el servicio LuzeiraSensores: ruta entre comillas, inicio automático (no
;   retrasado), LocalSystem (PawnIO solo deja abrirlo a SYSTEM y Administradores), SID
;   propio, privilegios mínimos, recuperación «solo reiniciar», sin el permiso de
;   controles propios (CR) para los usuarios, y DOTNET_EnableDiagnostics=0.
; - NO incluye PawnIO (no hay permiso de redistribución): mira si está y, si falta,
;   ofrece abrir su página oficial en el navegador del usuario (no del administrador).
; - Sin casilla de la placa (ADR-008, punto 5) ni clave en HKLM\SOFTWARE: el servicio solo
;   lee la CPU hasta que modulo.py tenga identificadores de placa (coordinador, 2026-11-25).
; - Al desinstalar: para y borra el servicio, la carpeta del programa y la de ProgramData
;   (reintentando: Luzeira suelta el fichero unos 5 s después de que deja de cambiar), y
;   avisa de que PawnIO se queda.

#if Ver < EncodeVer(7, 0, 0)
  #error Hace falta Inno Setup 7
#endif

#ifndef AppVersion
  #error Falta /DAppVersion=X.Y.Z (la de modulo-sensores/Directory.Build.props)
#endif
#ifndef SourceDir
  #define SourceDir AddBackslash(SourcePath) + "..\modulo-sensores\publish"
#endif
#ifndef OutputDir
  #define OutputDir AddBackslash(SourcePath) + "..\dist"
#endif
#ifndef IconFile
  #define IconFile AddBackslash(SourcePath) + "..\src\luzeira\recursos\luzeira.ico"
#endif

#define ServiceName "LuzeiraSensores"
#define ServiceExe "LuzeiraSensores.exe"
#define DataFolderName "Luzeira Sensores"
#define PawnIoUrl "https://pawnio.eu/"
#define PawnIoUninstallKey "SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO"

; DACL de la carpeta de datos (ADR-008, punto 6) y de la del programa. «O:BA»: propietario
; Administradores, aunque Windows esté configurado para dar la propiedad al usuario.
#define DataFolderSddl "O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FR;;;BU)"
#define AppFolderSddl "O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)"

; Permisos del servicio: los de Windows por defecto para un servicio nuevo, quitando CR
; (SERVICE_USER_DEFINED_CONTROL) a los usuarios interactivos (IU) y de servicio (SU).
#define ServiceSddl "D:(A;;CCLCSWRPWPDTLOCRRC;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWLORC;;;IU)(A;;CCLCSWLORC;;;SU)"

[Setup]
; AppId fijo del módulo (otro que el de Luzeira: son dos programas). NO cambiar.
AppId={{EB4BADF4-8B20-4265-96F8-990C0B350325}
AppName=Luzeira Sensores
AppVersion={#AppVersion}
AppVerName=Luzeira Sensores {#AppVersion}
AppPublisher=Luzeira contributors
VersionInfoVersion={#AppVersion}
VersionInfoDescription=Instalador de Luzeira Sensores
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=
SetupArchitecture=x64
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
DefaultDirName={autopf}\Luzeira Sensores
DisableDirPage=yes
UsePreviousAppDir=no
DisableProgramGroupPage=yes
SetupMutex=LuzeiraSensoresInstalador
; El servicio lo para [Code] antes de copiar; no hace falta el gestor de reinicio.
CloseApplications=no
OutputDir={#OutputDir}
OutputBaseFilename=LuzeiraSensores-{#AppVersion}-setup
SetupIconFile={#IconFile}
UninstallDisplayName=Luzeira Sensores
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
TimeStampsInUTC=yes
ShowLanguageDialog=auto
LanguageDetectionMethod=uilanguage

[Languages]
; Los mismos que Luzeira (instalador/luzeira.iss).
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "es"; MessagesFile: "compiler:Languages\Spanish.isl"
Name: "de"; MessagesFile: "compiler:Languages\German.isl"
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"
Name: "it"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "pt"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "pl"; MessagesFile: "compiler:Languages\Polish.isl"
Name: "tr"; MessagesFile: "compiler:Languages\Turkish.isl"
Name: "ru"; MessagesFile: "compiler:Languages\Russian.isl"
Name: "zh"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "ja"; MessagesFile: "compiler:Languages\Japanese.isl"

[CustomMessages]
es.AvisoBeta=Luzeira Sensores es un módulo opcional, en beta. Instala un servicio de Windows que lee la temperatura de la CPU con el driver PawnIO, que se instala aparte. Algún antitrampas de juegos (FACEIT) ha bloqueado PawnIO alguna vez.
es.EstadoPawnIO=PawnIO instalado: versión %1
es.PawnIONoEsta=PawnIO: no está instalado (hace falta para leer la temperatura)
es.FaltaPawnIO=Para leer la temperatura hace falta PawnIO, un driver gratuito de otro autor que no se puede incluir aquí. En este equipo no está.%n%nEl servicio ya está instalado: empezará a leer en cuanto instales PawnIO.%n%n¿Abrir ahora su página oficial (pawnio.eu)?
es.PawnIOViejo=PawnIO %1 es anterior a la 2.2.0, la que pide Luzeira Sensores.%n%n¿Abrir su página oficial para actualizarlo?
es.CarpetaAjena=La carpeta %1 ya existe y no tiene el propietario o los permisos que pone este instalador. Por seguridad, no se instala.%n%nSi no sabes de dónde sale, bórrala como administrador y vuelve a intentarlo.
es.ErrorCarpeta=No se pudo crear la carpeta %1 (error %2).
es.ErrorServicio=No se pudo configurar el servicio (paso «%1», código %2). Luzeira Sensores no funcionará hasta que lo reinstales.
es.DescripcionServicio=Lee la temperatura de la CPU para Luzeira (módulo opcional). Sin red y sin entradas.
es.PawnIOSeQueda=PawnIO no se desinstala: lo pueden usar otros programas. Si no lo necesitas, quítalo desde Configuración > Aplicaciones.
es.NoSeBorro=No se pudo borrar la carpeta %1. Bórrala a mano con Luzeira cerrada.

en.AvisoBeta=Luzeira Sensores is an optional module, in beta. It installs a Windows service that reads the CPU temperature through the PawnIO driver, which is installed separately. Some game anti-cheats (FACEIT) have blocked PawnIO in the past.
en.EstadoPawnIO=PawnIO installed: version %1
en.PawnIONoEsta=PawnIO: not installed (needed to read the temperature)
en.FaltaPawnIO=Reading the temperature needs PawnIO, a free driver by another author that cannot be bundled here. It is not installed on this computer.%n%nThe service is already installed: it will start reading as soon as you install PawnIO.%n%nOpen its official page (pawnio.eu) now?
en.PawnIOViejo=PawnIO %1 is older than 2.2.0, the version Luzeira Sensores needs.%n%nOpen its official page to update it?
en.CarpetaAjena=The folder %1 already exists and does not have the owner or permissions this installer sets. For safety, nothing is installed.%n%nIf you do not know where it comes from, delete it as an administrator and try again.
en.ErrorCarpeta=The folder %1 could not be created (error %2).
en.ErrorServicio=The service could not be configured (step "%1", code %2). Luzeira Sensores will not work until you reinstall it.
en.DescripcionServicio=Reads the CPU temperature for Luzeira (optional module). No network and no inputs.
en.PawnIOSeQueda=PawnIO is not uninstalled: other programs may use it. If you do not need it, remove it from Settings > Apps.
en.NoSeBorro=The folder %1 could not be deleted. Delete it by hand with Luzeira closed.

de.AvisoBeta=Luzeira Sensores ist ein optionales Modul in der Beta. Es installiert einen Windows-Dienst, der die CPU-Temperatur über den Treiber PawnIO liest, der separat installiert wird. Manche Anti-Cheat-Systeme von Spielen (FACEIT) haben PawnIO schon blockiert.
de.EstadoPawnIO=PawnIO installiert: Version %1
de.PawnIONoEsta=PawnIO: nicht installiert (wird zum Lesen der Temperatur gebraucht)
de.FaltaPawnIO=Zum Lesen der Temperatur wird PawnIO gebraucht, ein kostenloser Treiber eines anderen Autors, der hier nicht mitgeliefert werden darf. Auf diesem Computer ist er nicht installiert.%n%nDer Dienst ist schon installiert und beginnt zu lesen, sobald du PawnIO installierst.%n%nJetzt die offizielle Seite (pawnio.eu) öffnen?
de.PawnIOViejo=PawnIO %1 ist älter als 2.2.0, die Version, die Luzeira Sensores braucht.%n%nDie offizielle Seite zum Aktualisieren öffnen?
de.CarpetaAjena=Der Ordner %1 existiert bereits und hat nicht den Besitzer oder die Berechtigungen, die dieses Setup setzt. Aus Sicherheitsgründen wird nichts installiert.%n%nWenn du nicht weißt, woher er kommt, lösche ihn als Administrator und versuche es erneut.
de.ErrorCarpeta=Der Ordner %1 konnte nicht erstellt werden (Fehler %2).
de.ErrorServicio=Der Dienst konnte nicht eingerichtet werden (Schritt „%1“, Code %2). Luzeira Sensores funktioniert erst nach einer Neuinstallation.
de.DescripcionServicio=Liest die CPU-Temperatur für Luzeira (optionales Modul). Ohne Netzwerk und ohne Eingaben.
de.PawnIOSeQueda=PawnIO wird nicht deinstalliert, weil andere Programme es nutzen können. Wenn du es nicht brauchst, entferne es unter Einstellungen > Apps.
de.NoSeBorro=Der Ordner %1 konnte nicht gelöscht werden. Lösche ihn von Hand, wenn Luzeira geschlossen ist.

fr.AvisoBeta=Luzeira Sensores est un module facultatif, en bêta. Il installe un service Windows qui lit la température du processeur avec le pilote PawnIO, installé à part. Certains anti-triche de jeux (FACEIT) ont déjà bloqué PawnIO.
fr.EstadoPawnIO=PawnIO installé : version %1
fr.PawnIONoEsta=PawnIO : non installé (nécessaire pour lire la température)
fr.FaltaPawnIO=Pour lire la température, il faut PawnIO, un pilote gratuit d’un autre auteur qui ne peut pas être inclus ici. Il n’est pas installé sur cet ordinateur.%n%nLe service est déjà installé : il commencera à lire dès que PawnIO sera installé.%n%nOuvrir maintenant sa page officielle (pawnio.eu) ?
fr.PawnIOViejo=PawnIO %1 est antérieur à la 2.2.0, la version requise par Luzeira Sensores.%n%nOuvrir sa page officielle pour le mettre à jour ?
fr.CarpetaAjena=Le dossier %1 existe déjà et n’a pas le propriétaire ou les autorisations que définit ce programme d’installation. Par sécurité, rien n’est installé.%n%nSi vous ne savez pas d’où il vient, supprimez-le en tant qu’administrateur et réessayez.
fr.ErrorCarpeta=Impossible de créer le dossier %1 (erreur %2).
fr.ErrorServicio=Impossible de configurer le service (étape « %1 », code %2). Luzeira Sensores ne fonctionnera pas tant qu’il ne sera pas réinstallé.
fr.DescripcionServicio=Lit la température du processeur pour Luzeira (module facultatif). Sans réseau ni entrées.
fr.PawnIOSeQueda=PawnIO n’est pas désinstallé : d’autres programmes peuvent l’utiliser. Si vous n’en avez pas besoin, supprimez-le dans Paramètres > Applications.
fr.NoSeBorro=Impossible de supprimer le dossier %1. Supprimez-le à la main, Luzeira fermée.

it.AvisoBeta=Luzeira Sensores è un modulo facoltativo, in beta. Installa un servizio di Windows che legge la temperatura della CPU con il driver PawnIO, da installare a parte. Alcuni anti-cheat dei giochi (FACEIT) hanno bloccato PawnIO in passato.
it.EstadoPawnIO=PawnIO installato: versione %1
it.PawnIONoEsta=PawnIO: non installato (serve per leggere la temperatura)
it.FaltaPawnIO=Per leggere la temperatura serve PawnIO, un driver gratuito di un altro autore che non si può includere qui. Su questo computer non è installato.%n%nIl servizio è già installato: inizierà a leggere appena installerai PawnIO.%n%nAprire ora la sua pagina ufficiale (pawnio.eu)?
it.PawnIOViejo=PawnIO %1 è precedente alla 2.2.0, la versione richiesta da Luzeira Sensores.%n%nAprire la sua pagina ufficiale per aggiornarlo?
it.CarpetaAjena=La cartella %1 esiste già e non ha il proprietario o i permessi che imposta questo programma di installazione. Per sicurezza non viene installato nulla.%n%nSe non sai da dove viene, eliminala come amministratore e riprova.
it.ErrorCarpeta=Impossibile creare la cartella %1 (errore %2).
it.ErrorServicio=Impossibile configurare il servizio (passo «%1», codice %2). Luzeira Sensores non funzionerà finché non lo reinstalli.
it.DescripcionServicio=Legge la temperatura della CPU per Luzeira (modulo facoltativo). Senza rete e senza input.
it.PawnIOSeQueda=PawnIO non viene disinstallato: altri programmi possono usarlo. Se non ti serve, rimuovilo da Impostazioni > App.
it.NoSeBorro=Impossibile eliminare la cartella %1. Eliminala a mano con Luzeira chiusa.

pt.AvisoBeta=O Luzeira Sensores é um módulo opcional, em beta. Ele instala um serviço do Windows que lê a temperatura da CPU com o driver PawnIO, instalado à parte. Alguns anti-cheats de jogos (FACEIT) já bloquearam o PawnIO.
pt.EstadoPawnIO=PawnIO instalado: versão %1
pt.PawnIONoEsta=PawnIO: não instalado (necessário para ler a temperatura)
pt.FaltaPawnIO=Para ler a temperatura é preciso o PawnIO, um driver gratuito de outro autor que não pode ser incluído aqui. Ele não está instalado neste computador.%n%nO serviço já está instalado: começará a ler assim que você instalar o PawnIO.%n%nAbrir agora a página oficial dele (pawnio.eu)?
pt.PawnIOViejo=O PawnIO %1 é anterior ao 2.2.0, a versão que o Luzeira Sensores pede.%n%nAbrir a página oficial dele para atualizá-lo?
pt.CarpetaAjena=A pasta %1 já existe e não tem o proprietário ou as permissões que este instalador define. Por segurança, nada foi instalado.%n%nSe você não sabe de onde ela veio, apague-a como administrador e tente de novo.
pt.ErrorCarpeta=Não foi possível criar a pasta %1 (erro %2).
pt.ErrorServicio=Não foi possível configurar o serviço (etapa “%1”, código %2). O Luzeira Sensores não vai funcionar até ser reinstalado.
pt.DescripcionServicio=Lê a temperatura da CPU para a Luzeira (módulo opcional). Sem rede e sem entradas.
pt.PawnIOSeQueda=O PawnIO não é desinstalado: outros programas podem usá-lo. Se você não precisa dele, remova-o em Configurações > Aplicativos.
pt.NoSeBorro=Não foi possível apagar a pasta %1. Apague-a manualmente com a Luzeira fechada.

pl.AvisoBeta=Luzeira Sensores to opcjonalny moduł w wersji beta. Instaluje usługę systemu Windows, która odczytuje temperaturę procesora przez sterownik PawnIO, instalowany osobno. Niektóre systemy anti-cheat w grach (FACEIT) blokowały już PawnIO.
pl.EstadoPawnIO=PawnIO zainstalowany: wersja %1
pl.PawnIONoEsta=PawnIO: nie jest zainstalowany (potrzebny do odczytu temperatury)
pl.FaltaPawnIO=Do odczytu temperatury potrzebny jest PawnIO, bezpłatny sterownik innego autora, którego nie można tu dołączyć. Na tym komputerze go nie ma.%n%nUsługa jest już zainstalowana: zacznie odczytywać, gdy tylko zainstalujesz PawnIO.%n%nCzy otworzyć teraz jego oficjalną stronę (pawnio.eu)?
pl.PawnIOViejo=PawnIO %1 jest starszy niż 2.2.0, wersja wymagana przez Luzeira Sensores.%n%nCzy otworzyć jego oficjalną stronę, aby go zaktualizować?
pl.CarpetaAjena=Folder %1 już istnieje i nie ma właściciela ani uprawnień, które ustawia ten instalator. Ze względów bezpieczeństwa nic nie zostało zainstalowane.%n%nJeśli nie wiesz, skąd pochodzi, usuń go jako administrator i spróbuj ponownie.
pl.ErrorCarpeta=Nie udało się utworzyć folderu %1 (błąd %2).
pl.ErrorServicio=Nie udało się skonfigurować usługi (krok „%1”, kod %2). Luzeira Sensores nie będzie działać, dopóki go nie zainstalujesz ponownie.
pl.DescripcionServicio=Odczytuje temperaturę procesora dla Luzeiry (moduł opcjonalny). Bez sieci i bez danych wejściowych.
pl.PawnIOSeQueda=PawnIO nie zostaje odinstalowany: mogą go używać inne programy. Jeśli go nie potrzebujesz, usuń go w Ustawienia > Aplikacje.
pl.NoSeBorro=Nie udało się usunąć folderu %1. Usuń go ręcznie, gdy Luzeira będzie zamknięta.

tr.AvisoBeta=Luzeira Sensores, beta aşamasında isteğe bağlı bir modüldür. Ayrıca kurulan PawnIO sürücüsüyle CPU sıcaklığını okuyan bir Windows hizmeti kurar. Bazı oyun hile önleme sistemleri (FACEIT) PawnIO'yu daha önce engelledi.
tr.EstadoPawnIO=PawnIO kurulu: sürüm %1
tr.PawnIONoEsta=PawnIO: kurulu değil (sıcaklığı okumak için gerekli)
tr.FaltaPawnIO=Sıcaklığı okumak için PawnIO gerekiyor; başka bir yazarın ücretsiz sürücüsü olduğu için buraya eklenemiyor. Bu bilgisayarda kurulu değil.%n%nHizmet zaten kuruldu: PawnIO'yu kurar kurmaz okumaya başlayacak.%n%nResmî sayfası (pawnio.eu) şimdi açılsın mı?
tr.PawnIOViejo=PawnIO %1, Luzeira Sensores'in istediği 2.2.0 sürümünden eski.%n%nGüncellemek için resmî sayfası açılsın mı?
tr.CarpetaAjena=%1 klasörü zaten var ve bu kurulumun verdiği sahip ya da izinlere sahip değil. Güvenlik için hiçbir şey kurulmadı.%n%nNereden geldiğini bilmiyorsan, yönetici olarak sil ve yeniden dene.
tr.ErrorCarpeta=%1 klasörü oluşturulamadı (hata %2).
tr.ErrorServicio=Hizmet yapılandırılamadı (adım “%1”, kod %2). Luzeira Sensores yeniden kurulana kadar çalışmayacak.
tr.DescripcionServicio=Luzeira için CPU sıcaklığını okur (isteğe bağlı modül). Ağ yok, girdi yok.
tr.PawnIOSeQueda=PawnIO kaldırılmıyor: başka programlar da kullanabilir. Gerekmiyorsa Ayarlar > Uygulamalar'dan kaldırabilirsin.
tr.NoSeBorro=%1 klasörü silinemedi. Luzeira kapalıyken elle sil.

ru.AvisoBeta=Luzeira Sensores — необязательный модуль в бета-версии. Он устанавливает службу Windows, которая читает температуру процессора через драйвер PawnIO, устанавливаемый отдельно. Некоторые античиты игр (FACEIT) уже блокировали PawnIO.
ru.EstadoPawnIO=PawnIO установлен: версия %1
ru.PawnIONoEsta=PawnIO: не установлен (нужен для чтения температуры)
ru.FaltaPawnIO=Для чтения температуры нужен PawnIO — бесплатный драйвер другого автора, который нельзя включить сюда. На этом компьютере его нет.%n%nСлужба уже установлена: она начнёт читать, как только вы установите PawnIO.%n%nОткрыть его официальную страницу (pawnio.eu)?
ru.PawnIOViejo=PawnIO %1 старше версии 2.2.0, которая нужна Luzeira Sensores.%n%nОткрыть официальную страницу, чтобы обновить его?
ru.CarpetaAjena=Папка %1 уже существует, и у неё не тот владелец или не те права, которые задаёт этот установщик. В целях безопасности ничего не установлено.%n%nЕсли вы не знаете, откуда она, удалите её от имени администратора и попробуйте снова.
ru.ErrorCarpeta=Не удалось создать папку %1 (ошибка %2).
ru.ErrorServicio=Не удалось настроить службу (шаг «%1», код %2). Luzeira Sensores не будет работать, пока вы его не переустановите.
ru.DescripcionServicio=Читает температуру процессора для Luzeira (необязательный модуль). Без сети и без входных данных.
ru.PawnIOSeQueda=PawnIO не удаляется: его могут использовать другие программы. Если он вам не нужен, удалите его в «Параметры» > «Приложения».
ru.NoSeBorro=Не удалось удалить папку %1. Удалите её вручную, закрыв Luzeira.

zh.AvisoBeta=Luzeira Sensores 是一个可选模块，目前为测试版。它会安装一个 Windows 服务，通过需单独安装的 PawnIO 驱动读取 CPU 温度。部分游戏反作弊系统（FACEIT）曾拦截过 PawnIO。
zh.EstadoPawnIO=已安装 PawnIO：版本 %1
zh.PawnIONoEsta=PawnIO：未安装（读取温度需要它）
zh.FaltaPawnIO=读取温度需要 PawnIO，这是另一位作者的免费驱动，无法随本程序附带。这台电脑上没有安装它。%n%n服务已经安装好：安装 PawnIO 后就会开始读取。%n%n现在打开它的官方页面（pawnio.eu）吗？
zh.PawnIOViejo=PawnIO %1 早于 Luzeira Sensores 需要的 2.2.0 版本。%n%n打开它的官方页面进行更新吗？
zh.CarpetaAjena=文件夹 %1 已存在，但它的所有者或权限与本安装程序设置的不同。为安全起见，未安装任何内容。%n%n如果你不知道它从何而来，请以管理员身份删除它后重试。
zh.ErrorCarpeta=无法创建文件夹 %1（错误 %2）。
zh.ErrorServicio=无法配置服务（步骤“%1”，代码 %2）。重新安装之前，Luzeira Sensores 将无法工作。
zh.DescripcionServicio=为 Luzeira 读取 CPU 温度（可选模块）。不联网，不接收任何输入。
zh.PawnIOSeQueda=PawnIO 不会被卸载：其他程序可能在使用它。如果不需要，可在“设置 > 应用”中将其删除。
zh.NoSeBorro=无法删除文件夹 %1。请在关闭 Luzeira 后手动删除。

ja.AvisoBeta=Luzeira Sensores はベータ版のオプションモジュールです。別途インストールする PawnIO ドライバーで CPU 温度を読み取る Windows サービスをインストールします。一部のゲームのアンチチート（FACEIT）が PawnIO をブロックしたことがあります。
ja.EstadoPawnIO=PawnIO インストール済み: バージョン %1
ja.PawnIONoEsta=PawnIO: 未インストール（温度の読み取りに必要です）
ja.FaltaPawnIO=温度を読み取るには PawnIO が必要です。これは別の作者による無料のドライバーで、ここに同梱することはできません。このコンピューターにはインストールされていません。%n%nサービスはインストール済みです。PawnIO をインストールすると読み取りを始めます。%n%n公式ページ（pawnio.eu）を今すぐ開きますか？
ja.PawnIOViejo=PawnIO %1 は Luzeira Sensores が必要とする 2.2.0 より古いバージョンです。%n%n更新するために公式ページを開きますか？
ja.CarpetaAjena=フォルダー %1 はすでに存在し、このインストーラーが設定する所有者またはアクセス許可と異なります。安全のため、何もインストールしません。%n%n心当たりがない場合は、管理者として削除してからもう一度お試しください。
ja.ErrorCarpeta=フォルダー %1 を作成できませんでした（エラー %2）。
ja.ErrorServicio=サービスを構成できませんでした（手順「%1」、コード %2）。再インストールするまで Luzeira Sensores は動作しません。
ja.DescripcionServicio=Luzeira のために CPU 温度を読み取ります（オプションモジュール）。ネットワークも入力も使いません。
ja.PawnIOSeQueda=PawnIO はアンインストールされません。ほかのプログラムが使っている可能性があります。不要な場合は「設定」>「アプリ」から削除してください。
ja.NoSeBorro=フォルダー %1 を削除できませんでした。Luzeira を閉じてから手動で削除してください。

[InstallDelete]
; Al actualizar, fuera los binarios de la versión anterior: no deben quedar DLL que ya no
; son nuestras en la carpeta de la que el servicio carga.
Type: files; Name: "{app}\*.dll"
Type: files; Name: "{app}\*.json"
Type: files; Name: "{app}\{#ServiceExe}"

[Files]
; Sin las piezas de depuración y volcados de .NET ni QUIC: el servicio no las usa (el
; runtime arranca sin ellas: probado con la herramienta escribir-muestra publicada igual)
; y cada DLL de más en un proceso SYSTEM es superficie.
Source: "{#SourceDir}\*"; DestDir: "{app}"; Excludes: "*.pdb,createdump.exe,mscordbi.dll,mscordaccore*.dll,Microsoft.DiaSymReader.Native.amd64.dll,msquic.dll"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\modulo-sensores\TERCEROS.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\modulo-sensores\licencias\*.txt"; DestDir: "{app}\licencias"; Flags: ignoreversion

[Registry]
; Origen del Visor de eventos (los logs del servicio van solo ahí). El fichero de
; mensajes es el que trae System.Diagnostics.EventLog.
Root: HKLM64; Subkey: "SYSTEM\CurrentControlSet\Services\EventLog\Application\{#ServiceName}"; ValueType: expandsz; ValueName: "EventMessageFile"; ValueData: "{app}\System.Diagnostics.EventLog.Messages.dll"; Flags: uninsdeletekey

[UninstallDelete]
Type: dirifempty; Name: "{app}"

[Code]
const
  ERROR_ALREADY_EXISTS = 183;
  ERROR_SERVICE_EXISTS = 1073;
  ERROR_SERVICE_ALREADY_RUNNING = 1056;
  { FILE_ATTRIBUTE_DIRECTORY y FILE_ATTRIBUTE_REPARSE_POINT ya los define Inno. }
  INVALID_FILE_ATTRIBUTES = $FFFFFFFF;
  SDDL_REVISION_1 = 1;
  SE_FILE_OBJECT = 1;
  OWNER_SECURITY_INFORMATION = $1;
  DACL_SECURITY_INFORMATION = $4;
  SC_MANAGER_CONNECT = $1;
  SERVICE_QUERY_STATUS = $4;
  SERVICE_STOP = $20;
  SERVICE_CONTROL_STOP = 1;
  SERVICE_STOPPED = 1;

type
  { SECURITY_ATTRIBUTES con el relleno de 64 bits a mano: los registros de Pascal Script
    van siempre «empaquetados» (notas de Inno 7). 4 + 4 + 8 + 4 + 4 = 24 bytes. }
  TSecurityAttributes = record
    nLength: DWORD;
    Padding1: DWORD;
    lpSecurityDescriptor: NativeUInt;
    bInheritHandle: Integer;
    Padding2: DWORD;
  end;

  TServiceStatus = record
    dwServiceType: DWORD;
    dwCurrentState: DWORD;
    dwControlsAccepted: DWORD;
    dwWin32ExitCode: DWORD;
    dwServiceSpecificExitCode: DWORD;
    dwCheckPoint: DWORD;
    dwWaitHint: DWORD;
  end;

function ConvertStringSecurityDescriptorToSecurityDescriptorW(StringSecurityDescriptor: String; StringSDRevision: DWORD; var SecurityDescriptor: NativeUInt; SecurityDescriptorSize: NativeUInt): Integer;
  external 'ConvertStringSecurityDescriptorToSecurityDescriptorW@advapi32.dll stdcall';
function ConvertSecurityDescriptorToStringSecurityDescriptorW(SecurityDescriptor: NativeUInt; RequestedStringSDRevision: DWORD; SecurityInformation: DWORD; var StringSecurityDescriptor: NativeUInt; var StringSecurityDescriptorLen: DWORD): Integer;
  external 'ConvertSecurityDescriptorToStringSecurityDescriptorW@advapi32.dll stdcall';
function GetNamedSecurityInfoW(ObjectName: String; ObjectType: Integer; SecurityInfo: DWORD; var Owner: NativeUInt; var Group: NativeUInt; var Dacl: NativeUInt; var Sacl: NativeUInt; var SecurityDescriptor: NativeUInt): DWORD;
  external 'GetNamedSecurityInfoW@advapi32.dll stdcall';
function CreateDirectoryW(PathName: String; var SecurityAttributes: TSecurityAttributes): Integer;
  external 'CreateDirectoryW@kernel32.dll stdcall';
function GetFileAttributesW(FileName: String): DWORD;
  external 'GetFileAttributesW@kernel32.dll stdcall';
function LocalFree(Memory: NativeUInt): NativeUInt;
  external 'LocalFree@kernel32.dll stdcall';
function lstrlenW(Text: NativeUInt): Integer;
  external 'lstrlenW@kernel32.dll stdcall';
procedure RtlMoveMemory(Destination: String; Source: NativeUInt; Length: NativeUInt);
  external 'RtlMoveMemory@kernel32.dll stdcall';
function OpenSCManagerW(MachineName: NativeUInt; DatabaseName: NativeUInt; DesiredAccess: DWORD): NativeUInt;
  external 'OpenSCManagerW@advapi32.dll stdcall';
function OpenServiceW(Manager: NativeUInt; ServiceName: String; DesiredAccess: DWORD): NativeUInt;
  external 'OpenServiceW@advapi32.dll stdcall';
function ControlService(Service: NativeUInt; Control: DWORD; var Status: TServiceStatus): Integer;
  external 'ControlService@advapi32.dll stdcall';
function QueryServiceStatus(Service: NativeUInt; var Status: TServiceStatus): Integer;
  external 'QueryServiceStatus@advapi32.dll stdcall';
function CloseServiceHandle(Handle: NativeUInt): Integer;
  external 'CloseServiceHandle@advapi32.dll stdcall';

function DataFolder: String;
begin
  (* «commonappdata» lo resuelve Inno preguntando a Windows, no a la variable de entorno. *)
  Result := ExpandConstant('{commonappdata}\{#DataFolderName}');
end;

{ Propietario y DACL de una carpeta, en SDDL (p. ej. «O:BAD:P(A;OICI;FA;;;SY)…»). }
function FolderSddl(Path: String): String;
var
  Owner, Group, Dacl, Sacl, Descriptor, Text: NativeUInt;
  TextLength: DWORD;
  Chars: Integer;
begin
  Result := '';
  if GetNamedSecurityInfoW(Path, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION or DACL_SECURITY_INFORMATION, Owner, Group, Dacl, Sacl, Descriptor) <> 0 then
    exit;
  try
    if ConvertSecurityDescriptorToStringSecurityDescriptorW(Descriptor, SDDL_REVISION_1, OWNER_SECURITY_INFORMATION or DACL_SECURITY_INFORMATION, Text, TextLength) <> 0 then
    begin
      Chars := lstrlenW(Text);
      SetLength(Result, Chars);
      RtlMoveMemory(Result, Text, Chars * 2);
      LocalFree(Text);
    end;
  finally
    LocalFree(Descriptor);
  end;
end;

{ ¿Es el descriptor que pone este instalador? Propietario Administradores o SYSTEM y la
  DACL exacta (Windows puede añadir la marca AI, «autoheredada», al leerla). }
function IsExpectedSddl(Actual, Expected: String): Boolean;
var
  Dacl: String;
begin
  { Expected empieza por «O:BA»; lo que importa a partir de «D:». }
  Dacl := Copy(Expected, Pos('D:', Expected), MaxInt);
  Result := (Actual = 'O:BA' + Dacl) or (Actual = 'O:SY' + Dacl);
  if not Result then
  begin
    StringChangeEx(Dacl, 'D:P(', 'D:PAI(', True);
    Result := (Actual = 'O:BA' + Dacl) or (Actual = 'O:SY' + Dacl);
  end;
end;

{ Crea la carpeta con su descriptor o, si ya existe, comprueba que es la que pone este
  instalador. Devuelve '' si está bien o el mensaje para el usuario. }
function EnsureFolder(Path, Sddl: String): String;
var
  Attributes: TSecurityAttributes;
  Descriptor: NativeUInt;
  Existing: DWORD;
  Error: DWORD;
begin
  Result := '';
  Existing := GetFileAttributesW(Path);
  if Existing = INVALID_FILE_ATTRIBUTES then
  begin
    if ConvertStringSecurityDescriptorToSecurityDescriptorW(Sddl, SDDL_REVISION_1, Descriptor, 0) = 0 then
    begin
      Result := FmtMessage(CustomMessage('ErrorCarpeta'), [Path, IntToStr(DLLGetLastError)]);
      exit;
    end;
    try
      Attributes.nLength := 24;
      Attributes.Padding1 := 0;
      Attributes.lpSecurityDescriptor := Descriptor;
      Attributes.bInheritHandle := 0;
      Attributes.Padding2 := 0;
      if CreateDirectoryW(Path, Attributes) <> 0 then
        Error := 0
      else
        Error := DLLGetLastError;
    finally
      LocalFree(Descriptor);
    end;
    if (Error <> 0) and (Error <> ERROR_ALREADY_EXISTS) then
    begin
      Result := FmtMessage(CustomMessage('ErrorCarpeta'), [Path, IntToStr(Error)]);
      exit;
    end;
    { Recién creada o creada por otro justo ahora: se comprueba igual que una que ya
      existía (que de verdad quedó con nuestro propietario y nuestra DACL). }
    if Error = 0 then
      Log('Carpeta creada con su DACL: ' + Path);
    Existing := GetFileAttributesW(Path);
  end;
  { Un enlace (junction), un fichero o una carpeta con otro dueño o permisos → no se
    instala (ADR-008: nunca se adopta lo que ya estaba). }
  if (Existing = INVALID_FILE_ATTRIBUTES) or ((Existing and FILE_ATTRIBUTE_REPARSE_POINT) <> 0) or ((Existing and FILE_ATTRIBUTE_DIRECTORY) = 0) or not IsExpectedSddl(FolderSddl(Path), Sddl) then
  begin
    Log('Carpeta ajena: ' + Path + ' = ' + FolderSddl(Path));
    Result := FmtMessage(CustomMessage('CarpetaAjena'), [Path]);
  end;
end;

{ Para el servicio, si existe, y espera hasta 30 s a que se pare. }
procedure StopService;
var
  Manager, Service: NativeUInt;
  Status: TServiceStatus;
  I: Integer;
begin
  Manager := OpenSCManagerW(0, 0, SC_MANAGER_CONNECT);
  if Manager = 0 then
    exit;
  try
    Service := OpenServiceW(Manager, '{#ServiceName}', SERVICE_STOP or SERVICE_QUERY_STATUS);
    if Service = 0 then
      exit;
    try
      ControlService(Service, SERVICE_CONTROL_STOP, Status);
      for I := 1 to 60 do
      begin
        if (QueryServiceStatus(Service, Status) = 0) or (Status.dwCurrentState = SERVICE_STOPPED) then
          break;
        Sleep(500);
      end;
    finally
      CloseServiceHandle(Service);
    end;
  finally
    CloseServiceHandle(Manager);
  end;
end;

function ServiceExists: Boolean;
var
  Manager, Service: NativeUInt;
begin
  Result := False;
  Manager := OpenSCManagerW(0, 0, SC_MANAGER_CONNECT);
  if Manager = 0 then
    exit;
  Service := OpenServiceW(Manager, '{#ServiceName}', SERVICE_QUERY_STATUS);
  Result := Service <> 0;
  if Result then
    CloseServiceHandle(Service);
  CloseServiceHandle(Manager);
end;

{ sc.exe de System32 (ruta completa: nunca uno que esté junto al instalador). }
function Sc(Params: String; var Code: Integer): Boolean;
begin
  Log('sc ' + Params);
  Result := Exec(ExpandConstant('{sys}\sc.exe'), Params, ExpandConstant('{sys}'), SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
  Log('  -> ' + IntToStr(Code));
end;

var
  ServiceErrors: String;

procedure ScStep(Name, Params: String; AllowedCode: Integer);
var
  Code: Integer;
begin
  if not Sc(Params, Code) and (Code <> AllowedCode) and (ServiceErrors = '') then
    ServiceErrors := FmtMessage(CustomMessage('ErrorServicio'), [Name, IntToStr(Code)]);
end;

procedure RegisterService;
var
  Exe, Config: String;
begin
  ServiceErrors := '';
  Exe := ExpandConstant('{app}\{#ServiceExe}');
  { binPath entre comillas (dentro de comillas para sc): una ruta con espacios sin
    comillas deja que Windows pruebe «C:\Program.exe». }
  Config := 'binPath= "\"' + Exe + '\"" start= auto obj= LocalSystem DisplayName= "Luzeira Sensores"';
  if ServiceExists then
    ScStep('config', 'config {#ServiceName} ' + Config, 0)
  else
    ScStep('create', 'create {#ServiceName} type= own ' + Config, 0);
  if ServiceErrors <> '' then
    exit;
  ScStep('description', 'description {#ServiceName} "' + CustomMessage('DescripcionServicio') + '"', 0);
  { SID propio del servicio (NT SERVICE\LuzeiraSensores). }
  ScStep('sidtype', 'sidtype {#ServiceName} unrestricted', 0);
  { Privilegios: solo SeChangeNotifyPrivilege (Windows lo deja siempre; sin él no se
    atraviesan carpetas). Lo que hace el servicio no pide ninguno más:
    - PawnIO no mira privilegios, solo la ACL del dispositivo (SYSTEM y Administradores;
      PawnIO/src/driver.cpp y PawnIO.inf.in);
    - el fichero se crea y se mapea por los permisos de la carpeta, y la sección no
      tiene nombre;
    - los mutex Global\ de LibreHardwareMonitor no piden SeCreateGlobalPrivilege: ese
      privilegio es para secciones y enlaces simbólicos con nombre en Global\ creados
      desde una sesión que no es la 0, y el servicio corre en la 0 (crear un mutex o un
      evento en Global\ no lo pide ni a un usuario: medido en la revisión de seguridad).
    Si un día el servicio no arrancase por un privilegio, se vería en el Visor de
    eventos; no se añade ninguno «por si acaso». }
  ScStep('privs', 'privs {#ServiceName} SeChangeNotifyPrivilege', 0);
  { Recuperación: solo reiniciar (al minuto), nunca ejecutar un programa. }
  ScStep('failure', 'failure {#ServiceName} reset= 86400 actions= restart/60000/restart/60000/restart/60000', 0);
  ScStep('sdset', 'sdset {#ServiceName} {#ServiceSddl}', 0);
  { Entorno del servicio:
    - DOTNET_EnableDiagnostics=0: sin depurador, perfilador ni EventPipe de .NET, que
      serían una entrada al proceso SYSTEM;
    - PATH solo con System32, escrito con la ruta ya resuelta: un REG_MULTI_SZ no
      expande variables como %SystemRoot%. }
  if not RegWriteMultiStringValue(HKLM64, 'SYSTEM\CurrentControlSet\Services\{#ServiceName}', 'Environment', 'DOTNET_EnableDiagnostics=0' + #0 + 'PATH=' + ExpandConstant('{sys}')) and (ServiceErrors = '') then
    ServiceErrors := FmtMessage(CustomMessage('ErrorServicio'), ['Environment', '-']);
  ScStep('start', 'start {#ServiceName}', ERROR_SERVICE_ALREADY_RUNNING);
end;

{ La versión de PawnIO, de su entrada de desinstalación (igual que LibreHardwareMonitor:
  PawnIo.cs, v0.9.6). }
function PawnIoVersion(var Version: String): Boolean;
begin
  Result := RegQueryStringValue(HKLM64, '{#PawnIoUninstallKey}', 'DisplayVersion', Version) or
            RegQueryStringValue(HKLM32, '{#PawnIoUninstallKey}', 'DisplayVersion', Version);
end;

function PawnIoIsOld(Version: String): Boolean;
var
  Installed, Needed: Int64;
begin
  Result := StrToVersion(Version, Installed) and StrToVersion('2.2.0', Needed) and (ComparePackedVersion(Installed, Needed) < 0);
end;

procedure OfferPawnIo;
var
  Version, Text: String;
  Code: Integer;
begin
  if PawnIoVersion(Version) and not PawnIoIsOld(Version) then
    exit;
  if PawnIoVersion(Version) then
    Text := FmtMessage(CustomMessage('PawnIOViejo'), [Version])
  else
    Text := CustomMessage('FaltaPawnIO');
  Log('PawnIO falta o es antiguo.');
  if WizardSilent then
    exit;
  { El navegador se abre como el usuario que lanzó el instalador, no como administrador. }
  if MsgBox(Text, mbInformation, MB_YESNO) = IDYES then
    ShellExecAsOriginalUser('open', '{#PawnIoUrl}', '', '', SW_SHOWNORMAL, ewNoWait, Code);
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo, MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
var
  Version: String;
begin
  Result := CustomMessage('AvisoBeta') + NewLine + NewLine;
  if PawnIoVersion(Version) then
    Result := Result + FmtMessage(CustomMessage('EstadoPawnIO'), [Version])
  else
    Result := Result + CustomMessage('PawnIONoEsta');
  Result := Result + NewLine + NewLine + MemoDirInfo;
  if MemoTasksInfo <> '' then
    Result := Result + NewLine + NewLine + MemoTasksInfo;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { Antes de tocar nada: las dos carpetas, o ninguna instalación. }
  Result := EnsureFolder(ExpandConstant('{app}'), '{#AppFolderSddl}');
  if Result = '' then
    Result := EnsureFolder(DataFolder, '{#DataFolderSddl}');
  if Result = '' then
    StopService;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    { Comprobado tras instalar (ADR-008, punto 4): nadie ha cambiado la carpeta. }
    if not IsExpectedSddl(FolderSddl(ExpandConstant('{app}')), '{#AppFolderSddl}') then
      ServiceErrors := FmtMessage(CustomMessage('CarpetaAjena'), [ExpandConstant('{app}')])
    else
      RegisterService;
    if ServiceErrors <> '' then
    begin
      Log(ServiceErrors);
      SuppressibleMsgBox(ServiceErrors, mbError, MB_OK, IDOK);
    end;
    OfferPawnIo;
  end;
end;

{ Borra la carpeta de datos, SOLO si es la que puso este instalador (propietario y DACL
  exactos) y sin borrado recursivo: sensores.bin y luego la carpeta vacía. Si la carpeta
  la hubiera creado otro (porque llegó a faltar), un borrado recursivo como administrador
  podría seguir un enlace y borrar otra cosa (revisión de seguridad, MEDIA 2).
  Si Luzeira tiene abierto sensores.bin, lo suelta unos 5 s después de que el servicio se
  pare (modulo.STALE_SECONDS): se reintenta 20 s. }
procedure DeleteDataFolder;
var
  Path: String;
  Attributes: DWORD;
  I: Integer;
begin
  Path := DataFolder;
  Attributes := GetFileAttributesW(Path);
  if Attributes = INVALID_FILE_ATTRIBUTES then
    exit;
  if ((Attributes and FILE_ATTRIBUTE_REPARSE_POINT) <> 0) or ((Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0) or not IsExpectedSddl(FolderSddl(Path), '{#DataFolderSddl}') then
  begin
    Log('No se borra una carpeta de datos ajena: ' + Path + ' = ' + FolderSddl(Path));
    SuppressibleMsgBox(FmtMessage(CustomMessage('NoSeBorro'), [Path]), mbError, MB_OK, IDOK);
    exit;
  end;
  for I := 1 to 20 do
  begin
    if FileExists(Path + '\sensores.bin') then
      DeleteFile(Path + '\sensores.bin');
    if RemoveDir(Path) or not DirExists(Path) then
      exit;
    Sleep(1000);
  end;
  if DirExists(Path) then
    SuppressibleMsgBox(FmtMessage(CustomMessage('NoSeBorro'), [Path]), mbError, MB_OK, IDOK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
begin
  if CurUninstallStep = usUninstall then
  begin
    StopService;
    Sc('delete {#ServiceName}', Code);
  end;
  if CurUninstallStep = usPostUninstall then
  begin
    DeleteDataFolder;
    SuppressibleMsgBox(CustomMessage('PawnIOSeQueda'), mbInformation, MB_OK, IDOK);
  end;
end;
