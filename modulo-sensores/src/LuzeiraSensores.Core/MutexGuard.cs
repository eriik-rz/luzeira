using System.Security.AccessControl;
using System.Security.Principal;

namespace LuzeiraSensores.Core;

/// <summary>
/// Ocupa (crea o abre) los cinco mutex con nombre de LibreHardwareMonitor antes de abrirlo
/// y los mantiene abiertos mientras vive el servicio (revisión de seguridad del módulo,
/// hallazgo MEDIA 1).
/// </summary>
/// <remarks>
/// Por qué: LHM 0.9.6 (Mutexes.cs) crea <c>Global\Access_PCI</c> y otros cuatro con
/// control total para «Todos», y crear un mutex o un evento en <c>Global\</c> no exige
/// ningún privilegio. Un usuario normal puede adelantarse con ese nombre:
/// - con un <b>evento</b> del mismo nombre, <c>MutexAcl.Create</c> lanza
///   <see cref="WaitHandleCannotBeOpenedException"/>, que LHM no captura: <c>Computer.Open()</c>
///   falla siempre;
/// - con un <b>mutex que niega el acceso a SYSTEM</b>, LHM sigue sin él (sin turnarse con
///   nadie) y nuestro <c>OpenExisting</c> lanza <see cref="UnauthorizedAccessException"/>.
///
/// Si el servicio crea los nombres primero (arranca con Windows, antes que cualquier
/// sesión de usuario) y no los suelta, ya no hay ventana. Si alguno ya lo tiene otro de
/// forma hostil, se dice y no se lee nada (mejor «sin dato» que un dato falso).
///
/// Los nombres se inyectan para los tests (allí van en <c>Local\</c>).
/// </remarks>
public sealed class MutexGuard : IDisposable
{
    /// <summary>Los cinco de LHM 0.9.6 (Hardware/Mutexes.cs).</summary>
    public static IReadOnlyList<string> LhmNames { get; } =
    [
        @"Global\Access_ISABUS.HTP.Method",
        @"Global\Access_PCI",
        @"Global\Access_EC",
        @"Global\RazerReadWriteGuardMutex",
        @"Global\Access_USB_Sensors",
    ];

    public const string PciName = @"Global\Access_PCI";

    private readonly List<Mutex> _held = [];

    private MutexGuard()
    {
    }

    /// <summary>Los nombres que tiene otro proceso con un objeto que no podemos usar
    /// (un evento, o un mutex que nos niega el acceso). Vacío si todo está bien.</summary>
    public IReadOnlyList<string> Hostile { get; private set; } = [];

    /// <summary>El mutex del bus PCI, si está entre los nombres y lo tenemos.</summary>
    public Mutex? Pci { get; private set; }

    /// <summary>Crea o abre cada nombre con la misma DACL que LHM (control total para
    /// «Todos», para que otros programas que usan LHM, como FanControl, se sigan turnando
    /// con nosotros). Nunca lanza por un nombre ocupado: lo apunta en <see cref="Hostile"/>.</summary>
    public static MutexGuard Acquire(IReadOnlyList<string>? names = null, string pciName = PciName)
    {
        names ??= LhmNames;
        var guard = new MutexGuard();
        var hostile = new List<string>();
        var security = new MutexSecurity();
        security.AddAccessRule(new MutexAccessRule(new SecurityIdentifier(WellKnownSidType.WorldSid, null), MutexRights.FullControl, AccessControlType.Allow));
        foreach (string name in names)
        {
            try
            {
                Mutex mutex = MutexAcl.Create(false, name, out _, security);
                guard._held.Add(mutex);
                if (string.Equals(name, pciName, StringComparison.Ordinal))
                    guard.Pci = mutex;
            }
            catch (Exception e) when (e is WaitHandleCannotBeOpenedException or UnauthorizedAccessException or IOException)
            {
                hostile.Add(name);
            }
        }

        guard.Hostile = hostile;
        return guard;
    }

    public void Dispose()
    {
        foreach (Mutex mutex in _held)
            mutex.Dispose();
        _held.Clear();
        Pci = null;
    }
}
