using System.Reflection;
using LuzeiraSensores.Core;

// Uso: EscribirMuestra <fichero de salida> [estado]
// Escribe una muestra con valores fijos (los comprueba tests/test_modulo_servicio.py):
// secuencia 42, versión del módulo = la de Directory.Build.props y estas lecturas.
// Con un estado distinto de 0 no lleva entradas (como el servicio).

if (args.Length is < 1 or > 2)
{
    Console.Error.WriteLine("Uso: EscribirMuestra <fichero> [estado]");
    return 2;
}

var status = args.Length == 2 ? (ModuleStatus)uint.Parse(args[1], System.Globalization.CultureInfo.InvariantCulture) : ModuleStatus.Ok;
Entry[] entries = status == ModuleStatus.Ok
    ?
    [
        new(SensorIds.CpuTemperature, 47.25),
        new(SensorIds.CpuTemperatureCcd1, 45.5),
        new(SensorIds.CpuTemperatureCcd2, null),  // sin dato: va sin la bandera de válido
        new(SensorIds.CpuPower, 61.875),
        new(SensorIds.CpuVoltage, 1.1875),
    ]
    : [];
string version = typeof(SectionWriter).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()!.InformationalVersion;

var target = new MemoryTarget();
var publisher = new SeqlockPublisher(target, initialSequence: 40);
publisher.Publish(sequence => SectionWriter.Build(status, entries, sequence, 1_764_000_000_000, version));
File.WriteAllBytes(args[0], target.Bytes);
return 0;

/// <summary>Una «memoria compartida» en un array, para escribir el fichero de una vez.</summary>
internal sealed class MemoryTarget : ISectionTarget
{
    public byte[] Bytes { get; } = new byte[Layout.SectionSize];

    public void Write(int offset, ReadOnlySpan<byte> data) => data.CopyTo(Bytes.AsSpan(offset));

    public void Barrier()
    {
    }
}
