using System.Diagnostics;

namespace LuzeiraSensores;

/// <summary>
/// Registro del servicio: <b>solo el Visor de eventos</b> (registro «Aplicación», origen
/// «LuzeiraSensores»), nunca ficheros (ADR-008, punto 3). El origen lo registra el
/// instalador.
/// </summary>
/// <remarks>
/// Cada aviso va una sola vez por motivo (como <c>_first_time</c> en modulo.py): el
/// servicio da una vuelta cada 1,5 s y no debe llenar el registro. Los mensajes no llevan
/// datos personales: de una excepción solo se apunta su tipo.
/// </remarks>
internal sealed class ServiceLog
{
    public const string Source = "LuzeiraSensores";

    private readonly HashSet<string> _said = new(StringComparer.Ordinal);

    public void Info(int id, string message) => Write(id, message, EventLogEntryType.Information);

    public void Warning(int id, string message) => Write(id, message, EventLogEntryType.Warning);

    public void Error(int id, string message) => Write(id, message, EventLogEntryType.Error);

    /// <summary>Lo dice solo la primera vez que aparece <paramref name="key"/>.</summary>
    public void Once(string key, int id, string message, EventLogEntryType type = EventLogEntryType.Warning)
    {
        if (_said.Add(key))
            Write(id, message, type);
    }

    /// <summary>Olvida un motivo, para que vuelva a decirse si reaparece (p. ej. PawnIO
    /// desaparece, vuelve y desaparece otra vez).</summary>
    public void Forget(string key) => _said.Remove(key);

    private static void Write(int id, string message, EventLogEntryType type)
    {
        try
        {
            EventLog.WriteEntry(Source, message, type, id);
        }
        catch (Exception e) when (e is InvalidOperationException or System.ComponentModel.Win32Exception or System.Security.SecurityException or ArgumentException)
        {
            // Sin registro no hay a dónde avisar; el servicio sigue.
        }
    }
}
