using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace LuzeiraSensores.Core;

/// <summary>
/// Las funciones de Windows que usa el servicio (todas de kernel32 y advapi32, que se
/// cargan de System32: ver el atributo DefaultDllImportSearchPaths en Program.cs).
/// </summary>
/// <remarks>
/// Fuentes (Microsoft Learn): CreateFileW, GetFileInformationByHandle,
/// GetSecurityInfo, ConvertSecurityDescriptorToStringSecurityDescriptorW,
/// ConvertStringSecurityDescriptorToSecurityDescriptorW, SetDefaultDllDirectories y
/// «File Security and Access Rights».
/// </remarks>
internal static partial class Native
{
    public const uint GenericRead = 0x80000000;
    public const uint GenericWrite = 0x40000000;
    public const uint ReadControl = 0x00020000;
    public const uint FileReadData = 0x0001;
    public const uint FileWriteData = 0x0002;
    public const uint FileReadAttributes = 0x0080;

    public const uint FileShareRead = 0x1;
    public const uint FileShareWrite = 0x2;

    public const uint OpenExisting = 3;
    public const uint OpenAlways = 4;

    public const uint FileAttributeNormal = 0x80;
    public const uint FileAttributeDirectory = 0x10;
    public const uint FileAttributeReparsePoint = 0x400;
    public const uint FileFlagBackupSemantics = 0x02000000;  // para abrir una carpeta
    public const uint FileFlagOpenReparsePoint = 0x00200000; // abrir el enlace, no lo que apunta

    public const int ErrorFileNotFound = 2;
    public const int ErrorPathNotFound = 3;
    public const int ErrorSharingViolation = 32;

    public const int SeFileObject = 1;
    public const uint OwnerSecurityInformation = 0x1;
    public const uint DaclSecurityInformation = 0x4;
    public const uint SddlRevision1 = 1;

    public const uint LoadLibrarySearchApplicationDir = 0x00000200;
    public const uint LoadLibrarySearchSystem32 = 0x00000800;

    [StructLayout(LayoutKind.Sequential)]
    public struct SecurityAttributes
    {
        public int Length;
        public IntPtr SecurityDescriptor;
        public int InheritHandle;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct FileTime
    {
        public uint Low;
        public uint High;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct ByHandleFileInformation
    {
        public uint FileAttributes;
        public FileTime CreationTime;
        public FileTime LastAccessTime;
        public FileTime LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }

    [LibraryImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool SetDefaultDllDirectories(uint directoryFlags);

    [LibraryImport("kernel32.dll", EntryPoint = "CreateFileW", SetLastError = true, StringMarshalling = StringMarshalling.Utf16)]
    public static partial SafeFileHandle CreateFile(
        string fileName,
        uint desiredAccess,
        uint shareMode,
        IntPtr securityAttributes,
        uint creationDisposition,
        uint flagsAndAttributes,
        IntPtr templateFile);

    [LibraryImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool GetFileInformationByHandle(SafeFileHandle file, out ByHandleFileInformation information);

    [LibraryImport("kernel32.dll")]
    public static partial IntPtr LocalFree(IntPtr memory);

    [LibraryImport("advapi32.dll")]
    public static partial uint GetSecurityInfo(
        SafeHandle handle,
        int objectType,
        uint securityInfo,
        out IntPtr owner,
        out IntPtr group,
        out IntPtr dacl,
        out IntPtr sacl,
        out IntPtr securityDescriptor);

    [LibraryImport("advapi32.dll", EntryPoint = "ConvertSecurityDescriptorToStringSecurityDescriptorW", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool ConvertSecurityDescriptorToString(
        IntPtr securityDescriptor,
        uint revision,
        uint securityInfo,
        out IntPtr text,
        out uint length);

    [LibraryImport("advapi32.dll", EntryPoint = "ConvertStringSecurityDescriptorToSecurityDescriptorW", SetLastError = true, StringMarshalling = StringMarshalling.Utf16)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool ConvertStringToSecurityDescriptor(
        string text,
        uint revision,
        out IntPtr securityDescriptor,
        IntPtr size);

    /// <summary>Propietario y DACL de un handle abierto, en SDDL. Leer por handle (y no
    /// por ruta) evita que alguien cambie el objeto entre la comprobación y el uso.</summary>
    public static string SddlOf(SafeHandle handle)
    {
        const uint what = OwnerSecurityInformation | DaclSecurityInformation;
        uint status = GetSecurityInfo(handle, SeFileObject, what, out _, out _, out _, out _, out IntPtr descriptor);
        if (status != 0)
            throw new System.ComponentModel.Win32Exception((int)status);
        try
        {
            if (!ConvertSecurityDescriptorToString(descriptor, SddlRevision1, what, out IntPtr text, out _))
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastPInvokeError());
            try
            {
                return Marshal.PtrToStringUni(text) ?? string.Empty;
            }
            finally
            {
                LocalFree(text);
            }
        }
        finally
        {
            LocalFree(descriptor);
        }
    }
}
