"""Configuración del usuario y lista de temas (sin tocar el %APPDATA% real)."""

import json

import pytest

from luzeira.config import (
    BUILTIN_THEMES,
    DEFAULT_THEME,
    AppConfig,
    DisplaySetting,
    clean_import_leftovers,
    list_themes,
    load_config,
    save_config,
    theme_path,
)

MINIMAL = {"format": "luzeira-theme/1", "name": "x", "orientation": "landscape"}


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


def test_old_name_data_is_copied_once_and_themes_upgraded(tmp_path):
    from luzeira.config import migrate_old_name

    old, new = tmp_path / "Visoria", tmp_path / "Luzeira"
    (old / "temas" / "mio").mkdir(parents=True)
    (old / "config.json").write_text('{"theme": "mio"}', encoding="utf-8")
    (old / "temas" / "mio" / "theme.json").write_text(
        '{"format": "visoria-theme/1", "name": "Mío"}', encoding="utf-8"
    )
    assert migrate_old_name(new, old) is True
    assert json.loads((new / "config.json").read_text(encoding="utf-8")) == {"theme": "mio"}
    theme = json.loads((new / "temas" / "mio" / "theme.json").read_text(encoding="utf-8"))
    assert theme["format"] == "luzeira-theme/1" and theme["name"] == "Mío"
    # La vieja se queda intacta, y la segunda vez no se toca nada.
    assert '"visoria-theme/1"' in (old / "temas" / "mio" / "theme.json").read_text(encoding="utf-8")
    (new / "config.json").write_text('{"theme": "otro"}', encoding="utf-8")
    assert migrate_old_name(new, old) is False
    assert "otro" in (new / "config.json").read_text(encoding="utf-8")


def test_no_old_data_no_migration(tmp_path):
    from luzeira.config import migrate_old_name

    assert migrate_old_name(tmp_path / "Luzeira", tmp_path / "Visoria") is False
    assert not (tmp_path / "Luzeira").exists()


# --- pantalla elegida a mano (C-44) ---------------------------------------------------


def test_display_choice_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    display = DisplaySetting(
        model="xuanfang-rev-b", device="COM6", vid=0x1A86, pid=0x5722, serial="2017-2-25"
    )
    save_config(AppConfig(brightness=40, display=display), path)
    assert load_config(path) == AppConfig(brightness=40, display=display)


def test_old_config_without_display_still_loads(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"theme": "esencial", "brightness": 70, "flip": false}', encoding="utf-8")
    assert load_config(path) == AppConfig(brightness=70, display=None)


@pytest.mark.parametrize(
    "display",
    [
        '{"model": "../../x", "device": "COM3"}',
        '{"model": "kipye-rev-d", "device": "COM3", "vid": 70000}',
        '{"model": "kipye-rev-d"}',
        '"COM3"',
    ],
)
def test_broken_display_is_forgotten_without_losing_the_rest(tmp_path, display):
    path = tmp_path / "config.json"
    path.write_text(f'{{"brightness": 35, "display": {display}}}', encoding="utf-8")
    assert load_config(path) == AppConfig(brightness=35, display=None)
    assert not (tmp_path / "config.json.danado").exists()  # no se aparta como dañada


def test_updates_start_unasked_and_roundtrip(tmp_path):
    path = tmp_path / "config.json"
    assert AppConfig().updates is None  # sin preguntar: sin red
    config = AppConfig(updates=True, updates_max_seen="0.3.0", updates_last_check=1.5)
    save_config(config, path)
    assert load_config(path) == config


@pytest.mark.parametrize(
    "field",
    [
        '"updates": "sí"',
        '"updates_max_seen": "../0.3"',
        '"updates_max_seen": 3',
        '"updates_last_check": "ayer"',
    ],
)
def test_broken_updates_are_forgotten_without_losing_the_rest(tmp_path, field):
    path = tmp_path / "config.json"
    path.write_text(f'{{"brightness": 35, {field}}}', encoding="utf-8")
    config = load_config(path)
    assert config.brightness == 35
    assert (config.updates, config.updates_max_seen, config.updates_last_check) == (
        None,
        None,
        None,
    )
    assert not (tmp_path / "config.json.danado").exists()


@pytest.mark.parametrize("version", ["00.1.0", "\u0661.2.3"])
def test_max_seen_takes_only_what_the_updater_can_read(tmp_path, version):
    path = tmp_path / "config.json"
    path.write_text(f'{{"updates_max_seen": "{version}"}}', encoding="utf-8")
    assert load_config(path).updates_max_seen is None
