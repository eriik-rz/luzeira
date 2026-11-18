"""Configuración del usuario y lista de temas (sin tocar el %APPDATA% real)."""

import json

import pytest

from visoria.config import (
    BUILTIN_THEMES,
    DEFAULT_THEME,
    AppConfig,
    clean_import_leftovers,
    list_themes,
    load_config,
    save_config,
    theme_path,
)

MINIMAL = {"format": "visoria-theme/1", "name": "x", "orientation": "landscape"}


def make_theme(base, name):
    d = base / name
    d.mkdir(parents=True)
    (d / "theme.json").write_text(json.dumps(MINIMAL), encoding="utf-8")
    return d


def test_missing_config_gives_defaults(tmp_path):
    assert load_config(tmp_path / "config.json") == AppConfig()


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "sub" / "config.json"
    save_config(AppConfig(theme="mi-tema", brightness=80, flip=True), path)
    assert load_config(path) == AppConfig(theme="mi-tema", brightness=80, flip=True)
    assert not (tmp_path / "sub" / "config.json.tmp").exists()  # el temporal se renombró


@pytest.mark.parametrize(
    "raw", ["{roto", '{"brightness": 900}', '{"theme": "../../Windows"}', '{"theme": "CON"}']
)
def test_damaged_config_is_set_aside_and_defaults_used(tmp_path, raw):
    path = tmp_path / "config.json"
    path.write_text(raw, encoding="utf-8")
    assert load_config(path) == AppConfig()
    assert (tmp_path / "config.json.danado").read_text(encoding="utf-8") == raw


def test_config_saved_by_notepad_never_blocks_startup(tmp_path):
    path = tmp_path / "config.json"
    # UTF-8 con BOM (Bloc de notas, «UTF-8 con BOM»): se lee bien.
    path.write_bytes(b"\xef\xbb\xbf" + b'{"brightness": 30}')
    assert load_config(path).brightness == 30
    # UTF-16 (Bloc de notas, «Unicode»): no es legible, pero la app arranca igual.
    path.write_bytes(b"\xff\xfe{\x00}\x00")
    assert load_config(path) == AppConfig()
    assert (tmp_path / "config.json.danado").exists()


def test_unknown_fields_from_newer_versions_are_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"brightness": 30, "campo_futuro": 1}', encoding="utf-8")
    assert load_config(path).brightness == 30


def test_list_themes_builtin_first_and_cannot_be_impersonated(tmp_path):
    make_theme(tmp_path, "mio")
    make_theme(tmp_path, DEFAULT_THEME)  # un tema del usuario con el nombre de uno incluido
    make_theme(tmp_path, ".importando-abc")  # restos de una importación: no es un tema
    (tmp_path / "sin-json").mkdir()
    themes = list_themes(tmp_path)
    names = [t.name for t in themes]
    # Primero todos los incluidos, después los del usuario.
    flags = [t.builtin for t in themes]
    assert flags == sorted(flags, reverse=True) and flags[0]
    # El que se llama como uno incluido es el incluido, no el del usuario.
    default = next(t for t in themes if t.name == DEFAULT_THEME)
    assert default.builtin and default.path == BUILTIN_THEMES / DEFAULT_THEME
    assert "mio" in names
    assert ".importando-abc" not in names and "sin-json" not in names
    assert names.count(DEFAULT_THEME) == 1


def test_menu_shows_readable_title_and_survives_broken_json(tmp_path):
    good = make_theme(tmp_path, "mi-tema")
    (good / "theme.json").write_text(json.dumps({**MINIMAL, "name": "Mi Tema Ñ"}), encoding="utf-8")
    broken = make_theme(tmp_path, "roto")
    (broken / "theme.json").write_text("{esto no es json", encoding="utf-8")
    titles = {t.name: t.title for t in list_themes(tmp_path)}
    assert titles["mi-tema"] == "Mi Tema Ñ"
    assert titles["roto"] == "roto"  # un tema roto no rompe el menú: sale su carpeta


def test_theme_path_falls_back_to_default(tmp_path):
    make_theme(tmp_path, "mio")
    assert theme_path("mio", tmp_path) == tmp_path / "mio"
    assert theme_path("no-existe", tmp_path) == BUILTIN_THEMES / DEFAULT_THEME


def test_clean_import_leftovers(tmp_path):
    (tmp_path / ".importando-x1").mkdir()
    make_theme(tmp_path, "mio")
    assert clean_import_leftovers(tmp_path) == 1
    assert [p.name for p in tmp_path.iterdir()] == ["mio"]
