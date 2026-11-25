using LuzeiraSensores.Core;

namespace LuzeiraSensores.Tests;

/// <summary>El mapeo de sensores de LHM a nuestros identificadores (c), con árboles
/// inventados con los nombres que crea LibreHardwareMonitor 0.9.6.</summary>
public class SensorMapperTests
{
    private static SensorNode T(string name, float? value) => new(SensorKind.Temperature, name, value);

    private static SensorNode P(string name, float? value) => new(SensorKind.Power, name, value);

    private static SensorNode V(string name, float? value) => new(SensorKind.Voltage, name, value);

    private static HardwareNode Cpu(params SensorNode[] sensors) => new(true, sensors, []);

    /// <summary>Lo que enseña LHM para un Ryzen 5000 de un CCD (como un 5700X3D),
    /// con sensores que no usamos alrededor.</summary>
    private static HardwareNode Ryzen5000() => Cpu(
        P("Package", 61.875f),
        P("Core #1 (SMU)", 5f),
        T("Core (Tctl/Tdie)", 47.25f),
        T("CCD1 (Tdie)", 45.5f),
        V("Core (SVI2 TFN)", 1.1875f),
        V("SoC (SVI2 TFN)", 1.05f),
        V("Core #1 VID", 1.3f),
        new SensorNode(SensorKind.Other, "Cores (Average)", 4100f));

    [Fact]
    public void Ryzen_5000_single_ccd()
    {
        MappedReadings r = SensorMapper.Map([Ryzen5000()]);
        Assert.True(r.HasTemperatureSensor);
        Assert.Equal(47.25, r.Values[SensorIds.CpuTemperature]);
        Assert.Equal(45.5, r.Values[SensorIds.CpuTemperatureCcd1]);
        Assert.Null(r.Values[SensorIds.CpuTemperatureCcd2]);
        Assert.Equal(61.875, r.Values[SensorIds.CpuPower]);
        Assert.Equal(1.1875, r.Values[SensorIds.CpuVoltage]);
    }

    [Fact]
    public void Two_ccds()
    {
        HardwareNode cpu = Cpu(T("Core (Tctl/Tdie)", 60f), T("CCD1 (Tdie)", 58f), T("CCD2 (Tdie)", 55f), T("CCDs Max (Tdie)", 58f));
        MappedReadings r = SensorMapper.Map([cpu]);
        Assert.Equal(58, r.Values[SensorIds.CpuTemperatureCcd1]);
        Assert.Equal(55, r.Values[SensorIds.CpuTemperatureCcd2]);
    }

    [Fact]
    public void Ryzen_with_offset_uses_tdie_not_tctl()
    {
        // 1800X: Tctl = Tdie + 20 (desfase para los ventiladores).
        MappedReadings r = SensorMapper.Map([Cpu(T("Core (Tctl)", 70f), T("Core (Tdie)", 50f))]);
        Assert.Equal(50, r.Values[SensorIds.CpuTemperature]);
    }

    [Fact]
    public void Intel_package_sensors()
    {
        HardwareNode cpu = Cpu(
            T("Core Max", 72f),
            T("Core Average", 60f),
            T("CPU Core #1", 70f),
            T("CPU Package", 71f),
            P("CPU Package", 88.5f),
            P("CPU Cores", 80f),
            V("CPU Core", 1.25f),
            V("CPU Core #1", 1.2f));
        MappedReadings r = SensorMapper.Map([cpu]);
        Assert.Equal(71, r.Values[SensorIds.CpuTemperature]);   // la de temperatura, no la de potencia
        Assert.Equal(88.5, r.Values[SensorIds.CpuPower]);
        Assert.Equal(1.25, r.Values[SensorIds.CpuVoltage]);
        Assert.Null(r.Values[SensorIds.CpuTemperatureCcd1]);
    }

    [Fact]
    public void Intel_without_package_sensor_uses_core_max()
    {
        MappedReadings r = SensorMapper.Map([Cpu(T("Core Max", 65f), T("CPU Core #1", 65f))]);
        Assert.Equal(65, r.Values[SensorIds.CpuTemperature]);
    }

