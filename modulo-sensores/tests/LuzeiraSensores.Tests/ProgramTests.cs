using System.Text.RegularExpressions;
using LuzeiraSensores.Core;

namespace LuzeiraSensores.Tests;

/// <summary>El arranque del servicio (revisión, BAJA 4).</summary>
public class ProgramTests
{
    /// <summary>La carpeta modulo-sensores, subiendo desde donde corre el test.</summary>
    private static string ModuleRoot()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
        {
            if (File.Exists(Path.Combine(dir.FullName, "LuzeiraSensores.slnx")))
                return dir.FullName;
        }

        throw new InvalidOperationException("No se encuentra LuzeiraSensores.slnx.");
    }

    [Fact]
    public void Program_exits_if_the_dll_search_cannot_be_hardened()
    {
        string program = File.ReadAllText(Path.Combine(ModuleRoot(), "src", "LuzeiraSensores", "Program.cs"));
        Assert.Matches(new Regex(@"if \(!DllSearch\.Harden\(\)\)\s*\{\s*Environment\.Exit\(1\);\s*\}"), program);
        // Y antes de arrancar el servicio.
        Assert.True(program.IndexOf("DllSearch.Harden()", StringComparison.Ordinal) < program.IndexOf("ServiceBase.Run", StringComparison.Ordinal));
    }

    [Fact]
    public void Harden_works_on_this_windows()
    {
        Assert.True(DllSearch.Harden());
    }
}
