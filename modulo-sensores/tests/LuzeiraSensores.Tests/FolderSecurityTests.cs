using LuzeiraSensores.Core;

namespace LuzeiraSensores.Tests;

/// <summary>La comprobación de la carpeta y del fichero (b), con descriptores en SDDL.
/// Los casos son los de las reglas de Luzeira (permisos.foreign_writers y
/// windows_cpu.check_security) más la DACL protegida de la carpeta.</summary>
public class FolderSecurityTests
{
    private const string Good = "O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FR;;;BU)";

    [Fact]
    public void The_installer_folder_is_trusted()
    {
        Assert.Equal(TrustProblem.None, FolderSecurity.Check(Good, folder: true));
        Assert.Equal(TrustProblem.None, FolderSecurity.Check("O:SY" + FolderSecurity.FolderSddl, folder: true));
        // Windows puede añadir la marca de «autoheredada» (AI) al leerla.
        Assert.Equal(TrustProblem.None, FolderSecurity.Check("O:BAD:PAI(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FR;;;BU)", folder: true));
    }

    [Fact]
    public void The_file_the_service_creates_is_trusted()
    {
        Assert.Equal(TrustProblem.None, FolderSecurity.Check(FolderSecurity.FileSddl, folder: false));
        // Y heredando de la carpeta (sin protegerse) también.
        Assert.Equal(TrustProblem.None, FolderSecurity.Check("O:BAD:AI(A;ID;FA;;;SY)(A;ID;FA;;;BA)(A;ID;FR;;;BU)", folder: false));
    }

    [Theory]
    [InlineData("O:BUD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FR;;;BU)")]               // Usuarios
    [InlineData("O:S-1-5-21-1-2-3-1001D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)")]             // un usuario
    [InlineData("O:S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464D:P(A;;FA;;;SY)")] // TrustedInstaller: escribe, pero no es dueño aceptado
    [InlineData("D:P(A;OICI;FA;;;SY)")]                                                    // sin propietario
    public void Owner_must_be_system_or_administrators(string sddl)
    {
        Assert.Equal(TrustProblem.Owner, FolderSecurity.Check(sddl, folder: true));
    }

    [Fact]
    public void A_folder_that_inherits_from_programdata_is_refused()
    {
        // La herencia de ProgramData da a Usuarios crear ficheros y carpetas.
        Assert.Equal(TrustProblem.NotProtected, FolderSecurity.Check("O:BAD:AI(A;OICIID;FA;;;SY)(A;OICIID;FA;;;BA)(A;OICIID;GRGX;;;BU)", folder: true));
        Assert.Equal(
            TrustProblem.ForeignWriter,
            FolderSecurity.Check("O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;CIID;DCLCRPCR;;;BU)", folder: true));
    }

    [Fact]
    public void No_dacl_is_refused()
    {
        Assert.Equal(TrustProblem.NoDacl, FolderSecurity.Check("O:BAD:NO_ACCESS_CONTROL", folder: true));
        Assert.Equal(TrustProblem.NoDacl, FolderSecurity.Check("O:BA", folder: false));
    }

    [Theory]
    [InlineData("FA")]          // control total
    [InlineData("FW")]          // FILE_GENERIC_WRITE
    [InlineData("GA")]          // GENERIC_ALL
    [InlineData("GW")]          // GENERIC_WRITE
    [InlineData("WD")]          // WRITE_DAC
    [InlineData("WO")]          // WRITE_OWNER
    [InlineData("SD")]          // DELETE: renombrar y poner otra en su lugar
    [InlineData("0x2")]         // FILE_WRITE_DATA / FILE_ADD_FILE
    [InlineData("0x4")]         // FILE_APPEND_DATA / FILE_ADD_SUBDIRECTORY
    public void Writing_rights_for_users_are_refused(string right)
    {
        string sddl = $"O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;{right};;;BU)";
        Assert.Equal(TrustProblem.ForeignWriter, FolderSecurity.Check(sddl, folder: true));
        Assert.Equal(TrustProblem.ForeignWriter, FolderSecurity.Check(sddl, folder: false));
    }

    [Fact]
    public void Delete_child_only_counts_in_a_folder()
    {
        const string sddl = "O:BAD:P(A;;FA;;;SY)(A;;0x40;;;WD)";
        Assert.Equal(TrustProblem.ForeignWriter, FolderSecurity.Check(sddl, folder: true));
        Assert.Equal(TrustProblem.None, FolderSecurity.Check(sddl, folder: false));
    }

    [Fact]
    public void Inherit_only_aces_count()
    {
        Assert.Equal(
            TrustProblem.ForeignWriter,
            FolderSecurity.Check("O:BAD:P(A;OICI;FA;;;SY)(A;OICIIO;FA;;;WD)", folder: true));
    }

    [Fact]
    public void Deny_aces_are_ignored()
    {
        Assert.Equal(TrustProblem.None, FolderSecurity.Check("O:BAD:P(D;;FA;;;WD)(A;OICI;FA;;;SY)(A;OICI;FR;;;BU)", folder: true));
    }

    [Fact]
    public void Trusted_writers_are_accepted()
    {
        Assert.Equal(
            TrustProblem.None,
            FolderSecurity.Check("O:BAD:P(A;OICI;FA;;;SY)(A;OICIIO;FA;;;CO)(A;;FA;;;S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464)", folder: true));
    }

    [Fact]
    public void Odd_ace_types_are_refused()
    {
        // ACE de objeto (OA): en una carpeta de ficheros no debería aparecer.
        Assert.Equal(
            TrustProblem.UnknownAce,
            FolderSecurity.Check("O:BAD:P(A;;FA;;;SY)(OA;;FR;bf967aba-0de6-11d0-a285-00aa003049e2;;BU)", folder: true));
    }

    [Fact]
    public void An_empty_descriptor_is_refused()
    {
        // Sin nada: Windows lo da por bueno como descriptor vacío (sin propietario).
        Assert.Equal(TrustProblem.Owner, FolderSecurity.Check("", folder: true));
    }

    [Fact]
    public void Callback_allow_aces_are_refused()
    {
        // M2: una ACE de permitir con condición (XA) no se sabe evaluar: se desconfía,
        // aunque solo dé leer.
        Assert.Equal(
            TrustProblem.UnknownAce,
            FolderSecurity.Check("O:BAD:P(A;;FA;;;SY)(XA;;FR;;;WD;(Member_of {SID(BA)}))", folder: true));
    }

    [Fact]
    public void The_file_is_created_with_system_as_owner()
    {
        Assert.StartsWith("O:SY", FolderSecurity.FileSddl);
        Assert.EndsWith(FolderSecurity.FileDacl, FolderSecurity.FileSddl);
    }

    [Theory]
    [InlineData("esto no es SDDL")]
    [InlineData("O:BAD:P(A;;FA;;;SY")]
    public void Garbage_is_unreadable(string sddl)
    {
        Assert.Equal(TrustProblem.Unreadable, FolderSecurity.Check(sddl, folder: true));
    }

    [Fact]
    public void Write_masks_match_permisos_py()
    {
        // FILE_WRITE_RIGHTS = FILE_WRITE_DATA|FILE_APPEND_DATA|DELETE|WRITE_DAC|WRITE_OWNER|GENERIC_WRITE|GENERIC_ALL
        Assert.Equal(0x0002 | 0x0004 | 0x00010000 | 0x00040000 | 0x00080000 | 0x40000000 | 0x10000000, FolderSecurity.FileWriteRights);
        Assert.Equal(FolderSecurity.FileWriteRights | 0x0040, FolderSecurity.FolderWriteRights);
    }
}
