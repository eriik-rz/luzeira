"""Línea de órdenes provisional de Luzeira (hasta que exista la app con bandeja).

uv run luzeira detectar   -> dice qué pantalla hay en cada puerto (sin abrirlos)
uv run luzeira demo       -> pinta una pantalla de prueba y mide la velocidad
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw, ImageFont

from luzeira import __version__, idioma
from luzeira.idioma import t
from luzeira.pantallas import DisplayError, Orientation, TuringRevA

if TYPE_CHECKING:
    from luzeira.pantallas.modelos import Identification, PortInfo

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
        print(t("cli.detectar_ninguna"))
        return 1
    for port, ident in found:
        print(f"{port.device}: {_detect_line(ident, port)}")
    return 0


def _detect_line(ident: Identification, port: PortInfo) -> str:
    """Una línea por pantalla. Dice lo mismo que hará la detección sola
    (``pantallas.apertura.open_auto``): la rev C y la WeAct nunca se abren solas.

    - Una familia sin driver propio (rev C, WeAct) cuyos tamaños sí se ofrecen: con el
      tamaño si el serie lo dice (se propone y el usuario confirma); si no, que lo elija.
    - Una WeAct con su prefijo de serie: se propone, no se abre sola (beta, USB y serie
      sacados solo de código ajeno).
    """
    from luzeira.pantallas.modelos import WEACT_MODELS, compatible_models, rev_c_size

    model = ident.model
    if model is None:
        return t("cli.detectar_rev_a_o_b")
    if not model.supported:
        if not compatible_models(port):
            return t("cli.detectar_no_compatible", model=model.label)
        size = rev_c_size(port)
        if size is not None:
            return t("cli.detectar_beta_confirmar", model=size.label)
        return t("cli.detectar_elegir_tamano", model=model.label)
    if model in WEACT_MODELS:
        return t("cli.detectar_beta_confirmar", model=model.label)
    if ident.sure:
        key = "cli.detectar_segura_beta" if model.beta else "cli.detectar_segura"
        return t(key, model=model.label)
    return t("cli.detectar_parece", model=model.label)


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
    hello, works = t("cli.demo_hola"), t("cli.demo_funciona")
    draw.text((width // 2, 95), hello, font=_font(34, bold=True), fill="white", anchor="mm")
    draw.text((width // 2, 135), works, font=_font(20), fill="white", anchor="mm")
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
        print(
            t(
                "cli.demo_conectado",
                model=display.model.name,
                width=display.width,
                height=display.height,
            )
        )

        start = time.perf_counter()
        display.draw_image(_demo_image(display.width, display.height))
        full = time.perf_counter() - start
        print(t("cli.demo_completa", seconds=f"{full:.2f}", fps=f"{1 / full:.1f}"))

        # Reloj: solo se repinta una franja, como hará la app de verdad.
        print(t("cli.demo_reloj_ctrl_c") if seconds <= 0 else t("cli.demo_reloj"))
        times = []
        y = display.height - CLOCK_HEIGHT
        end = time.monotonic() + seconds if seconds > 0 else float("inf")
        try:
            while time.monotonic() < end:
                started = time.perf_counter()
                display.draw_image(_clock_image(display.width), 0, y)
                times.append(time.perf_counter() - started)
                time.sleep(max(0.0, 1.0 - times[-1]))
        except KeyboardInterrupt:
            pass
        if times:
            avg = sum(times) / len(times)
            print(
                t(
                    "cli.demo_franja",
                    width=display.width,
                    height=CLOCK_HEIGHT,
                    ms=f"{avg * 1000:.0f}",
                )
            )
    return 0


def _run(args: argparse.Namespace) -> int:
    from luzeira import app
    from luzeira.temas import ThemeError

    print(t("cli.ejecutar_ctrl_c") if args.segundos <= 0 else t("cli.ejecutar"))
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
            t(
                "cli.ejecutar_estadisticas",
                seconds=f"{stats.seconds:.0f}",
                patches=stats.patches,
                kbps=f"{stats.bytes_sent / stats.seconds / 1000:.1f}",
                cpu=f"{stats.cpu_percent_total:.2f}",
            )
        )
    return 0


# Lo que tiene la página de nuestro Vite (``editor/index.html``) y no la de otro.
VITE_MARKER = b"<title>Luzeira"


def _vite_port_state(origin: str, timeout: float = 3.0) -> str:
    """¿Quién hay en el puerto de Vite? ``"free"``, ``"ours"`` (el Vite del editor) u
    ``"other"`` (otro programa: p. ej. el Vite de otro proyecto, que también usa 5173).

    Primero se intenta ocupar el puerto un instante: si se puede, está libre (sin
    conectar a nadie). Si no, se pide la página y se mira si es la del editor.

    ⚠ Solo mira **una vez, al arrancar**. El flujo de ``vite.config.ts`` arranca Vite
    *después* (necesita el puerto de la API): si en ese momento el 5173 está libre y
    luego lo coge otro programa antes que nuestro Vite (que con ``strictPort`` no
    arranca), la dirección con el token que ya se imprimió acabaría en ese programa.
    Corta el caso habitual (el otro Vite ya abierto), no ese.
    """
    import http.client
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(origin)
    host, port = parts.hostname or "127.0.0.1", parts.port or 80
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind((host, port))
        return "free"
    except OSError:
        pass
    finally:
        probe.close()
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/", headers={"Accept": "text/html"})
        page = conn.getresponse().read(256 * 1024)
    except (OSError, http.client.HTTPException):
        return "other"
    finally:
        conn.close()
    return "ours" if VITE_MARKER in page else "other"


def _editor(dev: bool) -> int:
    """Editor sin bandeja: para probarlo o para trabajar en la interfaz."""
    from luzeira.config import load_config, save_config
    from luzeira.editor.conexion import EditorLink
    from luzeira.editor.ventana import EXIT_NO_WEBVIEW2

    def apply_theme(name: str) -> None:  # sin bandeja: solo se recuerda para la próxima vez
        save_config(load_config().model_copy(update={"theme": name}))

    exit_codes: list[int] = []
    vite = DEV_ORIGIN
    if dev and _vite_port_state(vite) == "other":
        # La dirección con el token se abriría en la página de ese otro programa, que
        # podría leerlo (el fragmento «#…» lo ve el JavaScript de la página) y, con el
        # CORS abierto a ese origen, usar la API. No se arranca nada.
        print(t("cli.editor_puerto_ocupado", url=vite))
        return 1
    link = EditorLink(
        apply_theme=apply_theme,
        status=lambda: ("stopped", t("cli.editor_modo")),
        active_theme=lambda: load_config().theme,
        dev_origin=vite if dev else None,
        on_window_exit=exit_codes.append,
    )
    try:
        server = link.server()
        if dev:
            # El token va en el fragmento (#…): el navegador no lo envía a ningún servidor.
            print(t("cli.editor_desarrollo", url=f"{vite}/#token={server.token}"))
            print(t("cli.editor_api", url=server.url))
            while True:
                time.sleep(1)
        if not link.is_built():
            print(t("cli.editor_sin_compilar"))
            return 1
        link.open_window()
        while link.window.is_open:
            time.sleep(0.5)
        time.sleep(0.2)  # que llegue el aviso de cierre (código de salida)
        if EXIT_NO_WEBVIEW2 in exit_codes:
            print(t("bandeja.sin_webview2"))
            return 1
    except KeyboardInterrupt:
        pass
    finally:
        link.close()
    return 0


def _saved_language() -> str | None:
    """El idioma que eligió el usuario (``None``: el de Windows). Sin configuración que
    leer, el de Windows: la línea de órdenes no debe fallar por esto."""
    # Solo se lee: ``load_config`` apartaría un fichero dañado, y eso le toca a la bandeja
    # (con su log), no a una ayuda de la línea de órdenes ni al proceso de la ventana.
    from luzeira.config import config_dir

    try:
        data = json.loads((config_dir() / "config.json").read_text(encoding="utf-8-sig"))
        code = data.get("language") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None
    return code if isinstance(code, str) and code in idioma.available() else None


def main(argv: list[str] | None = None) -> int:
    # Antes de la ayuda de argparse, que ya sale traducida. La bandeja y la ventana del
    # editor vuelven a fijarlo luego con lo suyo.
    args_in = sys.argv[1:] if argv is None else argv
    # La ventana del editor recibe el idioma por stdin: no lee la configuración.
    if args_in[:1] != ["editor-ventana"]:
        idioma.set_language(_saved_language())
    parser = argparse.ArgumentParser(prog="luzeira", description=t("cli.descripcion"))
    # metavar fijo: así la orden interna «editor-ventana» no sale en la ayuda (con
    # help=SUPPRESS argparse seguía listándola como «==SUPPRESS==»).
    parser.add_argument(
        "--abrir-editor",
        action="store_true",
        help=t("cli.ayuda_abrir_editor"),
    )
    sub = parser.add_subparsers(
        dest="cmd", metavar="{detectar,demo,ejecutar,editor}"
    )  # sin orden: abre la bandeja
    sub.add_parser("detectar", help=t("cli.ayuda_detectar"))
    demo = sub.add_parser("demo", help=t("cli.ayuda_demo"))
    demo.add_argument("--segundos", type=int, default=0, help=t("cli.ayuda_demo_segundos"))
    demo.add_argument("--orientacion", choices=ORIENTATIONS, default="vertical")
    run_p = sub.add_parser("ejecutar", help=t("cli.ayuda_ejecutar"))
    run_p.add_argument("--tema", type=Path, default=None, help=t("cli.ayuda_tema"))
    run_p.add_argument("--girar", action="store_true", help=t("cli.ayuda_girar"))
    run_p.add_argument("--brillo", type=int, default=60, choices=range(0, 101), metavar="0-100")
    run_p.add_argument("--segundos", type=int, default=0, help=t("cli.ayuda_segundos"))
    ed = sub.add_parser("editor", help=t("cli.ayuda_editor"))
    ed.add_argument(
        "--desarrollo",
        action="store_true",
        help=t("cli.ayuda_desarrollo"),
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
