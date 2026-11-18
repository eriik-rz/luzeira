"""Los temas que vienen con Visoria tienen que cargar y pintarse siempre (RQ-11)."""

import pytest

from visoria.config import BUILTIN_THEMES
from visoria.render import Renderer
from visoria.sensores.base import Reading
from visoria.temas import load_theme, resolve_font

BUILTIN = sorted(p.name for p in BUILTIN_THEMES.iterdir() if (p / "theme.json").is_file())


def test_builtin_theme_collection():
    assert BUILTIN == [
        "ambar",
        "ambar-vertical",
        "aurora",
        "aurora-vertical",
        "blanco",
        "blanco-vertical",
        "carbono",
        "carbono-vertical",
        "cyberpunk",
        "cyberpunk-vertical",
        "esencial",
        "esencial-claro",
        "esencial-detalle",
        "esencial-vertical",
        "hielo",
        "hielo-vertical",
        "instrumentos",
        "instrumentos-vertical",
        "neon",
        "neon-vertical",
        "reloj",
        "reloj-minimalista",
        "reloj-segundos",
        "reloj-vertical",
        "rgb",
        "rgb-vertical",
        "synthwave",
        "synthwave-vertical",
        "tarjetas",
        "tarjetas-vertical",
        "terminal",
        "terminal-vertical",
    ]


def test_every_style_comes_in_both_orientations():
    # El estilo es lo que va antes del primer guion («synthwave-vertical» → «synthwave»).
    # «estilo» es la versión horizontal y «estilo-vertical», la vertical.
    sizes = {n: load_theme(BUILTIN_THEMES / n).theme.size for n in BUILTIN}
    styles = {n.split("-")[0] for n in BUILTIN}
    landscape = {n.split("-")[0] for n, size in sizes.items() if size == (480, 320)}
    portrait = {n.split("-")[0] for n, size in sizes.items() if size == (320, 480)}
    assert landscape == styles
    assert portrait == styles
    for name, size in sizes.items():
        assert name.endswith("-vertical") == (size == (320, 480)), name


@pytest.mark.parametrize("name", BUILTIN)
def test_builtin_theme_loads_and_renders(name):
    loaded = load_theme(BUILTIN_THEMES / name)
    frame = Renderer(
        loaded.theme,
        font_for=lambda s: resolve_font(s, loaded.files),
        read_sensor=lambda sid: Reading(42.0, "%"),
        files=loaded.files,
    ).full_frame()
    assert frame.size == loaded.theme.size
    assert frame.getbbox() is not None  # no está vacío


@pytest.mark.parametrize("name", BUILTIN)
def test_builtin_theme_only_uses_known_sensors(name):
    # Un id con errata se vería «—» para siempre sin que nadie se entere.
    from visoria.sensores import default_hub

    hub = default_hub()
    try:
        known = {info.id for info in hub.available()}
    finally:
        hub.close()
    theme = load_theme(BUILTIN_THEMES / name).theme
    used = {w.sensor for w in theme.widgets if w.type in ("sensor", "bar")}
    assert used <= known, used - known


# --- Que los textos quepan en su caja ------------------------------------------------------
# Lo peor que puede llegar a cada widget, con la fuente de verdad. Un texto que se sale se
# corta en seco en la pantalla (y el «…» de los nombres largos se pierde).

_WORST_VALUES = {
    "%": [100.0],
    "MB/s": [1188.8],  # red de 10 Gb/s (los discos, aparte: _WORST_BY_SENSOR)
    "GB": [127.9],
    "W": [600.0],
    "MHz": [3200.0],
    "°C": [100.0],
    "h": [2000.8],  # 83 días encendido
}
_WORST_BY_SENSOR = {"disk.read": [7000.9], "disk.write": [7000.9]}  # NVMe PCIe 4.0
_LONG_NAMES = {
    "cpu.name": [
        "AMD Ryzen 7 5700X3D 8-Core Processor",
        "Intel(R) Core(TM) i7-8700 CPU @ 3.20GHz",
        "AMD Ryzen Threadripper PRO 5995WX 64-Cores",
    ],
    "gpu.name": ["NVIDIA GeForce RTX 4070 Ti SUPER", "NVIDIA RTX A6000"],
}


def _sensor_units() -> dict[str, str]:
    from visoria.sensores import default_hub

    hub = default_hub()
    try:
        return {info.id: info.unit for info in hub.available()}
    finally:
        hub.close()


