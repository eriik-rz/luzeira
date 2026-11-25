using System.Buffers.Binary;
using System.Diagnostics;
using LuzeiraSensores.Core;
using Microsoft.Win32.SafeHandles;

namespace LuzeiraSensores.Tests;

/// <summary>
/// El fichero mapeado de verdad (CreateFileW, GetSecurityInfo, MapViewOfFile), en una
/// carpeta temporal. Sin administrador: la carpeta es del usuario, así que la
/// comprobación real la rechaza (es lo que se prueba primero) y para lo demás se inyecta
/// una que lo acepta todo.
/// </summary>
public sealed class SectionFileTests : IDisposable
{
    private readonly string _root = Path.Combine(Path.GetTempPath(), "luzeira-sensores-tests-" + Guid.NewGuid().ToString("N"));

    public SectionFileTests() => Directory.CreateDirectory(_root);

    public void Dispose()
    {
        try
        {
            Directory.Delete(_root, recursive: true);
        }
        catch (IOException)
        {
        }
    }

    private static TrustProblem AcceptAll(string sddl, bool folder) => TrustProblem.None;

    private string Folder(string name = "carpeta")
    {
        string path = Path.Combine(_root, name);
        Directory.CreateDirectory(path);
        return path;
    }

    [Fact]
    public void A_folder_owned_by_the_user_is_refused_and_nothing_is_created()
    {
        string folder = Folder();
        Assert.Null(SectionFile.TryOpen(folder, out string? problem));
        Assert.Contains("Owner", problem);
        Assert.False(File.Exists(Path.Combine(folder, SectionFile.FileName)));
    }

    [Fact]
    public void A_missing_folder_is_not_created()
    {
        string folder = Path.Combine(_root, "no-existe");
        Assert.Null(SectionFile.TryOpen(folder, out string? problem, AcceptAll));
        Assert.Contains("no existe", problem);
        Assert.False(Directory.Exists(folder));
    }

    [Fact]
    public void A_junction_is_refused_even_if_its_target_is_fine()
    {
        string target = Folder("destino");
        string link = Path.Combine(_root, "enlace");
        // Una junction la puede crear cualquier usuario (no hace falta admin).
        using (Process mklink = Process.Start(new ProcessStartInfo("cmd.exe", ["/c", "mklink", "/J", link, target])
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
        })!)
        {
            mklink.WaitForExit();
            Assert.Equal(0, mklink.ExitCode);
        }

