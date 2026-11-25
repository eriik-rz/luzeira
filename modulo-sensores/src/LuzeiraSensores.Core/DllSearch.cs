namespace LuzeiraSensores.Core;

/// <summary>Dónde busca DLL el proceso (ADR-007 y ADR-008, punto 2).</summary>
public static class DllSearch
{
    /// <summary>Solo System32 y la carpeta del programa, para todo el proceso (también lo
    /// que se cargue con LoadLibrary sin ruta). Devuelve <c>false</c> si Windows no lo
    /// acepta: entonces el servicio no debe seguir (Program.cs sale con código 1).</summary>
    public static bool Harden() =>
        Native.SetDefaultDllDirectories(Native.LoadLibrarySearchSystem32 | Native.LoadLibrarySearchApplicationDir);
}
