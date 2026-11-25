using System.Buffers.Binary;
using LuzeiraSensores.Core;

namespace LuzeiraSensores.Tests;

/// <summary>El seqlock: orden de las escrituras y secuencia siempre local.</summary>
public class SeqlockPublisherTests
{
    /// <summary>Apunta cada escritura y deja ver la memoria como la vería el lector en
    /// cada momento.</summary>
    private sealed class RecordingTarget : ISectionTarget
    {
        public byte[] Memory { get; } = new byte[Layout.SectionSize];

        public List<(int Offset, int Length)> Writes { get; } = [];

        public List<byte[]> Snapshots { get; } = [];

        public int Barriers { get; private set; }

        /// <summary>Escrituras («W» + offset) y barreras («B»), en orden.</summary>
        public List<string> Events { get; } = [];

        public void Write(int offset, ReadOnlySpan<byte> data)
        {
            data.CopyTo(Memory.AsSpan(offset));
            Writes.Add((offset, data.Length));
            Events.Add("W" + offset);
            Snapshots.Add((byte[])Memory.Clone());
        }

        public void Barrier()
        {
            Barriers++;
            Events.Add("B");
        }
    }

    private static byte[] Image(ulong sequence, double temperature) =>
        SectionWriter.Build(ModuleStatus.Ok, [new(SensorIds.CpuTemperature, temperature)], sequence, 0, "t");

    private static ulong SequenceOf(byte[] memory) => BinaryPrimitives.ReadUInt64LittleEndian(memory.AsSpan(24));

    [Fact]
    public void Odd_first_then_body_then_even()
    {
        var target = new RecordingTarget();
        var publisher = new SeqlockPublisher(target, initialSequence: 100);
        publisher.Publish(seq => Image(seq, 40));

        Assert.Equal((24, 8), target.Writes[0]);
        Assert.Equal(101UL, SequenceOf(target.Snapshots[0]));
        Assert.Equal((24, 8), target.Writes[^1]);
        Assert.Equal(102UL, SequenceOf(target.Snapshots[^1]));
        // En medio, nadie toca la secuencia.
        foreach ((int offset, int length) in target.Writes[1..^1])
            Assert.True(offset + length <= 24 || offset >= 32, $"{offset}+{length}");
        Assert.True(target.Barriers >= 2);
        Assert.Equal(102UL, publisher.Sequence);
    }

    [Fact]
    public void A_barrier_between_each_step()
    {
        // M6: sin la barrera entre el cuerpo y la secuencia par, el procesador o el
        // compilador podrían adelantar el «ya está» al cuerpo.
        var target = new RecordingTarget();
        new SeqlockPublisher(target, 0).Publish(seq => Image(seq, 40));
        Assert.Equal(["W24", "B", "W0", "W32", "B", "W24", "B"], target.Events);
    }

    [Fact]
    public void While_the_sequence_is_even_the_memory_is_a_whole_version()
    {
        var target = new RecordingTarget();
        var publisher = new SeqlockPublisher(target, initialSequence: 0);
        publisher.Publish(seq => Image(seq, 40));
        byte[] first = (byte[])target.Memory.Clone();
        target.Snapshots.Clear();
        publisher.Publish(seq => Image(seq, 60));
        byte[] second = target.Memory;

        // Cualquier momento en que el lector vea una secuencia par tiene que ser la
        // versión anterior entera o la nueva entera.
        foreach (byte[] snapshot in target.Snapshots)
        {
            if (SequenceOf(snapshot) % 2 == 0)
                Assert.True(snapshot.AsSpan().SequenceEqual(first) || snapshot.AsSpan().SequenceEqual(second));
        }
    }

    [Fact]
    public void The_sequence_is_never_read_from_the_shared_memory()
    {
        // Otro proceso cambia la secuencia en la memoria: lo siguiente que publicamos
        // sigue la nuestra. (ISectionTarget ni siquiera tiene un método para leer.)
        var target = new RecordingTarget();
        var publisher = new SeqlockPublisher(target, initialSequence: 10);
        publisher.Publish(seq => Image(seq, 40));
        BinaryPrimitives.WriteUInt64LittleEndian(target.Memory.AsSpan(24), 999_999);
        publisher.Publish(seq => Image(seq, 41));
        Assert.Equal(14UL, SequenceOf(target.Memory));
        Assert.Equal(0, typeof(ISectionTarget).GetMethods().Count(m => m.Name.Contains("Read", StringComparison.Ordinal)));
    }

    [Fact]
    public void Initial_sequence_is_made_even()
    {
        var target = new RecordingTarget();
        var publisher = new SeqlockPublisher(target, initialSequence: 7);
        publisher.Publish(seq => Image(seq, 40));
        Assert.Equal(8UL, publisher.Sequence);
    }

    [Fact]
    public void A_wrong_size_image_is_refused_before_writing()
    {
        var target = new RecordingTarget();
        var publisher = new SeqlockPublisher(target, 0);
        Assert.Throws<ArgumentException>(() => publisher.Publish(_ => new byte[10]));
        Assert.Empty(target.Writes);
    }
}
