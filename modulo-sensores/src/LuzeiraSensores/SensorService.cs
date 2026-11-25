using System.Diagnostics;
using System.Reflection;
using System.ServiceProcess;
using LuzeiraSensores.Core;

namespace LuzeiraSensores;

/// <summary>
/// El servicio «LuzeiraSensores»: lee la CPU y publica en el fichero cada 1,5 s.
/// </summary>
/// <remarks>
/// <b>Sin entradas</b> (ADR-007): no escucha en la red ni en tuberías, no lee ficheros que
/// pueda escribir un usuario, ignora los argumentos de arranque y no atiende controles
/// propios (no hay <c>OnCustomCommand</c>; además el instalador quita a los usuarios el
/// permiso de mandarlos). No tiene ninguna configuración: lo único que lee son los
/// sensores de la CPU.
/// </remarks>
internal sealed class SensorService : ServiceBase
{
    /// <summary>Cada cuánto se publica.</summary>
    public static readonly TimeSpan Interval = TimeSpan.FromMilliseconds(1500);

    /// <summary>Pausa mínima entre dos ciclos, aunque leer haya tardado: nunca más de una
    /// lectura por segundo, pase lo que pase.</summary>
    public static readonly TimeSpan MinimumPause = TimeSpan.FromSeconds(1);

    /// <summary>Cada cuánto se vuelve a mirar si ha aparecido PawnIO.</summary>
    private static readonly TimeSpan PawnIoRetry = TimeSpan.FromSeconds(30);

    /// <summary>Cada cuánto se vuelve a intentar abrir la carpeta si no estaba bien.</summary>
    private static readonly TimeSpan FolderRetry = TimeSpan.FromSeconds(60);

    /// <summary>Errores de LHM seguidos antes de cerrarlo y volver a abrirlo.</summary>
    private const int MaxConsecutiveErrors = 10;

    /// <summary>Ciclos seguidos sin conseguir el bus antes de avisar (unos 30 s).</summary>
    private const int MissedBusWarning = 20;

    private static readonly TimeSpan StopTimeout = TimeSpan.FromSeconds(10);

    private readonly ManualResetEvent _stop = new(false);
    private readonly ServiceLog _log = new();
    private Thread? _worker;

    public SensorService()
    {
        ServiceName = "LuzeiraSensores";
        AutoLog = false;  // los avisos los escribe ServiceLog, con su origen
        CanStop = true;
        CanShutdown = true;
        CanPauseAndContinue = false;
        CanHandlePowerEvent = false;
        CanHandleSessionChangeEvent = false;
    }

    /// <summary>La versión que va en el fichero (la de Directory.Build.props).</summary>
    public static string ModuleVersion { get; } =
        typeof(SensorService).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "0.0.0";

    protected override void OnStart(string[] args)
    {
        // Los argumentos se ignoran a propósito (solo los podría pasar un administrador,
        // pero el servicio no tiene ninguna opción).
        _worker = new Thread(Run) { IsBackground = true, Name = "LuzeiraSensores" };
        _worker.Start();
    }

    protected override void OnStop() => StopWorker();

    protected override void OnShutdown() => StopWorker();

    private void StopWorker()
    {
        _stop.Set();
        if (_worker is not null && !_worker.Join(StopTimeout))
            _log.Warning(EventIds.SlowStop, "El servicio no terminó a tiempo de cerrarse.");
    }

