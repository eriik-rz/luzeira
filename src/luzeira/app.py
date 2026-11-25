"""Bucle de medición para `luzeira ejecutar`: tema + sensores + render + pantalla.

La app de verdad usa ``servicio.DisplayService`` (bandeja). Este bucle se queda solo
porque devuelve estadísticas (KB/s, CPU) para medir RQ-03/RQ-04; si algún día
``DisplayService`` las da, se borra este fichero (C-26).

Provisional hasta la fase 4 (bandeja, autoarranque, reposo y reconexión). Lo que ya
cumple: solo envía lo que cambia (RQ-03) y apaga la pantalla al salir (RQ-07).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path

import psutil

from luzeira.config import BUILTIN_THEMES
from luzeira.config import DEFAULT_THEME as CONFIG_DEFAULT_THEME
from luzeira.pantallas import DisplayError, TuringRevA
from luzeira.render import Renderer
from luzeira.sensores import default_hub
from luzeira.servicio import FLIPPED, ORIENTATIONS, display_size
from luzeira.temas import load_theme, resolve_font

log = logging.getLogger(__name__)

DEFAULT_THEME = BUILTIN_THEMES / CONFIG_DEFAULT_THEME  # carpeta del tema por defecto


@dataclass
class RunStats:
    seconds: float = 0.0
    ticks: int = 0
    patches: int = 0
    bytes_sent: int = 0
    cpu_percent_total: float | None = None  # % del equipo entero (RQ-04)


def run(
    theme_dir: Path = DEFAULT_THEME,
    *,
    flip: bool = False,
    brightness: int = 60,
    seconds: float = 0,
) -> RunStats:
    """Muestra el tema hasta Ctrl+C (o ``seconds`` si > 0) y devuelve estadísticas."""
    loaded = load_theme(theme_dir)
    theme = loaded.theme
    hub = default_hub()
    try:
        hub.poll()
        renderer = Renderer(
            theme,
            font_for=lambda style: resolve_font(style, loaded.files),
            read_sensor=hub.read,
            files=loaded.files,
        )
    except BaseException:
        hub.close()  # no dejar NVML abierto si el tema no se puede preparar
        raise
    orientation = ORIENTATIONS[theme.orientation]
    if flip:
        orientation = FLIPPED[orientation]

    stats = RunStats()
    process = psutil.Process()
    start = time.monotonic()
    try:
        display = TuringRevA.open()
    except BaseException:
        hub.close()  # sin pantalla no hay nada que mostrar: soltar NVML y salir
        raise
    try:
        display.screen_on()
        display.set_brightness(brightness)
        display.set_orientation(orientation)
        # En una pantalla de otro tamaño (5", 7"...) el tema se pinta escalado y centrado.
        size = display_size(display)
        if size is not None:
            renderer.set_output_size(size)
        time.sleep(1.0)  # que las velocidades (red, disco, CPU) tengan un intervalo real
        hub.poll()
        display.draw_image(renderer.full_frame())

        process.cpu_percent(None)
        start = time.monotonic()
        end = start + seconds if seconds > 0 else float("inf")
        next_tick = start + 1.0
        while time.monotonic() < end:
            time.sleep(max(0.0, next_tick - time.monotonic()))
            next_tick += 1.0
            if next_tick < time.monotonic():
                # Nos hemos quedado atrás (PC suspendido o envío lento): no hacer una
                # ráfaga de ticks para «ponerse al día», seguir desde ahora.
                next_tick = time.monotonic() + 1.0
            hub.poll()
            for patch in renderer.changed():
                display.draw_image(patch.image, patch.x, patch.y)
                stats.patches += 1
                stats.bytes_sent += patch.image.width * patch.image.height * 2
            stats.ticks += 1
    except KeyboardInterrupt:
        pass
    finally:
        stats.seconds = time.monotonic() - start
        if stats.ticks:
            stats.cpu_percent_total = process.cpu_percent(None) / psutil.cpu_count()
        try:
            display.screen_off()  # RQ-07: no dejar la pantalla encendida con datos viejos
        except DisplayError:
            # Si el USB ya no está, no se puede apagar; que no tape el error original.
            log.warning("No se pudo apagar la pantalla al salir.")
        finally:
            try:
                display.close()
            finally:
                hub.close()
    return stats
