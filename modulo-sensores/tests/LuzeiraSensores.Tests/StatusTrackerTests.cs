using LuzeiraSensores.Core;

namespace LuzeiraSensores.Tests;

/// <summary>El estado que se publica.</summary>
public class StatusTrackerTests
{
    private static readonly MappedReadings WithSensor = SensorMapper.Map(
        [new HardwareNode(true, [new SensorNode(SensorKind.Temperature, "Core (Tctl/Tdie)", 40f)], [])]);

    private static readonly MappedReadings WithoutSensor = SensorMapper.Map(
        [new HardwareNode(true, [new SensorNode(SensorKind.Other, "Bus Speed", 100f)], [])]);

    [Fact]
    public void No_pawnio()
    {
        var tracker = new StatusTracker();
        Assert.Equal(ModuleStatus.NoPawnIo, tracker.Next(false, null));
        Assert.Equal(ModuleStatus.NoPawnIo, tracker.Next(false, WithSensor));
    }

    [Fact]
    public void Starting_then_ok()
    {
        var tracker = new StatusTracker();
        Assert.Equal(ModuleStatus.Starting, tracker.Next(true, WithoutSensor));
        Assert.Equal(ModuleStatus.Ok, tracker.Next(true, WithSensor));
        // Una vez visto, un ciclo suelto sin sensor sigue siendo «bien» (sin valores).
        Assert.Equal(ModuleStatus.Ok, tracker.Next(true, WithoutSensor));
        Assert.Equal(ModuleStatus.Ok, tracker.Next(true, null));
    }

    [Fact]
    public void Unsupported_after_the_startup_margin()
    {
        var tracker = new StatusTracker();
        for (int i = 0; i < StatusTracker.StartupCycles; i++)
            Assert.Equal(ModuleStatus.Starting, tracker.Next(true, WithoutSensor));
        Assert.Equal(ModuleStatus.UnsupportedCpu, tracker.Next(true, WithoutSensor));
    }

    [Fact]
    public void Cycles_that_could_not_be_read_do_not_count()
    {
        // Otro programa se queda el bus: eso no convierte el procesador en «no compatible».
        var tracker = new StatusTracker();
        for (int i = 0; i < 100; i++)
            Assert.Equal(ModuleStatus.Starting, tracker.Next(true, null));
    }

    [Fact]
    public void Losing_pawnio_restarts_the_margin()
    {
        var tracker = new StatusTracker();
        for (int i = 0; i < StatusTracker.StartupCycles; i++)
            tracker.Next(true, WithoutSensor);
        Assert.Equal(ModuleStatus.NoPawnIo, tracker.Next(false, null));
        Assert.Equal(ModuleStatus.Starting, tracker.Next(true, WithoutSensor));
    }
}
