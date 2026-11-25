using System.Security.AccessControl;
using System.Security.Principal;

namespace LuzeiraSensores.Core;

/// <summary>Por qué no se fía el servicio de la carpeta o del fichero.</summary>
public enum TrustProblem
{
    None,

    /// <summary>El texto del descriptor no se puede interpretar.</summary>
    Unreadable,

    /// <summary>El propietario no es SYSTEM ni Administradores.</summary>
    Owner,

    /// <summary>No hay DACL: Windows deja hacer todo a todo el mundo.</summary>
    NoDacl,

    /// <summary>La DACL de la carpeta no está protegida (hereda de ProgramData, que deja
    /// crear ficheros a los usuarios).</summary>
    NotProtected,

    /// <summary>Alguien de fuera de SYSTEM/Administradores/TrustedInstaller/CREATOR OWNER
    /// puede escribir, borrar o cambiar los permisos.</summary>
    ForeignWriter,

    /// <summary>Una ACE de un tipo raro (de objeto, con condición…): se desconfía.</summary>
    UnknownAce,
}

/// <summary>
/// Decide si la carpeta <c>%ProgramData%\Luzeira Sensores</c> y el fichero
/// <c>sensores.bin</c> son de fiar, a partir de su descriptor de seguridad en texto SDDL.
/// </summary>
/// <remarks>
/// Conceptos: el descriptor de seguridad de un fichero lleva un <b>propietario</b> (un
/// SID) y una <b>DACL</b>, una lista de entradas (ACE) que dicen a quién se le permite o
/// se le niega qué. En texto (SDDL) se ve así:
/// <c>O:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FR;;;BU)</c> = propietario
/// Administradores (BA); DACL protegida (P, no hereda); SYSTEM (SY) y Administradores,
/// control total (FA); Usuarios (BU), leer (FR); OICI = la heredan ficheros y carpetas.
///
/// Las reglas son las mismas que aplica Luzeira al leer (<c>windows_cpu.check_security</c>
/// y <c>permisos.foreign_writers</c>), para que el servicio nunca escriba donde Luzeira
/// no se fiaría de leer; y además la carpeta tiene que tener la DACL protegida.
/// Esta clase solo decide: leer el descriptor de un handle de verdad lo hace el servicio.
/// </remarks>
public static class FolderSecurity
{
    /// <summary>La DACL que pone el instalador en la carpeta (ADR-008, punto 6).</summary>
    public const string FolderSddl = "D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FR;;;BU)";

    /// <summary>La DACL del fichero: la de la carpeta, sin herencia.</summary>
    public const string FileDacl = "D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;FR;;;BU)";

    /// <summary>Lo que pone el servicio al crear el fichero: propietario SYSTEM explícito
    /// (no el que toque por defecto al token) y <see cref="FileDacl"/>.</summary>
    public const string FileSddl = "O:SY" + FileDacl;

    public const string SidSystem = "S-1-5-18";
    public const string SidAdministrators = "S-1-5-32-544";
    public const string SidTrustedInstaller = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464";
    public const string SidCreatorOwner = "S-1-3-0";

    // Máscaras (winnt.h; «File Security and Access Rights» en Microsoft Learn).
    private const int Delete = 0x00010000;
    private const int WriteDac = 0x00040000;
    private const int WriteOwner = 0x00080000;
    private const int GenericAll = 0x10000000;
    private const int GenericWrite = 0x40000000;
    private const int FileWriteData = 0x0002;     // en una carpeta: FILE_ADD_FILE
    private const int FileAppendData = 0x0004;    // en una carpeta: FILE_ADD_SUBDIRECTORY
    private const int FileDeleteChild = 0x0040;

    /// <summary>Lo que deja cambiar el contenido de un fichero o quitarlo de en medio
    /// (<c>FILE_WRITE_RIGHTS</c> de permisos.py).</summary>
    public const int FileWriteRights =
        FileWriteData | FileAppendData | Delete | WriteDac | WriteOwner | GenericWrite | GenericAll;

    /// <summary>En una carpeta, además, borrar hijos (<c>FOLDER_WRITE_RIGHTS</c>).</summary>
    public const int FolderWriteRights = FileWriteRights | FileDeleteChild;

    private static readonly HashSet<string> TrustedOwners = new(StringComparer.Ordinal)
    {
        SidSystem,
        SidAdministrators,
    };

    private static readonly HashSet<string> TrustedWriters = new(StringComparer.Ordinal)
    {
        SidSystem,
        SidAdministrators,
        SidTrustedInstaller,
        SidCreatorOwner,
    };

    /// <summary>Comprueba un descriptor (propietario y DACL en SDDL).</summary>
    /// <param name="sddl">El texto que da <c>ConvertSecurityDescriptorToStringSecurityDescriptorW</c>.</param>
    /// <param name="folder">Si es la carpeta (cambian la máscara y se exige DACL protegida).</param>
    public static TrustProblem Check(string sddl, bool folder)
    {
        RawSecurityDescriptor descriptor;
        try
        {
            descriptor = new RawSecurityDescriptor(sddl);
        }
        catch (Exception e) when (e is ArgumentException or FormatException or OverflowException)
        {
            return TrustProblem.Unreadable;
        }

        SecurityIdentifier? owner = descriptor.Owner;
        if (owner is null || !TrustedOwners.Contains(owner.Value))
            return TrustProblem.Owner;

        RawAcl? dacl = descriptor.DiscretionaryAcl;
        if ((descriptor.ControlFlags & ControlFlags.DiscretionaryAclPresent) == 0 || dacl is null)
            return TrustProblem.NoDacl;

        if (folder && (descriptor.ControlFlags & ControlFlags.DiscretionaryAclProtected) == 0)
            return TrustProblem.NotProtected;

        int rights = folder ? FolderWriteRights : FileWriteRights;
        foreach (GenericAce ace in dacl)
        {
            switch (ace.AceType)
            {
                // Las de denegar solo quitan permisos: no contarlas nunca hace pasar algo
                // inseguro (lo mismo que foreign_writers en Python).
                case AceType.AccessDenied:
                case AceType.AccessDeniedObject:
                case AceType.AccessDeniedCallback:
                case AceType.AccessDeniedCallbackObject:
                    continue;
                case AceType.AccessAllowed:
                    var common = (CommonAce)ace;
                    // Cuentan también las que solo se heredan (INHERIT_ONLY): dicen qué
                    // permisos tendrá lo que se cree dentro.
                    if ((common.AccessMask & rights) != 0 && !TrustedWriters.Contains(common.SecurityIdentifier.Value))
                        return TrustProblem.ForeignWriter;
                    continue;
                default:
                    return TrustProblem.UnknownAce;
            }
        }

        return TrustProblem.None;
    }
}
