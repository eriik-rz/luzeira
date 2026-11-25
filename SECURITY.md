# Política de seguridad

## Cómo reportar una vulnerabilidad

**No abras un issue público.** Usa el reporte privado de GitHub: pestaña **Security → Report a vulnerability** del repositorio.

Incluye qué versión usas, cómo reproducirlo y qué impacto crees que tiene. Intentaremos responder en un plazo de 7 días.

## Versiones con soporte

Solo la última versión publicada recibe arreglos de seguridad.

## Cómo verificar que tu descarga es oficial

La única fuente oficial de Luzeira son las **Releases de este repositorio** (y, cuando existan, los canales que se anuncien aquí). Cada versión publica la suma `SHA256SUMS`, su firma `SHA256SUMS.sig` y una atestación de procedencia de GitHub.

**1. La huella.** Compara la del fichero con la que pone `SHA256SUMS` en la misma release. En PowerShell:

```powershell
Get-FileHash .\Luzeira-<versión>-instalador.exe -Algorithm SHA256
```

Esto solo dice que la descarga llegó entera: quien controlase la cuenta de GitHub podría cambiar los dos ficheros a la vez.

**2. La atestación de GitHub.** Comprueba que el fichero lo construyó el workflow de publicación de este repositorio (y no otro workflow ni un ordenador cualquiera):

```bash
gh attestation verify <fichero-descargado> --repo eriik-rz/luzeira --signer-workflow eriik-rz/luzeira/.github/workflows/release.yml --deny-self-hosted-runners
```

Tampoco protege si alguien se hace con la cuenta de GitHub: podría lanzar ese mismo workflow.

**3. La firma de quien mantiene el proyecto.** `SHA256SUMS.sig` es una firma Ed25519 (en base64) de `SHA256SUMS`, hecha con una clave que **no está en GitHub**. Es la misma clave con la que la app comprueba sus actualizaciones. Con OpenSSL 3 (viene con Git para Windows), guarda estas dos claves públicas en `luzeira-firma.pem` y `luzeira-reserva.pem`:

```text
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEA9sCofpm/Fpa1cLFv8cPDv5xPAOvRjguMchK/fvBPkKQ=
-----END PUBLIC KEY-----
```

```text
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAJ+ArjKOLJIEbsR45x/RjHNTk9HKSANuzcSb3UCAduh0=
-----END PUBLIC KEY-----
```

```bash
openssl base64 -d -A -in SHA256SUMS.sig -out SHA256SUMS.sig.bin
openssl pkeyutl -verify -pubin -inkey luzeira-firma.pem -rawin -in SHA256SUMS -sigfile SHA256SUMS.sig.bin
```

Tiene que decir `Signature Verified Successfully` con una de las dos claves (la segunda es la de reserva). Después, comprueba la huella como en el paso 1. ⚠ Coge las claves de una copia en la que ya confíes (por ejemplo, el código de una versión que ya tengas: `PUBLIC_KEYS` en `src/luzeira/actualizar.py`), no solo de esta página, que también vive en GitHub. Las versiones hasta la 0.1.7 no llevan `SHA256SUMS.sig`.

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
- El manifiesto va **firmado con Ed25519** por quien mantiene el proyecto, con una clave que **no está en GitHub**. La app trae dentro solo las claves públicas y, si la firma no casa, lo descarta. Así, quien se hiciera con la cuenta de GitHub no podría colar una versión **por el actualizador de la app**. Sí podría poner otro instalador en una página de descargas: quien descarga a mano está protegido solo si comprueba la firma (paso 3 de arriba).
- Lo que no evita la firma: quien controlase GitHub podría seguir sirviendo el manifiesto de la versión que ya tienes (o un error), y la app diría que ya tienes la última.
- Nunca se ofrece una versión más vieja que la instalada ni que la más nueva que ya se haya visto, ni una que salte más de una versión principal de golpe (de la 0.x a la 1.x sí; a la 2.0 no).
- Si Luzeira se está ejecutando como administrador, no descarga ni abre ningún instalador.
- **Nunca se instala nada sin que pulses «Actualizar».** Entonces descarga el instalador de esa misma versión, comprueba su tamaño y su SHA-256 contra el manifiesto firmado (y otra vez justo antes de abrirlo), cierra Luzeira, instala encima conservando tus temas y tu configuración, y la vuelve a abrir.
- Si prefieres hacerlo a mano, sigue valiendo: descarga el instalador de Releases y ejecútalo encima.