        Assert.Null(SectionFile.TryOpen(link, out string? problem, AcceptAll));
        Assert.Contains("enlace", problem);
        Assert.False(File.Exists(Path.Combine(target, SectionFile.FileName)));
        Directory.Delete(link);
    }

    [Fact]
    public void A_file_with_a_hard_link_is_refused()
    {
        string folder = Folder();
        string file = Path.Combine(folder, SectionFile.FileName);
        File.WriteAllBytes(Path.Combine(_root, "otro.bin"), new byte[Layout.SectionSize]);
        Assert.True(CreateHardLink(file, Path.Combine(_root, "otro.bin"), IntPtr.Zero));
        Assert.Null(SectionFile.TryOpen(folder, out string? problem, AcceptAll));
        Assert.Contains("enlaces duros", problem);
    }

    [Fact]
    public void A_directory_in_place_of_the_file_is_refused()
    {
        string folder = Folder();
        Directory.CreateDirectory(Path.Combine(folder, SectionFile.FileName));
        Assert.Null(SectionFile.TryOpen(folder, out string? problem, AcceptAll));
        Assert.NotNull(problem);
    }

    [Fact]
    public void Creates_the_file_with_its_own_protected_dacl_and_publishes()
    {
        string folder = Folder();
        // Sin el propietario SYSTEM: un usuario no lo puede poner (ver el test de abajo).
        using (SectionFile? section = SectionFile.TryOpen(folder, out string? problem, AcceptAll, FolderSecurity.FileDacl))
        {
            Assert.True(section is not null, problem);
            var publisher = new SeqlockPublisher(section, initialSequence: 1000);
            byte[] expected = SectionWriter.Build(ModuleStatus.Ok, [new(SensorIds.CpuTemperature, 50.5)], 1002, 7, "0.1.0");
            publisher.Publish(seq => SectionWriter.Build(ModuleStatus.Ok, [new(SensorIds.CpuTemperature, 50.5)], seq, 7, "0.1.0"));

            // Lo que vería Luzeira: abre solo para leer y comparte lectura y escritura.
            string path = Path.Combine(folder, SectionFile.FileName);
            using (var reader = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            {
                Assert.Equal(Layout.SectionSize, reader.Length);
                var data = new byte[Layout.SectionSize];
                reader.ReadExactly(data);
                Assert.Equal(expected, data);
                Assert.Equal(1002UL, BinaryPrimitives.ReadUInt64LittleEndian(data.AsSpan(24)));
            }

            // Nadie más lo puede abrir para escribir mientras el servicio lo tiene.
            Assert.ThrowsAny<Exception>(() => new FileStream(path, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite));

            // La DACL es la explícita del servicio (el propietario aquí es el usuario).
            using SafeFileHandle handle = File.OpenHandle(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
            string sddl = Native.SddlOf(handle);
            Assert.EndsWith(FolderSecurity.FileDacl, sddl);
        }
    }

    [Fact]
    public void The_real_descriptor_sets_system_as_owner()
    {
        // Con el descriptor de verdad (O:SY), un usuario normal no puede crear el fichero:
        // Windows no le deja poner a otro de propietario (ERROR_INVALID_OWNER, 1307). Así
        // se ve que el servicio pide SYSTEM explícitamente.
        string folder = Folder();
        Assert.Null(SectionFile.TryOpen(folder, out string? problem, out SectionOpenFailure failure, AcceptAll));
        Assert.Equal(SectionOpenFailure.Other, failure);
        Assert.Contains("1307", problem);
    }

    [Fact]
    public void The_file_is_checked_too_not_only_the_folder()
    {
        // M3: carpeta de fiar y fichero no.
        string folder = Folder();
        File.WriteAllBytes(Path.Combine(folder, SectionFile.FileName), new byte[Layout.SectionSize]);
        Assert.Null(SectionFile.TryOpen(folder, out string? problem, out SectionOpenFailure failure, (_, isFolder) => isFolder ? TrustProblem.None : TrustProblem.ForeignWriter));
        Assert.Equal(SectionOpenFailure.Untrusted, failure);
        Assert.Contains("el fichero", problem);
    }

    [Fact]
    public void The_folder_cannot_be_renamed_while_open()
    {
        // M4: la carpeta se abre sin FILE_SHARE_DELETE. Se prueba el handle de la carpeta
        // solo: con el fichero de dentro abierto, Windows ya no deja renombrarla, y eso
        // taparía el hueco entre comprobar la carpeta y abrir el fichero.
        string folder = Folder();
        using (SafeFileHandle handle = SectionFile.OpenFolder(folder))
        {
            Assert.False(handle.IsInvalid);
            Assert.ThrowsAny<IOException>(() => Directory.Move(folder, folder + "-otra"));
        }

        Directory.Move(folder, folder + "-otra"); // ya cerrada, sí
    }

    [Fact]
    public void A_symlink_in_place_of_the_file_is_refused()
    {
        // M5: el fichero no se abre siguiendo enlaces. Crear un symlink pide privilegio o
        // el modo de desarrollador: sin él, el test no puede probar nada y no falla.
        string folder = Folder();
        string target = Path.Combine(_root, "destino.bin");
        File.WriteAllBytes(target, new byte[Layout.SectionSize]);
        try
        {
            File.CreateSymbolicLink(Path.Combine(folder, SectionFile.FileName), target);
        }
        catch (IOException)
        {
            return;
        }
        catch (UnauthorizedAccessException)
        {
            return;
        }

        Assert.Null(SectionFile.TryOpen(folder, out string? problem, AcceptAll));
        Assert.Contains("enlace", problem);
        Assert.All(File.ReadAllBytes(target), b => Assert.Equal(0, b));
    }

    [Fact]
    public void A_file_in_place_of_the_folder_is_refused()
    {
        // M11b (la mitad que se puede probar): la comprobación de tipo de la carpeta. La
        // del fichero no tiene caso: sin FILE_FLAG_BACKUP_SEMANTICS, CreateFileW no abre
        // una carpeta (lo prueba A_directory_in_place_of_the_file_is_refused).
        string path = Path.Combine(_root, "no-es-carpeta");
        File.WriteAllBytes(path, [1]);
        Assert.Null(SectionFile.TryOpen(path, out string? problem, AcceptAll));
        Assert.Contains("no es del tipo esperado", problem);
    }

    [Fact]
    public void Writes_outside_the_section_are_refused()
    {
        // M14
        string folder = Folder();
        File.WriteAllBytes(Path.Combine(folder, SectionFile.FileName), new byte[4096]);
        using SectionFile? section = SectionFile.TryOpen(folder, out string? problem, AcceptAll);
        Assert.True(section is not null, problem);
        Assert.Throws<ArgumentOutOfRangeException>(() => section.Write(Layout.SectionSize - 4, new byte[8]));
        Assert.Throws<ArgumentOutOfRangeException>(() => section.Write(-1, new byte[1]));
        section.Write(Layout.SectionSize - 8, new byte[8]);
    }

    [Fact]
    public void A_reader_that_does_not_share_writing_is_reported_as_busy()
    {
        // BAJA 3: un usuario abre el fichero solo para leer y sin compartir escritura
        // mientras el servicio no lo tiene: error 32, con su propio motivo.
        string folder = Folder();
        string path = Path.Combine(folder, SectionFile.FileName);
        File.WriteAllBytes(path, new byte[Layout.SectionSize]);
        using var holder = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        Assert.Null(SectionFile.TryOpen(folder, out string? problem, out SectionOpenFailure failure, AcceptAll));
        Assert.Equal(SectionOpenFailure.Busy, failure);
        Assert.Contains("error 32", problem);
    }

    [Fact]
    public void Opens_an_existing_file_while_a_reader_holds_it()
    {
        // R4-10: si el servicio se reinicia con Luzeira leyendo, vuelve a abrir sin borrar
        // ni truncar. (El fichero «de antes» lo crea aquí el test con los permisos de la
        // carpeta temporal: uno creado por el servicio solo lo podría reabrir SYSTEM.)
        string folder = Folder();
        string path = Path.Combine(folder, SectionFile.FileName);
        File.WriteAllBytes(path, new byte[Layout.SectionSize]);

        using var reader = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite);
        using SectionFile? section = SectionFile.TryOpen(folder, out string? problem, AcceptAll);
        Assert.True(section is not null, problem);
        new SeqlockPublisher(section, 20).Publish(seq => SectionWriter.Build(ModuleStatus.Ok, [], seq, 0, "b"));
        var data = new byte[Layout.SectionSize];
        reader.ReadExactly(data);
        Assert.Equal(22UL, BinaryPrimitives.ReadUInt64LittleEndian(data.AsSpan(24)));
    }

    [Fact]
    public void A_short_file_is_grown_and_a_longer_one_is_left_alone()
    {
        string folder = Folder();
        string path = Path.Combine(folder, SectionFile.FileName);
        File.WriteAllBytes(path, new byte[100]);
        using (SectionFile? section = SectionFile.TryOpen(folder, out string? problem, AcceptAll))
            Assert.True(section is not null, problem);
        Assert.Equal(Layout.SectionSize, new FileInfo(path).Length);

        File.WriteAllBytes(path, new byte[4096]);
        using (SectionFile? section = SectionFile.TryOpen(folder, out string? problem, AcceptAll))
            Assert.True(section is not null, problem);
        Assert.Equal(4096, new FileInfo(path).Length);
    }

    [System.Runtime.InteropServices.DllImport("kernel32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode, SetLastError = true)]
    [System.Runtime.InteropServices.DefaultDllImportSearchPaths(System.Runtime.InteropServices.DllImportSearchPath.System32)]
    private static extern bool CreateHardLink(string newFile, string existingFile, IntPtr securityAttributes);
}
