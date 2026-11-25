using System.Buffers.Binary;
using System.Text;
using LuzeiraSensores.Core;

namespace LuzeiraSensores.Tests;

/// <summary>El escritor del formato (a): offsets y valores byte a byte, con los números
/// del docstring de modulo.py escritos a mano (no con las constantes de Layout, para que
/// un cambio en Layout no se valide a sí mismo).</summary>
public class SectionWriterTests
{
    private static byte[] Sample(ModuleStatus status = ModuleStatus.Ok, ulong sequence = 8) =>
        SectionWriter.Build(
            status,
            [new(SensorIds.CpuTemperature, 51.5), new(SensorIds.CpuPower, null)],
            sequence,
            unixMilliseconds: 0x0102030405060708,
            moduleVersion: "0.1.0");

    [Fact]
    public void Size_is_3136_bytes()
    {
        Assert.Equal(3136, Layout.SectionSize);
        Assert.Equal(3136, Sample().Length);
    }

    [Fact]
    public void Header_fields_are_at_their_offsets()
    {
        byte[] data = Sample(ModuleStatus.NoPawnIo, sequence: 0x10);
        Assert.Equal("LUZSENS\0"u8.ToArray(), data[0..8]);
        Assert.Equal(1u, BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(8)));
        Assert.Equal(64u, BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(12)));
        Assert.Equal(48u, BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(16)));
        Assert.Equal(2u, BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(20)));
        Assert.Equal(0x10UL, BinaryPrimitives.ReadUInt64LittleEndian(data.AsSpan(24)));
        Assert.Equal(0x0102030405060708L, BinaryPrimitives.ReadInt64LittleEndian(data.AsSpan(32)));
        Assert.Equal("0.1.0\0\0\0\0\0\0\0\0\0\0\0", Encoding.ASCII.GetString(data, 40, 16));
        Assert.Equal(1u, BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(56)));
        Assert.Equal(0u, BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(60)));
    }

    [Fact]
    public void Little_endian_on_the_wire()
    {
        byte[] data = Sample(sequence: 0x0A);
        Assert.Equal(0x0A, data[24]);
        Assert.Equal(0x00, data[31]);
        Assert.Equal(48, data[16]);
    }

    [Fact]
    public void Entries_have_id_value_and_flags()
    {
        byte[] data = Sample();
        Assert.Equal("cpu.temperature", Encoding.ASCII.GetString(data, 64, 15));
        Assert.All(data[(64 + 15)..(64 + 32)], b => Assert.Equal(0, b));
        Assert.Equal(51.5, BinaryPrimitives.ReadDoubleLittleEndian(data.AsSpan(64 + 32)));
        Assert.Equal(1u, BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(64 + 40)));
        Assert.Equal(0u, BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(64 + 44)));

        // Segunda entrada, sin valor: banderas a 0 y valor a 0.
        Assert.Equal("cpu.power", Encoding.ASCII.GetString(data, 112, 9));
        Assert.Equal(0.0, BinaryPrimitives.ReadDoubleLittleEndian(data.AsSpan(112 + 32)));
        Assert.Equal(0u, BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(112 + 40)));

        // Lo que sobra, a cero.
        Assert.All(data[160..], b => Assert.Equal(0, b));
    }

    [Theory]
    [InlineData(double.NaN)]
    [InlineData(double.PositiveInfinity)]
    [InlineData(double.NegativeInfinity)]
    public void Not_finite_values_are_not_valid(double value)
    {
        byte[] data = SectionWriter.Build(ModuleStatus.Ok, [new(SensorIds.CpuVoltage, value)], 2, 0, "x");
        Assert.Equal(0u, BinaryPrimitives.ReadUInt32LittleEndian(data.AsSpan(64 + 40)));
        Assert.Equal(0.0, BinaryPrimitives.ReadDoubleLittleEndian(data.AsSpan(64 + 32)));
    }

    [Fact]
    public void Ids_outside_the_list_are_refused()
    {
        Assert.Throws<ArgumentException>(() =>
            SectionWriter.Build(ModuleStatus.Ok, [new("gpu.temperature", 1)], 2, 0, "x"));
    }

    [Fact]
    public void Odd_sequence_is_refused()
    {
        Assert.Throws<ArgumentException>(() => Sample(sequence: 3));
    }

    [Theory]
    [InlineData("0.1.0-una-version-larga")]
    [InlineData("versión")]
    [InlineData("a\nb")]
    public void Module_version_must_be_short_printable_ascii(string version)
    {
        Assert.Throws<ArgumentException>(() =>
            SectionWriter.Build(ModuleStatus.Ok, [], 2, 0, version));
    }

    [Fact]
    public void A_16_byte_version_fits()
    {
        byte[] data = SectionWriter.Build(ModuleStatus.Ok, [], 2, 0, "0123456789abcdef");
        Assert.Equal("0123456789abcdef", Encoding.ASCII.GetString(data, 40, 16));
        Assert.Equal(0, data[56]);
    }

    [Fact]
    public void No_more_than_64_entries()
    {
        Entry[] many = Enumerable.Repeat(new Entry(SensorIds.CpuPower, 1), 65).ToArray();
        Assert.Throws<ArgumentException>(() => SectionWriter.Build(ModuleStatus.Ok, many, 2, 0, "x"));
        Entry[] max = Enumerable.Repeat(new Entry(SensorIds.CpuPower, 1), 64).ToArray();
        Assert.Equal(3136, SectionWriter.Build(ModuleStatus.Ok, max, 2, 0, "x").Length);
    }

    [Fact]
    public void Status_numbers_match_modulo_py()
    {
        Assert.Equal(0u, (uint)ModuleStatus.Ok);
        Assert.Equal(1u, (uint)ModuleStatus.NoPawnIo);
        Assert.Equal(2u, (uint)ModuleStatus.UnsupportedCpu);
        Assert.Equal(3u, (uint)ModuleStatus.Starting);
    }

    [Fact]
    public void Ids_match_modulo_py()
    {
        Assert.Equal(
            ["cpu.temperature", "cpu.temperature.ccd1", "cpu.temperature.ccd2", "cpu.power", "cpu.voltage"],
            SensorIds.All);
    }
}
