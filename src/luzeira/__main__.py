"""Línea de órdenes provisional de Luzeira (hasta que exista la app con bandeja).

uv run luzeira detectar   -> dice qué pantalla hay en cada puerto (sin abrirlos)
uv run luzeira demo       -> pinta una pantalla de prueba y mide la velocidad
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from luzeira import __version__
from luzeira.pantallas import DisplayError, Orientation, TuringRevA

if TYPE_CHECKING:
    from luzeira.pantallas.modelos import Identification

# Vite del editor en desarrollo: escucha solo en 127.0.0.1 (contrato de la fase 5). Con
# «localhost» el navegador manda Origin «http://localhost:5173» y el servidor lo rechaza.
DEV_ORIGIN = "http://127.0.0.1:5173"


def _detect(ports: Iterable | None = None) -> int:
    """Dice qué pantalla hay en cada puerto, **sin abrir ninguno**: solo con lo que Windows
    ya sabe del USB (``pantallas.modelos.identify``). ``ports``: para los tests."""
    from luzeira.pantallas.modelos import identify, list_ports

    found = [(port, identify(port)) for port in list_ports(ports)]
    found = [(port, ident) for port, ident in found if ident is not None]
    if not found:
        print("No encuentro ninguna pantalla compatible conectada.")
        return 1
    for port, ident in found:
        print(f"{port.device}: {_detect_line(ident)}")
    return 0


def _detect_line(ident: Identification) -> str:
    model = ident.model
    if model is None:
        return (
            'una pantalla Turing / UsbPCMonitor 3,5" (revisión A) o una XuanFang 3,5" (beta): '
            "su USB es el de las dos y no trae número de serie conocido. No se le enviará "
            "nada sin tu confirmación."
        )
    if not model.supported:
        return f"{model.label}, todavía no compatible. No se le enviará nada."
    if ident.sure:
        beta = " (beta: todavía sin probar con una pantalla de verdad)" if model.beta else ""
        return f"{model.label}{beta}. Reconocida por su número de serie."
    return (
        f"parece una {model.label} (beta), pero solo por su USB: no es seguro. "
        "No se le enviará nada sin tu confirmación."
    )


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Fuente con todos los caracteres del español (¡, ñ, tildes).

    Provisional: Segoe UI de Windows. La fuente por defecto de Pillow no tiene «¡» y
    sale un cuadro de «carácter no encontrado». La app incluirá una fuente libre propia.
    """
    for name in ("segoeuib.ttf", "seguisb.ttf") if bold else ("segoeui.ttf",):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


CLOCK_HEIGHT = 80
ORIENTATIONS = {
    "vertical": Orientation.PORTRAIT,
    "vertical-invertida": Orientation.REVERSE_PORTRAIT,
    "horizontal": Orientation.LANDSCAPE,
    "horizontal-invertida": Orientation.REVERSE_LANDSCAPE,
}


