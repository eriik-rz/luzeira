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
- La app **solo se conecta a internet si le dejas**, para avisarte de versiones nuevas. Lo pregunta una vez y se cambia en «Ajustes» o en el menú del icono. Sin ese «sí», no se conecta a nada.

## Cómo funcionan las actualizaciones

- Una vez al día pide a GitHub, por HTTPS, el fichero `manifest.json` de la última versión de este repositorio, y su firma. No envía nada tuyo: es una petición normal, como la de un navegador (GitHub ve tu IP, como en cualquier descarga).
- Solo habla con `github.com` y con los servidores de descargas de GitHub; si una redirección lleva a otro sitio, se corta.
- El manifiesto va **firmado con Ed25519** por quien mantiene el proyecto, con una clave que **no está en GitHub**. La app trae dentro solo las claves públicas y, si la firma no casa, lo descarta. Así, ni quien se hiciera con la cuenta de GitHub podría colar una versión.
- Nunca se ofrece una versión más vieja que la instalada ni que la más nueva que ya se haya visto.
- **Nunca se instala nada sin que pulses «Actualizar».** Entonces descarga el instalador de esa misma versión, comprueba su tamaño y su SHA-256 contra el manifiesto firmado (y otra vez justo antes de abrirlo), cierra Luzeira, instala encima conservando tus temas y tu configuración, y la vuelve a abrir.
- Si prefieres hacerlo a mano, sigue valiendo: descarga el instalador de Releases y ejecútalo encima.
