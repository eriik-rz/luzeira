using LibreHardwareMonitor.Hardware;
using LuzeiraSensores.Core;

namespace LuzeiraSensores;

/// <summary>
/// La parte que habla con LibreHardwareMonitorLib 0.9.6 (y, a través de ella, con PawnIO).
/// Solo la CPU. Nunca la placa, discos, memoria, GPU, controladores ni red (ADR-007,
/// punto 2). La placa (beta en el ADR-008, punto 5) queda fuera hasta que modulo.py
/// tenga identificadores para ella: activarla daría acceso a la Super I/O a cambio de
/// nada que Luzeira pueda mostrar (decisión del coordinador, 2026-11-25).
/// </summary>
internal sealed class LhmSensors : IDisposable
{
    /// <summary>El dispositivo que crea el driver PawnIO (lo abre igual LHM, en PawnIo.cs).</summary>
    private const string PawnIoDevice = @"\\?\GLOBALROOT\Device\PawnIO";

    private const int PciWaitMilliseconds = 500;

    private readonly Computer _computer;
    private readonly Mutex? _pciMutex;

    private LhmSensors(Computer computer, Mutex? pciMutex)
    {
        _computer = computer;
        _pciMutex = pciMutex;
    }

    /// <summary>
    /// Si el driver PawnIO está y se deja abrir. Sin él, LHM no falla: lee ceros (0 °C,
    /// 1,55 V), así que hay que mirarlo antes. Solo se abre y se cierra.
    /// </summary>
    public static bool PawnIoAvailable()
    {
        using var handle = Native.CreateFile(
            PawnIoDevice,
            Native.FileReadData | Native.FileWriteData, // lo mismo que pide LHM
            Native.FileShareRead | Native.FileShareWrite,
            IntPtr.Zero,
            Native.OpenExisting,
            0,
            IntPtr.Zero);
        return !handle.IsInvalid;
    }

    /// <summary>La versión de PawnIO instalada, según el registro de desinstalación (la
    /// lee LHM). Solo para el registro de eventos.</summary>
    public static Version? PawnIoVersion() => LibreHardwareMonitor.PawnIo.PawnIo.Version;

    /// <summary>Abre LHM. <paramref name="pciMutex"/> es el de <see cref="MutexGuard"/>
    /// (lo crea el servicio antes que LHM y es suyo: aquí no se cierra).</summary>
    public static LhmSensors Open(Mutex? pciMutex)
    {
        var computer = new Computer
        {
            IsCpuEnabled = true,
            IsMotherboardEnabled = false,
            IsMemoryEnabled = false,
            IsGpuEnabled = false,
            IsControllerEnabled = false,
            IsNetworkEnabled = false,
            IsStorageEnabled = false,
            IsBatteryEnabled = false,
            IsPsuEnabled = false,
            IsPowerMonitorEnabled = false,
        };
        try
        {
            computer.Open();
        }
        catch
        {
            // Sin esto, cada reintento (cada 30 s) dejaría abierto un Computer entero:
            // handles a PawnIO, su página ejecutable y sus mutex (revisión, MEDIA 1).
            computer.Close();
            throw;
        }

        return new LhmSensors(computer, pciMutex);
    }

    /// <summary>
    /// Lee un ciclo. Devuelve <c>null</c> si este ciclo no se pudo leer de forma fiable.
    /// </summary>
    /// <remarks>
    /// Por qué se toma aquí el mutex PCI: LHM lo pide con solo 10 ms de espera y, si no
    /// lo consigue, no lee la temperatura ni la tensión pero <b>deja el valor anterior</b>
    /// en la temperatura y calcula una tensión de 1,55 V con un registro a cero
    /// (Amd17Cpu.cs, v0.9.6). Ese mutex lo pueden tener otros programas (o cualquier
    /// usuario: LHM lo crea con control total para «Todos»). Tomándolo antes nosotros en
    /// este mismo hilo (los mutex de Windows son reentrantes para su dueño), la espera de
    /// LHM pasa seguro; y si no lo conseguimos en <see cref="PciWaitMilliseconds"/>, el
    /// ciclo se da por perdido en vez de publicar un dato viejo o falso.
    /// </remarks>
    public MappedReadings? Read()
    {
        bool held = false;
        if (_pciMutex is not null)
        {
            try
            {
                held = _pciMutex.WaitOne(PciWaitMilliseconds);
            }
            catch (AbandonedMutexException)
            {
                held = true; // el dueño murió sin soltarlo: ahora es nuestro
            }

            if (!held)
                return null;
        }

        try
        {
            var nodes = new List<HardwareNode>();
            foreach (IHardware hardware in _computer.Hardware)
            {
                if (hardware.HardwareType != HardwareType.Cpu)
                    continue;
                Update(hardware);
                nodes.Add(ToNode(hardware));
            }

            return SensorMapper.Map(nodes);
        }
        finally
        {
            if (held)
                _pciMutex!.ReleaseMutex();
        }
    }

    private static void Update(IHardware hardware)
    {
        hardware.Update();
        foreach (IHardware sub in hardware.SubHardware)
            Update(sub);
    }

    private static HardwareNode ToNode(IHardware hardware) => new(
        hardware.HardwareType == HardwareType.Cpu,
        hardware.Sensors.Select(s => new SensorNode(KindOf(s.SensorType), s.Name, s.Value)).ToList(),
        hardware.SubHardware.Select(ToNode).ToList());

    private static SensorKind KindOf(SensorType type) => type switch
    {
        SensorType.Temperature => SensorKind.Temperature,
        SensorType.Power => SensorKind.Power,
        SensorType.Voltage => SensorKind.Voltage,
        _ => SensorKind.Other,
    };

    public void Dispose()
    {
        _computer.Close();
    }
}
