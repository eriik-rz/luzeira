using System.Runtime.InteropServices;

// ADR-008, punto 2: las llamadas a Windows de esta biblioteca (Native.cs) cargan sus DLL
// solo de System32 o de la carpeta del programa. El atributo vale por ensamblado: el del
// servicio (Program.cs) no cubre a este.
[assembly: DefaultDllImportSearchPaths(DllImportSearchPath.System32 | DllImportSearchPath.AssemblyDirectory)]
