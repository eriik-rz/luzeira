namespace LuzeiraSensores.Core;

/// <summary>
/// Decide el estado que se publica en cada ciclo (función de lo visto hasta ahora; sin
/// relojes ni hardware, para poder probarla).
/// </summary>
/// <remarks>
/// - Sin PawnIO → <see cref="ModuleStatus.NoPawnIo"/>, siempre.
/// - Con PawnIO, mientras el procesador no haya enseñado ningún sensor de temperatura
///   conocido → <see cref="ModuleStatus.Starting"/> durante los primeros
///   <see cref="StartupCycles"/> ciclos (LHM solo activa el sensor tras su primera
///   lectura buena) y luego <see cref="ModuleStatus.UnsupportedCpu"/>.
/// - En cuanto lo ha enseñado una vez → <see cref="ModuleStatus.Ok"/> (aunque un ciclo
///   suelto no traiga valor: entonces van las entradas sin la bandera de válido).
/// </remarks>
public sealed class StatusTracker
{
    /// <summary>Ciclos de margen antes de dar el procesador por no compatible (con un
    /// ciclo de 1,5 s, unos 15 s).</summary>
    public const int StartupCycles = 10;

    private int _cyclesWithPawnIo;
    private bool _seenTemperature;

    /// <summary>Si ya se ha visto alguna vez un sensor de temperatura.</summary>
    public bool SeenTemperature => _seenTemperature;

    /// <summary>Apunta un ciclo y devuelve el estado que toca publicar.</summary>
    /// <param name="pawnIoAvailable">Si el driver se pudo abrir.</param>
    /// <param name="readings">El mapeo de este ciclo, o <c>null</c> si este ciclo no se
    /// pudo leer (LHM falló u otro programa tenía el bus). Un ciclo sin leer no cuenta
    /// para dar el procesador por no compatible: si no, bastaría con quedarse el bus
    /// para que Luzeira dijera algo falso.</param>
    public ModuleStatus Next(bool pawnIoAvailable, MappedReadings? readings)
    {
        if (!pawnIoAvailable)
        {
            _cyclesWithPawnIo = 0;
            return ModuleStatus.NoPawnIo;
        }

        if (readings is not null)
            _cyclesWithPawnIo++;
        if (readings?.HasTemperatureSensor == true)
            _seenTemperature = true;
        if (_seenTemperature)
            return ModuleStatus.Ok;
        return _cyclesWithPawnIo > StartupCycles ? ModuleStatus.UnsupportedCpu : ModuleStatus.Starting;
    }
}