    private void Run()
    {
        _log.Info(EventIds.Started, $"Luzeira Sensores {ModuleVersion} arrancado.");

        string folder = SectionFile.DefaultFolder();
        SectionFile? file = null;
        SeqlockPublisher? publisher = null;
        LhmSensors? lhm = null;
        MutexGuard? guard = null;
        int missedBus = 0;
        var tracker = new StatusTracker();
        bool pawnIo = false;
        int errors = 0;
        long nextFolderTry = 0;
        long nextPawnIoCheck = 0;

        try
        {
            do
            {
                long started = Stopwatch.GetTimestamp();
                long now = Environment.TickCount64;

                if (file is null && now >= nextFolderTry)
                {
                    file = SectionFile.TryOpen(folder, out string? problem, out SectionOpenFailure failure);
                    nextFolderTry = now + (long)FolderRetry.TotalMilliseconds;
                    if (file is null)
                    {
                        int id = failure == SectionOpenFailure.Busy ? EventIds.FileBusy : EventIds.FolderUntrusted;
                        var type = failure == SectionOpenFailure.Busy ? EventLogEntryType.Warning : EventLogEntryType.Error;
                        _log.Once("carpeta:" + problem, id, problem ?? "No se pudo abrir el fichero.", type);
                    }
                    else
                    {
                        ulong initial = (ulong)DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() << 1;
                        publisher = new SeqlockPublisher(file, initial);
                    }
                }

                if (lhm is null && now >= nextPawnIoCheck)
                {
                    nextPawnIoCheck = now + (long)PawnIoRetry.TotalMilliseconds;
                    // Primero, los nombres de los mutex de LHM: si los ocupa otro con un
                    // objeto que no podemos usar, no se lee (ver MutexGuard).
                    guard ??= AcquireNames();
                    pawnIo = LhmSensors.PawnIoAvailable();
                    if (!pawnIo)
                    {
                        _log.Once("pawnio", EventIds.NoPawnIo, "No se puede abrir el driver PawnIO: ¿está instalado? Se vuelve a mirar cada 30 s.");
                    }
                    else if (guard is not null)
                    {
                        _log.Forget("pawnio");
                        lhm = OpenLhm(guard.Pci);
                    }
                }

                MappedReadings? readings = null;
                if (lhm is not null)
                {
                    try
                    {
                        readings = lhm.Read();
                        errors = 0;
                        // null = no se consiguió el bus PCI en este ciclo.
                        missedBus = readings is null ? missedBus + 1 : 0;
                        if (missedBus == MissedBusWarning)
                            _log.Once("bus", EventIds.BusBusy, $"Otro programa no suelta el bus PCI (mutex de LibreHardwareMonitor) desde hace {MissedBusWarning} ciclos: no se publican lecturas.");
                    }
                    catch (Exception e)
                    {
                        _log.Once("lectura:" + e.GetType().Name, EventIds.ReadFailed, $"Falló una lectura de LibreHardwareMonitor ({e.GetType().Name}).");
                        if (++errors >= MaxConsecutiveErrors)
                        {
                            lhm.Dispose();
                            lhm = null;
                            errors = 0;
                        }
                    }
                }

                ModuleStatus status = tracker.Next(pawnIo, readings);
                if (status == ModuleStatus.UnsupportedCpu)
                    _log.Once("cpu", EventIds.UnsupportedCpu, "PawnIO está, pero LibreHardwareMonitor no da ningún sensor de temperatura para este procesador.");
                IReadOnlyList<Entry> entries = status == ModuleStatus.Ok
                    ? (readings ?? Empty).ToEntries()
                    : [];
                long unixMs = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds();
                publisher?.Publish(sequence => SectionWriter.Build(status, entries, sequence, unixMs, ModuleVersion));

                TimeSpan elapsed = Stopwatch.GetElapsedTime(started);
                TimeSpan pause = Interval - elapsed;
                if (pause < MinimumPause)
                    pause = MinimumPause;
                if (_stop.WaitOne(pause))
                    break;
            }
            while (true);
        }
        catch (Exception e)
        {
            // No debería pasar; si pasa, se dice y el servicio termina (el administrador
            // de servicios lo reinicia: «sc failure» solo reinicia).
            _log.Error(EventIds.Crashed, $"El servicio se ha detenido por un error ({e.GetType().Name}).");
            Environment.Exit(1);
        }
        finally
        {
            lhm?.Dispose();
            guard?.Dispose();
            file?.Dispose();
        }
    }

    /// <summary>Ocupa los nombres de LHM. Si alguno es hostil, se dice una vez, se suelta
    /// todo y se devuelve <c>null</c> (se reintenta con PawnIO, cada 30 s).</summary>
    private MutexGuard? AcquireNames()
    {
        MutexGuard guard = MutexGuard.Acquire();
        if (guard.Hostile.Count == 0)
        {
            _log.Forget("mutex");
            return guard;
        }

        _log.Once("mutex", EventIds.HostileMutex, $"Otro proceso ocupa el nombre {string.Join(", ", guard.Hostile)} con un objeto que no se puede usar: no se lee la CPU hasta que lo suelte.", EventLogEntryType.Error);
        guard.Dispose();
        return null;
    }

    private static readonly MappedReadings Empty = new(new Dictionary<string, double?>(), HasTemperatureSensor: false);

    private LhmSensors? OpenLhm(Mutex? pciMutex)
    {
        Version? version = LhmSensors.PawnIoVersion();
        if (version is not null && version < new Version(2, 2, 0))
            _log.Once("pawnio-viejo", EventIds.OldPawnIo, $"PawnIO {version} es anterior a la 2.2.0: conviene actualizarlo.");
        try
        {
            return LhmSensors.Open(pciMutex);
        }
        catch (Exception e)
        {
            _log.Once("abrir:" + e.GetType().Name, EventIds.OpenFailed, $"No se pudo abrir LibreHardwareMonitor ({e.GetType().Name}).", EventLogEntryType.Error);
            return null;
        }
    }


    protected override void Dispose(bool disposing)
    {
        if (disposing)
            _stop.Dispose();
        base.Dispose(disposing);
    }

    /// <summary>Números de evento (para filtrar en el Visor de eventos).</summary>
    private static class EventIds
    {
        public const int Started = 1;
        public const int FolderUntrusted = 10;
        public const int FileBusy = 11;
        public const int NoPawnIo = 20;
        public const int OldPawnIo = 21;
        public const int OpenFailed = 22;
        public const int UnsupportedCpu = 23;
        public const int HostileMutex = 24;
        public const int ReadFailed = 30;
        public const int BusBusy = 31;
        public const int SlowStop = 40;
        public const int Crashed = 90;
    }
}
