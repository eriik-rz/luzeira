using System.Buffers.Binary;
using System.Text;

namespace LuzeiraSensores.Core;

/// <summary>Una lectura para el fichero: identificador de la lista cerrada y valor
/// (<c>null</c> si en este ciclo no hay dato).</summary>
public readonly record struct Entry(string Id, double? Value);

/// <summary>
/// Convierte un conjunto de lecturas en los bytes del fichero (función pura: no toca
/// ni el disco ni la memoria compartida, por eso se puede probar).
/// </summary>
public static class SectionWriter
{
    private static readonly HashSet<string> Allowed = new(SensorIds.All, StringComparer.Ordinal);

    /// <summary>
    /// Los <see cref="Layout.SectionSize"/> bytes del fichero, con la secuencia
    /// <paramref name="sequence"/> (par: la versión terminada).
    /// </summary>
    /// <exception cref="ArgumentException">Si algo no cabe en el formato: un identificador
    /// fuera de la lista, demasiadas entradas, una secuencia impar o una versión que no
    /// sea ASCII de como mucho 16 bytes. Es un fallo nuestro, no de datos de fuera.</exception>
    public static byte[] Build(
        ModuleStatus status,
        IReadOnlyList<Entry> entries,
        ulong sequence,
        long unixMilliseconds,
        string moduleVersion)
    {
        ArgumentNullException.ThrowIfNull(entries);
        ArgumentNullException.ThrowIfNull(moduleVersion);
        if (entries.Count > Layout.MaxEntries)
            throw new ArgumentException("Demasiadas entradas.", nameof(entries));
        if (sequence % 2 != 0)
            throw new ArgumentException("La secuencia publicada tiene que ser par.", nameof(sequence));

        var data = new byte[Layout.SectionSize];
        Span<byte> span = data;

        Layout.Magic.CopyTo(span[Layout.MagicOffset..]);
        BinaryPrimitives.WriteUInt32LittleEndian(span[Layout.VersionOffset..], Layout.FormatVersion);
        BinaryPrimitives.WriteUInt32LittleEndian(span[Layout.HeaderSizeOffset..], Layout.HeaderSize);
        BinaryPrimitives.WriteUInt32LittleEndian(span[Layout.EntrySizeOffset..], Layout.EntrySize);
        BinaryPrimitives.WriteUInt32LittleEndian(span[Layout.CountOffset..], (uint)entries.Count);
        BinaryPrimitives.WriteUInt64LittleEndian(span[Layout.SequenceOffset..], sequence);
        BinaryPrimitives.WriteInt64LittleEndian(span[Layout.TimestampOffset..], unixMilliseconds);
        WriteAscii(span.Slice(Layout.ModuleVersionOffset, Layout.ModuleVersionSize), moduleVersion, nameof(moduleVersion));
        BinaryPrimitives.WriteUInt32LittleEndian(span[Layout.StatusOffset..], (uint)status);
        // El reservado de la cabecera (offset 60) y el de cada entrada se quedan a cero.

        for (int i = 0; i < entries.Count; i++)
        {
            Entry entry = entries[i];
            if (!Allowed.Contains(entry.Id))
                throw new ArgumentException($"Identificador fuera de la lista: {entry.Id}", nameof(entries));
            Span<byte> slot = span.Slice(Layout.HeaderSize + i * Layout.EntrySize, Layout.EntrySize);
            WriteAscii(slot.Slice(Layout.IdOffset, Layout.IdSize - 1), entry.Id, nameof(entries));
            bool valid = entry.Value is double v && double.IsFinite(v);
            BinaryPrimitives.WriteDoubleLittleEndian(slot[Layout.ValueOffset..], valid ? entry.Value!.Value : 0.0);
            BinaryPrimitives.WriteUInt32LittleEndian(slot[Layout.FlagsOffset..], valid ? Layout.FlagValid : 0u);
        }

        return data;
    }

    /// <summary>Copia un texto ASCII imprimible en <paramref name="target"/>; lo que sobra
    /// ya está a cero. El hueco del identificador es de 31 bytes para que siempre quede
    /// al menos un cero detrás.</summary>
    private static void WriteAscii(Span<byte> target, string text, string paramName)
    {
        if (text.Length > target.Length || text.Any(c => c < 0x20 || c > 0x7E))
            throw new ArgumentException($"No es ASCII imprimible de como mucho {target.Length} bytes.", paramName);
        Encoding.ASCII.GetBytes(text, target);
    }
}