    [Fact]
    public void Same_name_other_type_does_not_count()
    {
        // Solo una «CPU Package» de potencia: no es una temperatura.
        MappedReadings r = SensorMapper.Map([Cpu(P("CPU Package", 50f))]);
        Assert.False(r.HasTemperatureSensor);
        Assert.Null(r.Values[SensorIds.CpuTemperature]);
        Assert.Null(r.Values[SensorIds.CpuPower]);
    }

    [Fact]
    public void Without_driver_lhm_reads_zeros_and_nothing_is_published()
    {
        // Lo que da LHM 0.9.6 en un Ryzen con el módulo de PawnIO sin cargar: 0 °C y 1,55 V.
        HardwareNode cpu = Cpu(T("Core (Tctl/Tdie)", 0f), P("Package", 0f), V("Core (SVI2 TFN)", 1.55f));
        MappedReadings r = SensorMapper.Map([cpu]);
        Assert.True(r.HasTemperatureSensor);
        Assert.All(r.Values.Values, Assert.Null);
    }

    [Fact]
    public void Missing_value_this_cycle()
    {
        // El primer ciclo, la potencia no tiene valor (hace falta una diferencia).
        HardwareNode cpu = Cpu(T("Core (Tctl/Tdie)", 40f), P("Package", null));
        MappedReadings r = SensorMapper.Map([cpu]);
        Assert.Equal(40, r.Values[SensorIds.CpuTemperature]);
        Assert.Null(r.Values[SensorIds.CpuPower]);
    }

    [Fact]
    public void Temperature_sensor_without_value_still_counts_as_present()
    {
        MappedReadings r = SensorMapper.Map([Cpu(T("CPU Package", null))]);
        Assert.True(r.HasTemperatureSensor);
        Assert.All(r.Values.Values, Assert.Null);
    }

    [Fact]
    public void Not_finite_values_are_dropped()
    {
        MappedReadings r = SensorMapper.Map([Cpu(T("Core (Tctl/Tdie)", float.NaN), T("CPU Package", 50f))]);
        Assert.Equal(50, r.Values[SensorIds.CpuTemperature]);
        r = SensorMapper.Map([Cpu(T("Core (Tctl/Tdie)", 50f), P("Package", float.PositiveInfinity))]);
        Assert.Null(r.Values[SensorIds.CpuPower]);
    }

    [Fact]
    public void A_ccd_at_zero_is_dropped()
    {
        MappedReadings r = SensorMapper.Map([Cpu(T("Core (Tctl/Tdie)", 50f), T("CCD1 (Tdie)", 0f))]);
        Assert.Null(r.Values[SensorIds.CpuTemperatureCcd1]);
    }

    [Fact]
    public void Only_the_first_cpu_and_never_the_board()
    {
        var board = new HardwareNode(false, [T("CPU", 99f), T("Core (Tctl/Tdie)", 99f)], []);
        HardwareNode first = Cpu(T("Core (Tctl/Tdie)", 41f));
        HardwareNode second = Cpu(T("Core (Tctl/Tdie)", 42f));
        MappedReadings r = SensorMapper.Map([board, first, second]);
        Assert.Equal(41, r.Values[SensorIds.CpuTemperature]);
    }

    [Fact]
    public void Sub_hardware_of_the_cpu_is_searched()
    {
        var cpu = new HardwareNode(true, [], [new HardwareNode(false, [T("Core (Tctl/Tdie)", 44f)], [])]);
        Assert.Equal(44, SensorMapper.Map([cpu]).Values[SensorIds.CpuTemperature]);
    }

    [Fact]
    public void No_cpu_at_all()
    {
        MappedReadings r = SensorMapper.Map([]);
        Assert.False(r.HasTemperatureSensor);
        Assert.Equal(SensorIds.All.Count, r.ToEntries().Count);
        Assert.All(r.ToEntries(), e => Assert.Null(e.Value));
    }

    [Fact]
    public void Entries_always_five_in_order()
    {
        IReadOnlyList<Entry> entries = SensorMapper.Map([Ryzen5000()]).ToEntries();
        Assert.Equal(SensorIds.All, entries.Select(e => e.Id));
    }
}
