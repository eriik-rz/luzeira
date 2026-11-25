namespace LuzeiraSensores.Core;

/// <summary>
/// Formato del fichero <c>%ProgramData%\Luzeira Sensores\sensores.bin</c>, versión 1.
/// Es EXACTAMENTE el que lee Luzeira en <c>src/luzeira/sensores/modulo.py</c>
/// (<c>HEADER = struct.Struct("&lt;8sIIIIQq16sII")</c> y <c>ENTRY = struct.Struct("&lt;32sdII")</c>);
/// el test cruzado <c>tests/test_modulo_servicio.py</c> lo comprueba con los dos programas.
/// </summary>
/// <remarks>
/// Todo en little-endian (el orden de bytes de x86/x64).
///
/// Cabecera, 64 bytes:
/// <code>
///  0  8 B  magia "LUZSENS\0"
///  8  u32  versión del formato (1)
/// 12  u32  tamaño de la cabecera (64)
/// 16  u32  tamaño de cada entrada (48)
/// 20  u32  número de entradas (como mucho 64)
/// 24  u64  secuencia (seqlock): impar mientras el servicio escribe
/// 32  i64  hora de la última escritura, ms desde 1970 (solo informativa)
/// 40  16 B versión del módulo, ASCII rellena con ceros
/// 56  u32  estado (ver ModuleStatus)
/// 60  u32  reservado (0)
/// </code>
/// Cada entrada, 48 bytes: identificador ASCII de 32 bytes relleno con ceros, valor
/// float64, u32 de banderas (bit 0: el valor es válido) y 4 bytes reservados.
/// </remarks>
public static class Layout
{
    /// <summary>Los 8 bytes del principio: «LUZSENS» y un cero.</summary>
    public static ReadOnlySpan<byte> Magic => "LUZSENS\0"u8;

    public const uint FormatVersion = 1;
    public const int HeaderSize = 64;
    public const int EntrySize = 48;
    public const int MaxEntries = 64;

    /// <summary>Tamaño del fichero: cabecera y el máximo de entradas (3136 bytes), el
    /// <c>SECTION_SIZE</c> de modulo.py.</summary>
    public const int SectionSize = HeaderSize + MaxEntries * EntrySize;

    // Cabecera
    public const int MagicOffset = 0;
    public const int VersionOffset = 8;
    public const int HeaderSizeOffset = 12;
    public const int EntrySizeOffset = 16;
    public const int CountOffset = 20;
    public const int SequenceOffset = 24;
    public const int SequenceSize = 8;
    public const int TimestampOffset = 32;
    public const int ModuleVersionOffset = 40;
    public const int ModuleVersionSize = 16;
    public const int StatusOffset = 56;
    public const int HeaderReservedOffset = 60;

    // Entrada (relativo al principio de la entrada)
    public const int IdOffset = 0;
    public const int IdSize = 32;
    public const int ValueOffset = 32;
    public const int FlagsOffset = 40;
    public const int EntryReservedOffset = 44;

    /// <summary>Bit 0 de las banderas: el valor es válido.</summary>
    public const uint FlagValid = 1;
}

/// <summary>Estado del módulo (campo de la cabecera en el offset 56). Los números son los
/// <c>STATUS_*</c> de modulo.py y no se pueden cambiar sin subir la versión del formato.</summary>
public enum ModuleStatus : uint
{
    /// <summary>Todo bien: las entradas llevan lecturas.</summary>
    Ok = 0,

    /// <summary>No se puede abrir el driver PawnIO (no está instalado o no arranca).</summary>
    NoPawnIo = 1,

    /// <summary>LibreHardwareMonitor no da temperatura para este procesador.</summary>
    UnsupportedCpu = 2,

    /// <summary>El servicio acaba de arrancar y aún no tiene lecturas.</summary>
    Starting = 3,
}

/// <summary>La lista cerrada de identificadores: la misma que <c>SENSORS</c> de
/// modulo.py. Luzeira ignora cualquier otro.</summary>
public static class SensorIds
{
    public const string CpuTemperature = "cpu.temperature";
    public const string CpuTemperatureCcd1 = "cpu.temperature.ccd1";
    public const string CpuTemperatureCcd2 = "cpu.temperature.ccd2";
    public const string CpuPower = "cpu.power";
    public const string CpuVoltage = "cpu.voltage";

    /// <summary>Todos, en el orden en que se escriben en el fichero.</summary>
    public static IReadOnlyList<string> All { get; } =
        [CpuTemperature, CpuTemperatureCcd1, CpuTemperatureCcd2, CpuPower, CpuVoltage];
}