def _demo_image(width: int, height: int) -> Image.Image:
    """Pantalla de prueba. Deja libre abajo la franja del reloj."""
    img = Image.new("RGB", (width, height), (12, 16, 28))
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width, 60), fill=(255, 140, 0))
    draw.text((width // 2, 30), "Luzeira", font=_font(32, bold=True), fill="black", anchor="mm")
    draw.text((width - 8, 52), f"v{__version__}", font=_font(14), fill=(60, 30, 0), anchor="rs")
    draw.text((width // 2, 95), "¡Hola!", font=_font(34, bold=True), fill="white", anchor="mm")
    draw.text((width // 2, 135), "Tu pantalla funciona.", font=_font(20), fill="white", anchor="mm")
    # Barras de color para comprobar que rojo, verde y azul salen bien.
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255), (128, 128, 128)]
    bar = width // len(colors)
    for i, color in enumerate(colors):
        draw.rectangle((i * bar, 160, (i + 1) * bar - 1, 200), fill=color)
    return img


def _clock_image(width: int) -> Image.Image:
    img = Image.new("RGB", (width, CLOCK_HEIGHT), (12, 16, 28))
    draw = ImageDraw.Draw(img)
    draw.text(
        (width // 2, CLOCK_HEIGHT // 2),
        datetime.now().strftime("%H:%M:%S"),
        font=_font(52, bold=True),
        fill=(255, 140, 0),
        anchor="mm",
    )
    return img


def _demo(seconds: int, orientation: Orientation) -> int:
    try:
        display = TuringRevA.open()
    except DisplayError as exc:
        print(exc)
        return 1
    with display:
        display.screen_on()
        display.set_brightness(60)
        display.set_orientation(orientation)
        print(f"Conectado: {display.model.name} ({display.width}x{display.height}).")

        start = time.perf_counter()
        display.draw_image(_demo_image(display.width, display.height))
        full = time.perf_counter() - start
        print(f"Pantalla completa: {full:.2f} s ({1 / full:.1f} fps).")

        # Reloj: solo se repinta una franja, como hará la app de verdad.
        print("Reloj en marcha. Ctrl+C para parar." if seconds <= 0 else "Reloj en marcha.")
        times = []
        y = display.height - CLOCK_HEIGHT
        end = time.monotonic() + seconds if seconds > 0 else float("inf")
        try:
            while time.monotonic() < end:
                t = time.perf_counter()
                display.draw_image(_clock_image(display.width), 0, y)
                times.append(time.perf_counter() - t)
                time.sleep(max(0.0, 1.0 - times[-1]))
        except KeyboardInterrupt:
            pass
        if times:
            avg = sum(times) / len(times)
            print(f"Franja de reloj {display.width}x{CLOCK_HEIGHT}: {avg * 1000:.0f} ms de media.")
    return 0


def _run(args: argparse.Namespace) -> int:
    from luzeira import app
    from luzeira.temas import ThemeError

    print("Mostrando el tema. Ctrl+C para parar." if args.segundos <= 0 else "Mostrando el tema.")
    try:
        stats = app.run(
            args.tema or app.DEFAULT_THEME,
            flip=args.girar,
            brightness=args.brillo,
            seconds=args.segundos,
        )
    except (DisplayError, ThemeError) as exc:
        print(exc)
        return 1
    if stats.ticks:
        print(
            f"{stats.seconds:.0f} s · {stats.patches} trozos enviados · "
            f"{stats.bytes_sent / stats.seconds / 1000:.1f} KB/s de media · "
            f"CPU de la app: {stats.cpu_percent_total:.2f} % del equipo"
        )
    return 0


def _editor(dev: bool) -> int:
    """Editor sin bandeja: para probarlo o para trabajar en la interfaz."""
    from luzeira.config import load_config, save_config
    from luzeira.editor.conexion import EditorLink
    from luzeira.editor.ventana import EXIT_NO_WEBVIEW2

    def apply_theme(name: str) -> None:  # sin bandeja: solo se recuerda para la próxima vez
        save_config(load_config().model_copy(update={"theme": name}))

    exit_codes: list[int] = []
    vite = DEV_ORIGIN
    link = EditorLink(
        apply_theme=apply_theme,
        status=lambda: ("stopped", "Modo editor: la pantalla no se usa ahora."),
        active_theme=lambda: load_config().theme,
        dev_origin=vite if dev else None,
        on_window_exit=exit_codes.append,
    )
    try:
        server = link.server()
        if dev:
            # El token va en el fragmento (#…): el navegador no lo envía a ningún servidor.
            print(f"Editor en desarrollo: abre {vite}/#token={server.token}")
            print(f"(API en {server.url}). Ctrl+C para parar.")
            while True:
                time.sleep(1)
        if not link.is_built():
            print("El editor no está compilado: ejecuta «npm run build» en editor/.")
            return 1
        link.open_window()
        while link.window.is_open:
            time.sleep(0.5)
        time.sleep(0.2)  # que llegue el aviso de cierre (código de salida)
        if EXIT_NO_WEBVIEW2 in exit_codes:
            print(
                "Para el editor hace falta Microsoft Edge WebView2 Runtime (gratuito de "
                "Microsoft). Instálalo y vuelve a abrir el editor."
            )
            return 1
    except KeyboardInterrupt:
        pass
    finally:
        link.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="luzeira", description="Luzeira (versión de pruebas)")
    # metavar fijo: así la orden interna «editor-ventana» no sale en la ayuda (con
    # help=SUPPRESS argparse seguía listándola como «==SUPPRESS==»).
    parser.add_argument(
        "--abrir-editor",
        action="store_true",
        help="abre también el editor de temas (lo usa el instalador al terminar)",
    )
    sub = parser.add_subparsers(
        dest="cmd", metavar="{detectar,demo,ejecutar,editor}"
    )  # sin orden: abre la bandeja
    sub.add_parser("detectar", help="busca la pantalla")
    demo = sub.add_parser("demo", help="pinta una pantalla de prueba")
    demo.add_argument(
        "--segundos", type=int, default=0, help="cuánto rato mostrar el reloj (0 = hasta Ctrl+C)"
    )
    demo.add_argument("--orientacion", choices=ORIENTATIONS, default="vertical")
    run_p = sub.add_parser("ejecutar", help="muestra un tema con datos reales")
    run_p.add_argument("--tema", type=Path, default=None, help="carpeta del tema")
    run_p.add_argument("--girar", action="store_true", help="girar 180° (pantalla del revés)")
    run_p.add_argument("--brillo", type=int, default=60, choices=range(0, 101), metavar="0-100")
    run_p.add_argument("--segundos", type=int, default=0, help="0 = hasta Ctrl+C")
    ed = sub.add_parser("editor", help="abre el editor de temas sin la bandeja")
    ed.add_argument(
        "--desarrollo",
        action="store_true",
        help="no abre ventana: imprime la dirección para «npm run dev» (Vite, puerto 5173)",
    )
    # Interna: el proceso de la ventana del editor (lo lanza la bandeja; ver editor/ventana.py).
    sub.add_parser("editor-ventana")
    args = parser.parse_args(argv)
    # Antes de abrir ninguna ventana: que Windows la agrupe y la ancle como «Luzeira».
    from luzeira.sistema.identidad import set_process_app_id

    set_process_app_id()
    if args.cmd == "editor-ventana":
        from luzeira.editor.ventana import run_window_from_stdin

        return run_window_from_stdin()
    if args.cmd == "editor":
        return _editor(args.desarrollo)
    if args.cmd is None:
        from luzeira import bandeja

        return bandeja.main(open_editor=args.abrir_editor)
    if args.cmd == "detectar":
        return _detect()
    if args.cmd == "ejecutar":
        return _run(args)
    return _demo(args.segundos, ORIENTATIONS[args.orientacion])


if __name__ == "__main__":
    sys.exit(main())
