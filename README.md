# Luzeira

Tu pantalla USB de monitorización del PC, en tu idioma y como tú quieras.

### [⬇ Descargar en Microsoft Store](https://apps.microsoft.com/detail/9MSNPHG5NZ40) · [Instalador (GitHub)](https://github.com/eriik-rz/luzeira/releases/latest)

¿Quieres verlo antes? [Mira la web de Luzeira](https://eriik-rz.github.io/luzeira/): ahí puedes probar el editor sin instalar nada. Síguela en [X](https://x.com/eriik_rz_dev), y hay vídeos en [TikTok](https://www.tiktok.com/@eriik_rz.dev) e [Instagram](https://www.instagram.com/eriik_rz.dev/).

Luzeira es una app libre para las pequeñas pantallas USB que se ponen dentro o encima del PC (tipo *Turing Smart Screen* / *UsbPCMonitor*). Muestra la hora, el uso y la frecuencia de la CPU, la GPU y la RAM, la red, el disco y la temperatura de la gráfica (y la de la CPU, con un módulo opcional), con 32 temas incluidos y un editor visual para hacer los tuyos, sin tocar ficheros.

![Algunos de los temas incluidos](guia/temas.png)

## Qué la hace distinta

- **Fácil**: se instala con dos clics, arranca sola con Windows y se maneja desde el icono junto al reloj.
- **En tu idioma**, sin jerga: español, inglés, alemán, francés, italiano, portugués de Brasil, polaco, turco, ruso, chino simplificado y japonés. El chino y el japonés aún no los ha revisado un hablante nativo: si algo suena raro, [avísanos](https://github.com/eriik-rz/luzeira/issues).
- **Sin permisos de administrador**: ni para instalar ni para funcionar.
- **Ligera**: solo envía a la pantalla lo que cambia (unos pocos KB por segundo) y apenas usa CPU.
- **Respetuosa**: apaga la pantalla al apagar o suspender el PC y la vuelve a encender al volver; si desenchufas el cable, se reconecta sola.
- **Editor visual**: arrastra elementos, cambia colores y fondos, mira el resultado exacto antes de aplicarlo.
- **Segura y limpia**: sin anuncios y sin telemetría. Solo se conecta a internet si le dejas, para avisarte de versiones nuevas, y nunca instala nada sin que pulses «Actualizar». Los temas son datos, nunca programas.

## Instalar

**Lo más fácil:** desde [Microsoft Store](https://apps.microsoft.com/detail/9MSNPHG5NZ40). Se instala con un clic, sin avisos de Windows, y se actualiza sola.

O con el instalador de GitHub:

1. Descarga `Luzeira-<versión>-instalador.exe` desde [Releases](https://github.com/eriik-rz/luzeira/releases/latest).
2. Ábrelo. Como Luzeira todavía no va firmada digitalmente, Windows puede mostrar **«Windows protegió su PC»**: pulsa **Más información → Ejecutar de todas formas**. Si quieres comprobar que la descarga es la oficial, mira [cómo verificarla](SECURITY.md#cómo-verificar-que-tu-descarga-es-oficial).
3. Sigue el asistente. Al terminar, Luzeira aparece junto al reloj y abre el editor para que veas lo que puedes hacer.

¿Prefieres no instalar nada? Descarga el `.zip` **portable**, descomprímelo donde quieras y abre `Luzeira.cmd`.

Para desinstalar: *Configuración → Aplicaciones → Luzeira*. Te preguntará si quieres conservar tus temas (por defecto, sí).

## Requisitos

- Windows 10 u 11 de 64 bits.
- El componente **Microsoft Edge WebView2** para el editor (viene con Windows 11 y con Edge; si falta, el instalador te lo dice).
- **La gráfica:** con NVIDIA salen todos sus datos. Con AMD o Intel, el uso y la memoria siempre. La temperatura, el ventilador y la frecuencia salen si el controlador los da, y el consumo no sale. AMD e Intel están en pruebas: si algo sale raro, [avísanos](https://github.com/eriik-rz/luzeira/issues).

## Temperatura de la CPU (opcional)

Windows no deja leer la temperatura de la CPU sin permisos de administrador, y Luzeira nunca los pide. Por eso va en un módulo aparte, **Luzeira Sensores** (beta), que se instala solo si quieres:

1. Instala el driver [PawnIO](https://pawnio.eu/), versión 2.2.0 o posterior.
2. Descarga `LuzeiraSensores-<versión>-setup.exe` desde [Releases](https://github.com/eriik-rz/luzeira/releases/latest) y ábrelo. Pide permiso de administrador una vez.
3. En el editor, añade el sensor «Temperatura de CPU» a tu tema. También están el consumo y la tensión de la CPU.

El módulo es un servicio de Windows que solo lee los sensores de la CPU y deja los valores en un fichero que Luzeira lee sin permisos. No tiene red ni acepta órdenes. Sin él, Luzeira funciona igual y esos sensores muestran «—». Está probado en un Ryzen 7 5700X3D. En Intel y en otros AMD, está en pruebas.

## Pantallas soportadas

| Pantalla | Estado |
|---|---|
| Turing Smart Screen 3,5" / UsbPCMonitor 3,5" (revisión A, USB `1A86:5722`) | ✅ Probada |
| XuanFang 3,5" (también «flagship», revisión B) | Beta |
| Kipye Qiye 3,5" | Beta |
| Turing Smart Screen 2,1", 5" y 8,8" (revisión C) | Beta |
| WeAct 3,5" y 0,96" | Beta |
| Turing Smart Screen de la primera versión (USB `1CBE`) | No: necesitan otro driver de Windows |

«Beta» quiere decir que Luzeira las reconoce y habla su protocolo, pero nadie las ha probado todavía con el aparato de verdad. Si tienes una, [cuéntanos cómo va](https://github.com/eriik-rz/luzeira/issues). En una pantalla de otro tamaño, los temas se ven escalados y centrados.

## Licencia

[GPL-3.0-or-later](LICENSE). Puedes usarla, estudiarla, modificarla y compartirla; las versiones modificadas que distribuyas deben seguir siendo libres.

El nombre «Luzeira» identifica la versión oficial: las copias modificadas deben usar otro nombre.

## Créditos

- Fuente [Inter](https://github.com/rsms/inter) 4.1, de The Inter Project Authors, con licencia [SIL Open Font License 1.1](src/luzeira/recursos/fuentes/OFL.txt).
- Las licencias de todo lo que va dentro del instalador están en `TERCEROS.txt`, junto a la app.

## Seguridad

¿Has encontrado un fallo de seguridad? Lee [SECURITY.md](SECURITY.md) y repórtalo en privado.
