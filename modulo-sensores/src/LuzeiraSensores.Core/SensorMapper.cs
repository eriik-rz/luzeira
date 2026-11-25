namespace LuzeiraSensores.Core;

/// <summary>El tipo de un sensor, como lo da LibreHardwareMonitor (<c>SensorType</c>);
/// solo los que nos interesan.</summary>
public enum SensorKind
{
    Other,
    Temperature,
    Power,
    Voltage,
}

/// <summary>Un sensor tal como lo da LibreHardwareMonitor: tipo, nombre y valor
/// (<c>null</c> si en este ciclo no lo ha leído).</summary>
public sealed record SensorNode(SensorKind Kind, string Name, float? Value);

/// <summary>Un aparato (<c>IHardware</c> de LibreHardwareMonitor) con sus sensores y sus
/// sub-aparatos. Es una copia propia, no el tipo de LHM, para poder probar el mapeo con
/// árboles inventados sin hardware ni driver.</summary>
public sealed record HardwareNode(bool IsCpu, IReadOnlyList<SensorNode> Sensors, IReadOnlyList<HardwareNode> SubHardware);

/// <summary>Lo que sale del mapeo de un ciclo.</summary>
/// <param name="Values">Valor de cada identificador de <see cref="SensorIds.All"/>
/// (<c>null</c> si no hay dato fiable en este ciclo).</param>
/// <param name="HasTemperatureSensor">Si el procesador tiene alguno de los sensores de
/// temperatura que sabemos leer (aunque en este ciclo no traiga valor). Si nunca lo
/// tiene, el procesador no es compatible.</param>
public sealed record MappedReadings(IReadOnlyDictionary<string, double?> Values, bool HasTemperatureSensor)
{
    /// <summary>Las entradas del fichero, siempre las cinco y en el mismo orden.</summary>
    public IReadOnlyList<Entry> ToEntries() =>
        SensorIds.All.Select(id => new Entry(id, Values.TryGetValue(id, out double? v) ? v : null)).ToList();
}

/// <summary>
/// Traduce los sensores de LibreHardwareMonitor 0.9.6 a nuestros identificadores.
/// </summary>
/// <remarks>
/// De dónde salen los nombres (código de LHM en la etiqueta v0.9.6, commit
/// 3d331e3370efb858411f19511373eff65a218701, leído con <c>gh api</c> el 2026-11-25):
///
/// AMD Zen (familias 17h, 19h y 1Ah, clase <c>Amd17Cpu</c>; el Ryzen 7 5700X3D es la
/// 19h modelo 0x21):
/// - <c>cpu.temperature</c>: «Core (Tdie)» si existe, si no «Core (Tctl/Tdie)».
///   LHM lee THM_TCON_CUR_TMP por SMN. Solo en los procesadores con desfase (1600X,
///   1700X, 1800X, 2700X y Threadripper 1000/2000) crea «Core (Tctl)» y «Core (Tdie)»
///   por separado; Tdie es la temperatura real del chip (Tctl lleva sumado el desfase
///   para el control de los ventiladores), así que se prefiere. En los demás (Zen 2 en
///   adelante) solo existe «Core (Tctl/Tdie)», que es la misma.
/// - <c>cpu.temperature.ccd1</c>/<c>ccd2</c>: «CCD1 (Tdie)»/«CCD2 (Tdie)». LHM solo las crea
///   en los modelos 0x31, 0x71, 0x21, 0x61 y 0x44, y solo si el CCD da una lectura entre
///   0 y 125 °C (un 5700X3D tiene un solo CCD: solo saldrá la ccd1).
/// - <c>cpu.power</c>: «Package» (potencia, contador de energía MSRC001_029B). En el
///   primer ciclo no tiene valor (hace falta una diferencia entre dos lecturas).
/// - <c>cpu.voltage</c>: «Core (SVI2 TFN)» (telemetría SVI2 por SMN). LHM no lo activa en
///   Ryzen 7000/9000 (modelos 0x61 y 0x44): allí no habrá tensión.
///
/// Intel (clase <c>IntelCpu</c>; no se puede probar aquí: beta):
/// - <c>cpu.temperature</c>: «CPU Package» (IA32_PACKAGE_THERM_STATUS) y, si el procesador no
///   tiene sensor de paquete, «Core Max» (la mayor de los núcleos).
/// - <c>cpu.power</c>: «CPU Package» de tipo potencia (ojo: el mismo nombre que la
///   temperatura; por eso se compara también el tipo).
/// - <c>cpu.voltage</c>: «CPU Core» de tipo tensión.
/// - No hay CCD en Intel.
///
/// Dos reglas más, por cómo se comporta LHM sin driver:
/// - Si el módulo de PawnIO no carga, LHM no falla: lee ceros. En AMD eso da 0 °C de
///   temperatura y 1,55 V de tensión (1,550 − 0,00625 · 0). Por eso una temperatura de
///   0 °C o menos se descarta, y <b>si no hay temperatura válida no se publica nada</b>
///   de ese ciclo: potencia y tensión vienen por el mismo camino.
/// - Solo se mira el primer procesador (equipos de un zócalo).
/// </remarks>
public static class SensorMapper
{
    private static readonly (SensorKind Kind, string Name)[] TemperatureCandidates =
    [
        (SensorKind.Temperature, "Core (Tdie)"),
        (SensorKind.Temperature, "Core (Tctl/Tdie)"),
        (SensorKind.Temperature, "CPU Package"),
        (SensorKind.Temperature, "Core Max"),
    ];

