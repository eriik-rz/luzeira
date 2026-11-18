# Política de seguridad

## Cómo reportar una vulnerabilidad

**No abras un issue público.** Usa el reporte privado de GitHub: pestaña **Security → Report a vulnerability** del repositorio.

Incluye qué versión usas, cómo reproducirlo y qué impacto crees que tiene. Intentaremos responder en un plazo de 7 días.

## Versiones con soporte

Solo la última versión publicada recibe arreglos de seguridad.

## Cómo verificar que tu descarga es oficial

La única fuente oficial de Luzeira son las **Releases de este repositorio** (y, cuando existan, los canales que se anuncien aquí). Cada versión publica la suma `SHA256SUMS` y una atestación de procedencia de GitHub:

```bash
gh attestation verify <fichero-descargado> --repo eriik-rz/luzeira
```

Sin `gh`, basta con comparar la huella del fichero con la que pone `SHA256SUMS` en la misma release. En PowerShell:

```powershell
Get-FileHash .\Luzeira-<versión>-instalador.exe -Algorithm SHA256
```

Si alguien distribuye Luzeira desde otro sitio, no podemos garantizar que no esté modificada.

## Principios de diseño

- La app **no lleva claves ni tokens** dentro.
- **Sin telemetría** y sin anuncios.
- La app normal corre **sin permisos de administrador**. El módulo opcional de sensores de CPU (futuro) será un componente separado y mínimo.
- Los temas son **datos** (JSON e imágenes), nunca código ejecutable.
- La app **no se conecta a internet** ni se actualiza sola. Para actualizar, descarga el instalador nuevo de Releases y ejecútalo encima: conserva tus temas y tu configuración. Si algún día hay aviso de versiones nuevas, será opcional y las actualizaciones irán verificadas con firma.
