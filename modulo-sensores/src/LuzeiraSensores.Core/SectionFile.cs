using System.ComponentModel;
using System.IO.MemoryMappedFiles;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace LuzeiraSensores.Core;

/// <summary>Por qué no se pudo abrir el fichero (cada uno va con su evento).</summary>
public enum SectionOpenFailure
{
    None,

    /// <summary>La carpeta no existe: la crea el instalador.</summary>
    Missing,

    /// <summary>Carpeta o fichero con otro propietario, permisos, tipo o enlaces.</summary>
    Untrusted,

    /// <summary>Otro proceso tiene abierto el fichero sin dejarnos escribir (error 32).
    /// Lo puede hacer un usuario normal (los usuarios pueden leer el fichero) mientras
    /// el servicio no lo tiene abierto: al arrancar o tras reiniciarse. No es una
    /// escalada (solo deja a Luzeira sin datos); se reintenta y se avisa.</summary>
    Busy,

    /// <summary>Cualquier otro error de Windows.</summary>
    Other,
}

/// <summary>
/// El fichero <c>sensores.bin</c> abierto y mapeado para escribir (ADR-008, punto 1).
/// </summary>
/// <remarks>
/// Orden, para que nadie cambie nada entre la comprobación y el uso:
/// 1. Se abre <b>la carpeta</b> sin seguir enlaces y <b>sin compartir el borrado</b>: mientras
///    el servicio la tiene abierta, nadie la puede renombrar ni cambiar por otra. Se
///    comprueban por su handle el tipo, que no sea un enlace, el propietario y la DACL.
///    La carpeta la crea el instalador; <b>el servicio nunca la crea</b>.
/// 2. Se abre (o se crea, con su DACL explícita) <b>el fichero</b>, sin seguir enlaces y
///    compartiendo solo la lectura: con el fichero abierto por el servicio, nadie más
///    lo puede abrir para escribir. Se comprueba igual, y que no tenga otros nombres
///    (enlaces duros).
/// 3. Se mapea <see cref="Layout.SectionSize"/> bytes. Nunca se lee lo que hay: solo se
///    escribe (ver <see cref="ISectionTarget"/>).
///
/// Si Luzeira tiene el fichero abierto de antes (lo comparte para leer y escribir, sin
/// borrar), abrirlo con OPEN_ALWAYS funciona igual: no se borra ni se trunca nunca.
/// </remarks>
internal sealed unsafe class SectionFile : ISectionTarget, IDisposable
{
    public const string FileName = "sensores.bin";

    private readonly SafeFileHandle _folder;
    private readonly SafeFileHandle _file;
    private readonly MemoryMappedFile _mapping;
    private readonly MemoryMappedViewAccessor _view;
    private readonly byte* _pointer;

    private SectionFile(SafeFileHandle folder, SafeFileHandle file, MemoryMappedFile mapping, MemoryMappedViewAccessor view)
    {
        _folder = folder;
        _file = file;
        _mapping = mapping;
        _view = view;
        byte* pointer = null;
        view.SafeMemoryMappedViewHandle.AcquirePointer(ref pointer);
        _pointer = pointer + view.PointerOffset;
    }

    /// <summary>La carpeta del módulo, preguntada a Windows (<c>SHGetKnownFolderPath</c>
    /// por debajo) y no a la variable de entorno <c>%ProgramData%</c>.</summary>
    public static string DefaultFolder() =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Luzeira Sensores");

    /// <summary>Abre y comprueba carpeta y fichero. Devuelve <c>null</c> y el motivo (sin
    /// datos personales: va al Visor de eventos) si no se debe escribir.</summary>
    /// <param name="folderPath">La carpeta (la de <see cref="DefaultFolder"/> en el servicio).</param>
    /// <param name="trust">Cómo se decide si un descriptor es de fiar. Siempre
    /// <see cref="FolderSecurity.Check"/>; solo los tests ponen otra, porque una carpeta
    /// temporal es del usuario y no de SYSTEM.</param>
    /// <param name="fileSddl">El descriptor con el que se crea el fichero. Siempre
    /// <see cref="FolderSecurity.FileSddl"/>; los tests quitan el propietario SYSTEM, que
    /// un usuario no puede poner.</param>
    public static SectionFile? TryOpen(string folderPath, out string? problem, Func<string, bool, TrustProblem>? trust = null, string? fileSddl = null) =>
        TryOpen(folderPath, out problem, out _, trust, fileSddl);

