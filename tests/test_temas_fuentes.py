"""Tests de ``resolve_font`` (orden de búsqueda y caché) y ``missing_glyphs`` (RQ-16)."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from PIL import ImageFont
from pydantic import ValidationError

from luzeira.temas import fuentes, load_theme, missing_glyphs, resolve_font
from luzeira.temas.modelo import Style

FONTS_DIR = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
SEGOE = FONTS_DIR / "segoeui.ttf"
SEGOE_BOLD = FONTS_DIR / "segoeuib.ttf"
needs_segoe = pytest.mark.skipif(
    not (SEGOE.is_file() and SEGOE_BOLD.is_file()), reason="Segoe UI solo está en Windows"
)
SPANISH = "¡¿ñÑáéíóúü€°"


@pytest.fixture
def no_bundled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # La carpeta de fuentes incluidas aún no existe; se fuerza para no depender de ello.
    monkeypatch.setattr(fuentes, "BUNDLED_DIR", tmp_path / "no-existe")


def font_path(font: object) -> Path:
    return Path(font.path)  # type: ignore[attr-defined]


# --- missing_glyphs --------------------------------------------------------------------


def test_default_font_lacks_inverted_exclamation() -> None:
    # Se vio en la pantalla real: «¡» salía como un cuadro con la fuente por defecto.
    font = ImageFont.load_default(20)
    missing = missing_glyphs(font, "¡Hola!")
    assert missing == ["¡"]


def test_default_font_lacks_all_spanish_letters() -> None:
    # Más grave de lo que parecía: a Aileron (la de Pillow) le falta TODO lo español.
    font = ImageFont.load_default(20)
    # Solo «°» está; «¡¿ñÑáéíóúü€» faltan todos.
    assert missing_glyphs(font, SPANISH) == list("¡¿ñÑáéíóúü€")


@needs_segoe
def test_segoe_has_all_spanish_characters() -> None:
    font = ImageFont.truetype(str(SEGOE), 20)
    assert missing_glyphs(font, SPANISH + " ABCxyz0123456789%") == []


@needs_segoe
def test_segoe_lacks_cjk_and_reports_each_once() -> None:
    # Comprueba que la función sí detecta en Segoe (no devuelve siempre []).
    font = ImageFont.truetype(str(SEGOE), 20)
    assert missing_glyphs(font, "漢a漢") == ["漢"]


def test_control_characters_are_ignored() -> None:
    font = ImageFont.load_default(20)
    assert missing_glyphs(font, "a\nb\tc") == []


# --- resolve_font ----------------------------------------------------------------------


def theme_with_font(folder: Path, rel: str = "fuentes/mia.ttf", size: int = 18) -> Path:
    """Tema real en disco con una fuente (copia de Segoe UI Bold) en ``rel``."""
    folder.mkdir(parents=True, exist_ok=True)
    widget = {
        "type": "text",
        "text": "hola",
        "box": {"x": 0, "y": 0, "width": 100, "height": 30},
        "style": {"font": rel, "size": size},
    }
    theme = {"format": "luzeira-theme/1", "name": "F", "orientation": "portrait"}
    theme["widgets"] = [widget]
    (folder / "theme.json").write_text(json.dumps(theme), encoding="utf-8")
    (folder / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(SEGOE_BOLD, folder / rel)
    return folder


@needs_segoe
def test_theme_font_wins(tmp_path: Path, no_bundled: None) -> None:
    loaded = load_theme(theme_with_font(tmp_path / "t"))
    style = loaded.theme.widgets[0].style
    font = resolve_font(style, loaded.files)
    assert font_path(font) == loaded.files["fuentes/mia.ttf"]
    assert font.size == 18  # type: ignore[union-attr]


@needs_segoe
def test_theme_font_path_comes_only_from_validated_files(tmp_path: Path, no_bundled: None) -> None:
    # La ruta sale de ``files``, nunca de unir carpeta + style.font: una fuente que
    # exista en disco con ese nombre pero no esté en ``files`` no se usa…
    folder = theme_with_font(tmp_path / "t")
    style = Style(font="fuentes/mia.ttf")
    assert font_path(resolve_font(style, {})).name.lower() == "segoeui.ttf"
    assert font_path(resolve_font(style, None)).name.lower() == "segoeui.ttf"
    # …y la que esté en ``files`` se usa tal cual, aunque viva en otro sitio.
    elsewhere = tmp_path / "validada" / "otra.ttf"
    elsewhere.parent.mkdir()
    shutil.copy(SEGOE_BOLD, elsewhere)
    assert (folder / "fuentes" / "mia.ttf").is_file()
    assert font_path(resolve_font(style, {"fuentes/mia.ttf": elsewhere})) == elsewhere


@needs_segoe
def test_theme_font_swapped_after_load_is_not_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_bundled: None
) -> None:
    # TOCTOU: tras load_theme alguien cambia la fuente por otra cosa. Antes de abrirla
    # se repite la firma. Se espía _load porque, sin la defensa, FreeType también la
    # rechazaría y el resultado visible sería el mismo.
    loaded = load_theme(theme_with_font(tmp_path / "t"))
    swapped = loaded.files["fuentes/mia.ttf"]
    swapped.write_bytes(b"MZ esto ya no es una fuente" + b"x" * 100)
    opened: list[str | None] = []
    real_load = fuentes._load

    def spy(path: str | None, size: int):  # type: ignore[no-untyped-def]
        opened.append(path)
        return real_load(path, size)

    monkeypatch.setattr(fuentes, "_load", spy)
    font = resolve_font(loaded.theme.widgets[0].style, loaded.files)
    assert font_path(font).name.lower() == "segoeui.ttf"  # cae a la del sistema
    assert str(swapped) not in opened


@needs_segoe
def test_missing_theme_font_falls_back_to_segoe(tmp_path: Path, no_bundled: None) -> None:
    files = {"no-esta.ttf": tmp_path / "no-esta.ttf"}
    font = resolve_font(Style(font="no-esta.ttf"), files)
    assert font_path(font).name.lower() == "segoeui.ttf"


@needs_segoe
def test_reserved_theme_font_is_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_bundled: None
) -> None:
    # «NUL.ttf» abriría el dispositivo NUL de Windows: se salta sin tocar el disco.
    # Se espía el disco porque, sin la defensa, el resultado visible sería el mismo.
    real_is_file = Path.is_file
    real_resolve = Path.resolve

    def guard(name: str, real):  # type: ignore[no-untyped-def]
        def spy(self: Path, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
            assert not self.name.upper().startswith("NUL"), f"{name} sobre {self}"
            return real(self, *args, **kwargs)

        return spy

    monkeypatch.setattr(Path, "is_file", guard("is_file", real_is_file))
    monkeypatch.setattr(Path, "resolve", guard("resolve", real_resolve))
    # Desde la fase 3 el modelo ya rechaza «NUL.ttf» (primera capa) y load_theme nunca
    # lo pondría en ``files``. Aquí se prueba la última capa (resolve_font) con un
    # estilo sin validar y un ``files`` hecho a mano.
    files = {"NUL.ttf": tmp_path / "NUL.ttf"}
    font = resolve_font(Style.model_construct(font="NUL.ttf"), files)
    assert font_path(font).name.lower() == "segoeui.ttf"


@pytest.mark.parametrize(
    "path", ["NUL.ttf", "con.png", "aux/fuente.ttf", "COM3.otf", "fuente.svg", "fuente.exe"]
)
def test_model_rejects_reserved_names_and_bad_font_extensions(path: str) -> None:
    with pytest.raises(ValidationError):
        Style(font=path)


@needs_segoe
def test_bold_uses_segoe_bold(no_bundled: None) -> None:
    assert font_path(resolve_font(Style(bold=True), None)).name.lower() == "segoeuib.ttf"
    assert font_path(resolve_font(Style(bold=False), None)).name.lower() == "segoeui.ttf"


@needs_segoe
def test_bundled_font_before_system(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundled = tmp_path / "recursos"
    bundled.mkdir()
    shutil.copy(SEGOE_BOLD, bundled / fuentes.BUNDLED_REGULAR)
    monkeypatch.setattr(fuentes, "BUNDLED_DIR", bundled)
    assert font_path(resolve_font(Style(), None)) == bundled / fuentes.BUNDLED_REGULAR


def test_no_fonts_anywhere_uses_pillow_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_bundled: None
) -> None:
    monkeypatch.setenv("WINDIR", str(tmp_path / "sin-windows"))
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    font = resolve_font(Style(size=33), None)
    assert font.getname() == ("Aileron", "Regular")  # type: ignore[union-attr]
    assert font.size == 33  # type: ignore[union-attr]


@needs_segoe
def test_planted_font_in_current_dir_is_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_bundled: None
) -> None:
    # Un «segoeui.ttf» plantado en la carpeta actual no debe usarse nunca. Tiene que ser
    # una fuente de verdad: si fuera basura, fallaría al abrirse y el test no vigilaría.
    monkeypatch.chdir(tmp_path)
    shutil.copy(SEGOE_BOLD, tmp_path / "segoeui.ttf")
    monkeypatch.setenv("WINDIR", str(tmp_path / "sin-windows"))
    monkeypatch.delenv("SYSTEMROOT", raising=False)
    assert resolve_font(Style(), None).getname() == ("Aileron", "Regular")  # type: ignore[union-attr]


@needs_segoe
def test_cache_returns_same_object(no_bundled: None) -> None:
    assert resolve_font(Style(size=21), None) is resolve_font(Style(size=21), None)
    assert resolve_font(Style(size=21), None) is not resolve_font(Style(size=22), None)