    private static readonly Dictionary<string, (SensorKind Kind, string Name)[]> Others = new(StringComparer.Ordinal)
    {
        [SensorIds.CpuTemperatureCcd1] = [(SensorKind.Temperature, "CCD1 (Tdie)")],
        [SensorIds.CpuTemperatureCcd2] = [(SensorKind.Temperature, "CCD2 (Tdie)")],
        [SensorIds.CpuPower] = [(SensorKind.Power, "Package"), (SensorKind.Power, "CPU Package")],
        [SensorIds.CpuVoltage] = [(SensorKind.Voltage, "Core (SVI2 TFN)"), (SensorKind.Voltage, "CPU Core")],
    };

    /// <summary>Mapea el árbol de un ciclo.</summary>
    public static MappedReadings Map(IEnumerable<HardwareNode> roots)
    {
        ArgumentNullException.ThrowIfNull(roots);
        var values = SensorIds.All.ToDictionary(id => id, _ => (double?)null, StringComparer.Ordinal);

        HardwareNode? cpu = roots.FirstOrDefault(h => h.IsCpu);
        if (cpu is null)
            return new MappedReadings(values, HasTemperatureSensor: false);

        List<SensorNode> sensors = Flatten(cpu).ToList();
        bool hasTemperature = TemperatureCandidates.Any(c => Find(sensors, c) is not null);

        double? temperature = FirstValue(sensors, TemperatureCandidates);
        if (temperature is not double t || t <= 0)
            return new MappedReadings(values, hasTemperature);  // nada fiable este ciclo

        values[SensorIds.CpuTemperature] = t;
        foreach ((string id, (SensorKind, string)[] candidates) in Others)
        {
            double? value = FirstValue(sensors, candidates);
            if (id.StartsWith(SensorIds.CpuTemperature, StringComparison.Ordinal) && value <= 0)
                value = null;
            values[id] = value;
        }

        return new MappedReadings(values, hasTemperature);
    }

    private static IEnumerable<SensorNode> Flatten(HardwareNode hardware) =>
        hardware.Sensors.Concat(hardware.SubHardware.SelectMany(Flatten));

    private static SensorNode? Find(List<SensorNode> sensors, (SensorKind Kind, string Name) candidate) =>
        sensors.FirstOrDefault(s => s.Kind == candidate.Kind && string.Equals(s.Name, candidate.Name, StringComparison.Ordinal));

    /// <summary>El primer candidato que existe y trae un número finito.</summary>
    private static double? FirstValue(List<SensorNode> sensors, (SensorKind Kind, string Name)[] candidates)
    {
        foreach ((SensorKind Kind, string Name) candidate in candidates)
        {
            if (Find(sensors, candidate)?.Value is float value && float.IsFinite(value))
                return value;
        }

        return null;
    }
}