    /// <inheritdoc cref="TryOpen(string, out string?, Func{string, bool, TrustProblem}?, string?)"/>
    public static SectionFile? TryOpen(string folderPath, out string? problem, out SectionOpenFailure failure, Func<string, bool, TrustProblem>? trust = null, string? fileSddl = null)
    {
        problem = null;
        failure = SectionOpenFailure.None;
        trust ??= FolderSecurity.Check;
        fileSddl ??= FolderSecurity.FileSddl;
        SafeFileHandle folder = OpenFolder(folderPath);
        if (folder.IsInvalid)
        {
            int error = Marshal.GetLastPInvokeError();
            folder.Dispose();
            failure = error is Native.ErrorFileNotFound or Native.ErrorPathNotFound ? SectionOpenFailure.Missing : SectionOpenFailure.Other;
            problem = error is Native.ErrorFileNotFound or Native.ErrorPathNotFound
                ? "La carpeta del módulo no existe (la crea el instalador)."
                : $"No se pudo abrir la carpeta del módulo (error {error}).";
            return null;
        }

        SafeFileHandle? file = null;
        MemoryMappedFile? mapping = null;
        try
        {
            problem = CheckHandle(folder, isFolder: true, "la carpeta", trust);
            if (problem is not null)
            {
                failure = SectionOpenFailure.Untrusted;
                return null;
            }

            file = CreateFileWithAcl(Path.Combine(folderPath, FileName), fileSddl, out int createError);
            if (file.IsInvalid)
            {
                failure = createError == Native.ErrorSharingViolation ? SectionOpenFailure.Busy : SectionOpenFailure.Other;
                problem = failure == SectionOpenFailure.Busy
                    ? "Otro programa tiene abierto el fichero de sensores sin dejar escribir (error 32). Se reintenta."
                    : $"No se pudo abrir el fichero de sensores (error {createError}).";
                return null;
            }

            problem = CheckHandle(file, isFolder: false, "el fichero", trust);
            if (problem is not null)
            {
                failure = SectionOpenFailure.Untrusted;
                return null;
            }

            // Si mide menos, la capacidad lo agranda; si mide más (de otra versión), se
            // deja: Luzeira nunca lee más allá de SECTION_SIZE.
            long capacity = Math.Max(RandomAccess.GetLength(file), Layout.SectionSize);
            mapping = MemoryMappedFile.CreateFromFile(
                file, mapName: null, capacity, MemoryMappedFileAccess.ReadWrite, HandleInheritability.None, leaveOpen: true);
            MemoryMappedViewAccessor view = mapping.CreateViewAccessor(0, Layout.SectionSize, MemoryMappedFileAccess.ReadWrite);
            var result = new SectionFile(folder, file, mapping, view);
            file = null;
            mapping = null;
            folder = null!;
            return result;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or Win32Exception)
        {
            failure = SectionOpenFailure.Other;
            problem = $"No se pudo preparar el fichero de sensores ({e.GetType().Name}).";
            return null;
        }
        finally
        {
            mapping?.Dispose();
            file?.Dispose();
            folder?.Dispose();
        }
    }

    /// <summary>Abre la carpeta sin seguir enlaces y sin compartir el borrado: con este
    /// handle abierto, nadie puede renombrarla ni cambiarla por otra (ni en el rato entre
    /// comprobarla y abrir el fichero). Inválido si no se puede (ver el último error).</summary>
    /// <remarks>Ojo (medido el 2026-11-25): el modo de compartir solo cuenta si el handle
    /// pide acceso a datos. Con solo READ_CONTROL y FILE_READ_ATTRIBUTES, Windows deja
    /// renombrar la carpeta aunque no compartamos el borrado; por eso se pide además
    /// FILE_LIST_DIRECTORY (el mismo bit que FILE_READ_DATA).</remarks>
    internal static SafeFileHandle OpenFolder(string folderPath) => Native.CreateFile(
        folderPath,
        Native.ReadControl | Native.FileReadAttributes | Native.FileReadData,
        Native.FileShareRead | Native.FileShareWrite, // sin FILE_SHARE_DELETE, a propósito
        IntPtr.Zero,
        Native.OpenExisting,
        Native.FileFlagBackupSemantics | Native.FileFlagOpenReparsePoint,
        IntPtr.Zero);

    private static SafeFileHandle CreateFileWithAcl(string path, string sddl, out int error)
    {
        if (!Native.ConvertStringToSecurityDescriptor(sddl, Native.SddlRevision1, out IntPtr descriptor, IntPtr.Zero))
            throw new Win32Exception(Marshal.GetLastPInvokeError());
        try
        {
            var attributes = new Native.SecurityAttributes
            {
                Length = sizeof(Native.SecurityAttributes),
                SecurityDescriptor = descriptor,
                InheritHandle = 0,
            };
            SafeFileHandle handle = Native.CreateFile(
                path,
                Native.GenericRead | Native.GenericWrite,
                Native.FileShareRead, // nadie más puede escribir mientras lo tenemos
                (IntPtr)(&attributes),
                Native.OpenAlways,
                Native.FileAttributeNormal | Native.FileFlagOpenReparsePoint,
                IntPtr.Zero);
            error = handle.IsInvalid ? Marshal.GetLastPInvokeError() : 0;
            return handle;
        }
        finally
        {
            Native.LocalFree(descriptor);
        }
    }

    /// <summary>El motivo para no fiarse de un handle, o <c>null</c> si está bien.</summary>
    private static string? CheckHandle(SafeFileHandle handle, bool isFolder, string what, Func<string, bool, TrustProblem> trust)
    {
        if (!Native.GetFileInformationByHandle(handle, out Native.ByHandleFileInformation info))
            throw new Win32Exception(Marshal.GetLastPInvokeError());
        if ((info.FileAttributes & Native.FileAttributeReparsePoint) != 0)
            return $"No se escribe: {what} es un enlace.";
        if (((info.FileAttributes & Native.FileAttributeDirectory) != 0) != isFolder)
            return $"No se escribe: {what} no es del tipo esperado.";
        if (!isFolder && info.NumberOfLinks != 1)
            return $"No se escribe: {what} tiene otros nombres (enlaces duros).";
        TrustProblem problem = trust(Native.SddlOf(handle), isFolder);
        return problem == TrustProblem.None
            ? null
            : $"No se escribe: {what} no tiene el propietario o los permisos esperados ({problem}).";
    }

    public void Write(int offset, ReadOnlySpan<byte> data)
    {
        if (offset < 0 || offset + data.Length > Layout.SectionSize)
            throw new ArgumentOutOfRangeException(nameof(offset));
        data.CopyTo(new Span<byte>(_pointer + offset, data.Length));
    }

    public void Barrier() => Thread.MemoryBarrier();

    public void Dispose()
    {
        _view.SafeMemoryMappedViewHandle.ReleasePointer();
        _view.Dispose();
        _mapping.Dispose();
        _file.Dispose();
        _folder.Dispose();
    }
}
