"""Los temas que vienen con Visoria tienen que cargar y pintarse siempre (RQ-11)."""

import pytest

from visoria.config import BUILTIN_THEMES
from visoria.render import Renderer
from visoria.sensores.base import Reading
from visoria.temas import load_theme, resolve_font

BUILTIN = sorted(p.name for p in BUILTIN_THEMES.iterdir() if (p / "theme.json").is_file())


def test_builtin_theme_collection():
    assert BUILTIN == [
        "esencial",
        "esencial-claro",
        "esencial-detalle",
        "esencial-vertical",
        "instrumentos",
        "instrumentos-vertical",
        "neon",
        "neon-hielo",
        "neon-synthwave",
        "neon-terminal",
        "reloj",
        "reloj-minimal",
        "reloj-segundos",
        "reloj-vertical",
        "tarjetas",
        "tarjetas-vertical",
    ]


def test_every_family_has_a_portrait_theme():
    # La familia es lo que va antes del primer guion («neon-hielo» → «neon»).
    families = {n.split("-")[0] for n in BUILTIN}
    portrait = {
        n.split("-")[0] for n in BUILTIN if load_theme(BUILTIN_THEMES / n).theme.size == (320, 480)
    }
    assert portrait == families


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
