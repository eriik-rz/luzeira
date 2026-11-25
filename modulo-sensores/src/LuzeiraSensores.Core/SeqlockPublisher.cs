using System.Buffers.Binary;

namespace LuzeiraSensores.Core;

/// <summary>
/// Dónde se escriben los bytes: la vista del fichero mapeado en el servicio, o una
/// memoria de prueba en los tests. A propósito <b>no tiene ningún método para leer</b>:
/// el servicio nunca lee lo que hay en la memoria compartida (ADR-008, punto 1), así que
/// nada de lo que ponga otro proceso ahí puede cambiar lo que hace.
/// </summary>
public interface ISectionTarget
{
    /// <summary>Copia <paramref name="data"/> a partir de <paramref name="offset"/>.</summary>
    void Write(int offset, ReadOnlySpan<byte> data);

    /// <summary>Barrera de memoria: lo escrito antes se ve antes que lo escrito después.</summary>
    void Barrier();
}

/// <summary>
/// Publica en el fichero con un <b>seqlock</b>.
/// </summary>
/// <remarks>
/// Concepto: el lector (Luzeira) y el escritor (el servicio) comparten memoria sin
/// cerrojos. Para que el lector no coja datos a medio escribir, el escritor:
/// 1. pone la secuencia en un número <b>impar</b> («estoy escribiendo»);
/// 2. escribe todo lo demás;
/// 3. pone la secuencia en el <b>par</b> siguiente («ya está»).
/// El lector copia y mira la secuencia antes y después: si es impar o ha cambiado,
/// descarta la copia (<c>parse_section</c> rechaza la impar; <c>read()</c> repite la copia).
///
/// La secuencia vive en <see cref="_sequence"/>, una variable de este proceso, y nunca
/// se lee de la memoria compartida: si otro proceso pudiera escribir en ella (no puede:
/// el fichero se abre sin compartir escritura), no cambiaría lo que publicamos.
///
/// Empieza en un número par sacado de la hora, para que tras reiniciar el servicio
/// la secuencia siga siendo distinta de la última que vio el lector.
/// </remarks>
public sealed class SeqlockPublisher
{
    private readonly ISectionTarget _target;
    private ulong _sequence;

    public SeqlockPublisher(ISectionTarget target, ulong initialSequence)
    {
        _target = target ?? throw new ArgumentNullException(nameof(target));
        _sequence = initialSequence & ~1UL; // par
    }

    /// <summary>La última secuencia publicada (par).</summary>
    public ulong Sequence => _sequence;

    /// <summary>Escribe un fichero completo. <paramref name="build"/> recibe la secuencia
    /// par que llevará y devuelve los <see cref="Layout.SectionSize"/> bytes.</summary>
    public void Publish(Func<ulong, byte[]> build)
    {
        ArgumentNullException.ThrowIfNull(build);
        ulong writing = _sequence + 1;  // impar
        ulong done = _sequence + 2;     // par
        byte[] image = build(done);
        if (image.Length != Layout.SectionSize)
            throw new ArgumentException("El fichero no mide lo que debe.", nameof(build));

        Span<byte> number = stackalloc byte[Layout.SequenceSize];

        BinaryPrimitives.WriteUInt64LittleEndian(number, writing);
        _target.Write(Layout.SequenceOffset, number);
        _target.Barrier();

        ReadOnlySpan<byte> all = image;
        _target.Write(0, all[..Layout.SequenceOffset]);
        int afterSequence = Layout.SequenceOffset + Layout.SequenceSize;
        _target.Write(afterSequence, all[afterSequence..]);
        _target.Barrier();

        BinaryPrimitives.WriteUInt64LittleEndian(number, done);
        _target.Write(Layout.SequenceOffset, number);
        _target.Barrier();

        _sequence = done;
    }
}