def _worst_texts(widget, units: dict[str, str], font) -> list[str]:
    from itertools import product

    from visoria.render.motor import _MONTHS, _WEEKDAYS, NO_VALUE, format_reading

    if widget.type == "text":
        return [widget.text]
    if widget.type == "clock":
        if widget.format == "date-long":
            # Los 3 días de la semana y los 3 meses más anchos con esta fuente, con todos los
            # días del mes (las 2604 combinaciones tardaban 50 s y no encontraban más).
            def widest(words):
                return sorted(words, key=lambda word: font.getlength(word))[-3:]

            weekdays, months = widest(_WEEKDAYS), widest(_MONTHS)
            return [f"{w}, {d} de {m}" for w, d, m in product(weekdays, range(1, 32), months)]
        return {
            "HH:MM": ["00:00", "08:08", "23:58", "20:44"],
            "HH:MM:SS": ["00:00:00", "08:08:08", "23:58:58", "20:44:44"],
            "date-short": ["28/08/2028", "30/09/2030", "00/00/0000"],
            "weekday": list(_WEEKDAYS),
        }[widget.format]
    texts = [format_reading(widget, Reading(None, ""))]  # sin dato: «—»
    if widget.sensor in _LONG_NAMES:
        return texts + [
            format_reading(widget, Reading(None, "", text=name))
            for name in _LONG_NAMES[widget.sensor]
        ]
    unit = units[widget.sensor]
    worst = _WORST_BY_SENSOR.get(widget.sensor) or _WORST_VALUES.get(unit, [100.0])
    texts += [format_reading(widget, Reading(v, unit)) for v in worst]
    assert NO_VALUE in texts[0]
    return texts


@pytest.mark.parametrize("name", BUILTIN)
def test_builtin_texts_fit_their_box(name):
    from PIL import Image, ImageDraw

    loaded = load_theme(BUILTIN_THEMES / name)
    units = _sensor_units()
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    overflow = []
    for i, widget in enumerate(loaded.theme.widgets):
        if widget.type not in ("text", "clock", "sensor"):
            continue
        font = resolve_font(widget.style, loaded.files)
        w, h = widget.box.width, widget.box.height
        # El mismo anclaje que Renderer._draw_text.
        x, anchor = {"left": (0, "lm"), "center": (w // 2, "mm"), "right": (w, "rm")}[
            widget.style.align
        ]
        for text in _worst_texts(widget, units, font):
            left, top, right, bottom = draw.textbbox((x, h // 2), text, font=font, anchor=anchor)
            # 1 px de margen: el trazo de algunas letras (la «j» de «jueves») asoma 1 px
            # por fuera de su origen; no se nota en la pantalla.
            if min(left, top) < -1 or right > w + 1 or bottom > h + 1:
                overflow.append(f"#{i} {text!r} {(left, top, right, bottom)} en {w}x{h}")
                break
    assert overflow == []


# --- Otras reglas de la colección -----------------------------------------------------------

#: En la v1 (sin admin, Windows) estos sensores existen pero nunca dan dato: un tema incluido
#: que los use enseñaría «—» para siempre.
NO_DATA_IN_V1 = {"cpu.temperature", "cpu.frequency"}


@pytest.mark.parametrize("name", BUILTIN)
def test_builtin_theme_avoids_sensors_without_data_in_v1(name):
    theme = load_theme(BUILTIN_THEMES / name).theme
    used = {w.sensor for w in theme.widgets if w.type in ("sensor", "bar")}
    assert not used & NO_DATA_IN_V1


def test_vertical_titles_follow_their_horizontal_one():
    titles = {n: load_theme(BUILTIN_THEMES / n).theme.name for n in BUILTIN}
    for name in BUILTIN:
        if name.endswith("-vertical"):
            base = name.removesuffix("-vertical")
            assert titles[name] == f"{titles[base]} Vertical", name


def test_every_background_has_exactly_one_generator():
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parent.parent / "scripts" / "fondos.py"
    spec = importlib.util.spec_from_file_location("fondos_test", script)
    fondos = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fondos)
    generated = [(folder, file) for folder, file, _ in [*fondos.FONDOS, *fondos._style_modules()]]
    assert len(generated) == len(set(generated)), "dos generadores escriben el mismo fichero"
    pngs = {(p.parent.name, p.name) for p in BUILTIN_THEMES.glob("*/*.png")}
    assert set(generated) == pngs
