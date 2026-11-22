"""Tests de los idiomas (C-62): la función ``t``, cómo se elige el idioma y que los
catálogos de la app y del editor estén completos en español e inglés."""

from __future__ import annotations

import json
import re
import string
from pathlib import Path

import pytest

from luzeira import idioma

ROOT = Path(__file__).resolve().parent.parent
REAL_WINDOWS_LANGUAGE = idioma.windows_language
#: Los dos juegos de catálogos: los de Python y los del editor (React).
CATALOG_SETS = {
    "app": idioma.CATALOG_DIR,
    "editor": ROOT / "editor" / "src" / "i18n",
}


def _catalogs(folder: Path) -> dict[str, dict[str, str]]:
    if not folder.is_dir():
        return {}
    return {d.name: idioma.load(d.name, folder) for d in sorted(folder.iterdir()) if d.is_dir()}


def _fields(text: str) -> set[str]:
    return {name for _lit, name, _spec, _conv in string.Formatter().parse(text) if name}


# --- t() y elegir idioma -----------------------------------------------------------------


@pytest.fixture
def fake_catalogs(tmp_path, monkeypatch):
    for code, data in {
        "es": {"saludo": "Hola, {nombre}", "solo_es": "Solo en español"},
        "en": {"saludo": "Hi, {nombre}", "solo_en": "Only English"},
        "de": {"saludo": "Hallo, {nombre}"},
    }.items():
        (tmp_path / code).mkdir()
        (tmp_path / code / "zona.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(idioma, "CATALOG_DIR", tmp_path)
    monkeypatch.setattr(idioma, "_catalogs", {})
    return tmp_path


def test_text_comes_from_the_active_language_with_its_variables(fake_catalogs):
    idioma.set_language("de")
    assert idioma.t("saludo", nombre="Ana") == "Hallo, Ana"
    idioma.set_language("en")
    assert idioma.t("saludo", nombre="Ana") == "Hi, Ana"


def test_a_missing_text_falls_back_to_english_then_spanish_then_the_key(fake_catalogs):
    idioma.set_language("de")
    assert idioma.t("solo_en") == "Only English"
    assert idioma.t("solo_es") == "Solo en español"
    assert idioma.t("no.existe") == "no.existe"


def test_a_text_that_does_not_match_its_variables_is_shown_anyway(fake_catalogs):
    idioma.set_language("en")
    assert idioma.t("saludo") == "Hi, {nombre}"  # sin variables: tal cual
    assert idioma.t("saludo", otra=1) == "Hi, {nombre}"  # no revienta


@pytest.mark.parametrize(
    "chosen,windows,expected",
    [
        ("de", "es", "de"),  # lo elegido manda
        (None, "de", "de"),  # automático: el de Windows
        (None, "ja", "en"),  # uno que no tenemos: inglés
        ("xx", "es", "es"),  # un código raro: como automático
        (None, None, "en"),
    ],
)
def test_resolve(fake_catalogs, chosen, windows, expected):
    assert idioma.resolve(chosen, detected=windows or "") == expected


def test_only_languages_with_catalogs_are_offered_with_their_own_name(fake_catalogs):
    assert idioma.available() == {"es": "Español", "en": "English", "de": "Deutsch"}


def test_a_key_in_two_zones_is_refused(fake_catalogs):
    (fake_catalogs / "es" / "otra.json").write_text('{"saludo": "Otra"}', encoding="utf-8")
    with pytest.raises(ValueError, match="repetidas"):
        idioma.load("es", fake_catalogs)


def test_windows_language_is_a_short_code_or_none():
    code = REAL_WINDOWS_LANGUAGE()  # la de verdad (conftest la fija a «es» en los demás)
    assert code is None or re.fullmatch(r"[a-z]{2,3}", code)


# --- los catálogos de verdad ---------------------------------------------------------------


@pytest.mark.parametrize("which", sorted(CATALOG_SETS))
def test_spanish_and_english_have_the_same_texts_and_variables(which):
    cats = _catalogs(CATALOG_SETS[which])
    if not cats:
        pytest.skip(f"aún no hay catálogos de {which}")
    es, en = cats["es"], cats["en"]
    assert sorted(es.keys() - en.keys()) == [], "faltan en inglés"
    assert sorted(en.keys() - es.keys()) == [], "faltan en español"
    wrong = [k for k in es if _fields(es[k]) != _fields(en[k])]
    assert wrong == [], "las variables no casan"


@pytest.mark.parametrize("which", sorted(CATALOG_SETS))
def test_other_languages_have_no_extra_keys_and_the_same_variables(which):
    cats = _catalogs(CATALOG_SETS[which])
    base = cats.get("es", {})
    for code, cat in cats.items():
        if code in idioma.COMPLETE:
            continue
        assert code in idioma.NATIVE_NAMES, f"{code}: falta su nombre en NATIVE_NAMES"
        assert sorted(cat.keys() - base.keys()) == [], f"{code}: claves que no existen"
        wrong = [k for k in cat if _fields(cat[k]) != _fields(base[k])]
        assert wrong == [], f"{code}: variables que no casan"


def test_the_editor_offers_the_same_languages_as_the_app():
    app, editor = _catalogs(CATALOG_SETS["app"]), _catalogs(CATALOG_SETS["editor"])
    if editor:
        assert sorted(app) == sorted(editor)


def test_screen_formats_are_the_same_in_the_app_and_in_the_editor_preview():
    """Días, meses, formato de fecha y coma decimal: la vista previa del editor tiene que
    pintar igual que la pantalla (zona ``pantalla`` en los dos juegos)."""
    for code in _catalogs(CATALOG_SETS["app"]):
        app = CATALOG_SETS["app"] / code / "pantalla.json"
        editor = CATALOG_SETS["editor"] / code / "pantalla.json"
        if app.exists() and editor.exists():
            assert json.loads(app.read_text("utf-8")) == json.loads(editor.read_text("utf-8"))
