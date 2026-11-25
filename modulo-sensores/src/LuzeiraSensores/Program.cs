using System.Runtime.InteropServices;
using System.ServiceProcess;
using LuzeiraSensores;
using LuzeiraSensores.Core;

// ADR-008, punto 2: las DLL de nuestras llamadas a Windows se buscan solo en System32 y
// en la carpeta del programa (Program Files, donde un usuario no escribe), nunca en la
// carpeta actual ni en el PATH. LibreHardwareMonitor usa CsWin32, que pone lo mismo
// (System32) en cada una de sus funciones.
[assembly: DefaultDllImportSearchPaths(DllImportSearchPath.System32 | DllImportSearchPath.AssemblyDirectory)]

// Y para todo el proceso (también lo que cargue con LoadLibrary sin decir dónde):
// SetDefaultDllDirectories, como pedía el ADR-007.
// Si no se puede, el servicio no sigue: buscaría DLL en sitios que no controlamos.
if (!DllSearch.Harden())
{
    Environment.Exit(1);
}

// Sin argumentos ni modo de consola: solo arranca como servicio.
ServiceBase.Run(new SensorService());
