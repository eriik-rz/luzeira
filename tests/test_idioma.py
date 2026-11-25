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


def test_automatic_language_says_the_same_in_the_app_and_in_the_editor():
    # C-62: el menú del icono y los Ajustes del editor dicen lo mismo («el de Windows»).
    app, editor = _catalogs(CATALOG_SETS["app"]), _catalogs(CATALOG_SETS["editor"])
    different = {
        code: (app[code]["idioma.automatico"], editor[code]["ajustes.idioma.automatico"])
        for code in app
        if app[code]["idioma.automatico"] != editor[code]["ajustes.idioma.automatico"]
    }
    assert different == {}


MODEL_NAME_KEYS = (
    "pantallas.modelo_rev_a",
    "pantallas.modelo_xuanfang",
    "pantallas.modelo_kipye",
    "pantallas.modelo_rev_c",
    # Fase 5: los tamaños de la rev C y la WeAct con decimales (el 5" y la familia WeAct,
    # sin cifras decimales, no entran: el test busca un decimal en cada nombre).
    "pantallas.modelo_rev_c_21",
    "pantallas.modelo_rev_c_88",
    "pantallas.modelo_weact_35",
    "pantallas.modelo_weact_096",
)


def test_model_names_use_the_decimal_separator_of_each_language():
    # C-62: en inglés «3.5"», no «3,5"». El separador es el de la pantalla (``pantalla.decimal``).
    for code, cat in _catalogs(CATALOG_SETS["app"]).items():
        sep = cat["pantalla.decimal"]
        other = "." if sep == "," else ","
        for key in MODEL_NAME_KEYS:
            assert re.search(rf"\d{re.escape(sep)}\d", cat[key]), (code, key)
            assert not re.search(rf"\d{re.escape(other)}\d", cat[key]), (code, key)


def test_no_english_text_has_a_decimal_comma():
    for which, folder in CATALOG_SETS.items():
        en = _catalogs(folder).get("en", {})
        assert [k for k, v in en.items() if re.search(r"\d,\d", v)] == [], which


def test_every_display_model_has_its_name_in_the_catalog():
    from luzeira.pantallas.modelos import MODELS

    es, en = (_catalogs(CATALOG_SETS["app"])[code] for code in ("es", "en"))
    for model in MODELS:
        assert model.label_key in es and model.label_key in en, model.key
    idioma.set_language("en")
    assert [m.label for m in MODELS if "3,5" in m.label] == []


# --- Text: textos que recuerdan su clave (C-62) --------------------------------------------


def test_text_is_a_str_that_can_be_written_again_in_another_language(fake_catalogs):
    idioma.set_language("es")
    hello = idioma.Text("saludo", nombre="Ana")
    assert hello == "Hola, Ana" and isinstance(hello, str)
    idioma.set_language("en")
    assert hello == "Hola, Ana"  # el str no cambia: es el de cuando se creó
    assert hello.again() == "Hi, Ana" and idioma.again(hello) == "Hi, Ana"
    assert idioma.again("suelto") == "suelto"


def test_the_same_text_in_two_languages_is_the_same_message(fake_catalogs):
    idioma.set_language("es")
    spanish = idioma.Text("saludo", nombre="Ana")
    idioma.set_language("en")
    english = idioma.Text("saludo", nombre="Ana")
    assert spanish != english
    assert idioma.ident(spanish) == idioma.ident(english)
    assert idioma.ident(idioma.Text("saludo", nombre="Eva")) != idioma.ident(english)
    assert idioma.ident("Hola") != idioma.ident("Hi")  # un str suelto cuenta por su texto


def test_joined_texts_and_errors_are_written_again_too(fake_catalogs):
    idioma.set_language("es")
    error = ValueError(idioma.Text("saludo", nombre="Ana"))
    joined = idioma.Joined(idioma.message_of(error), idioma.Text("solo_es"))
    assert joined == "Hola, Ana Solo en español"
    assert idioma.message_of(ValueError("tal cual")) == "tal cual"
    idioma.set_language("en")
    assert joined.again() == "Hi, Ana Solo en español"  # «solo_es» no está en inglés
    nested = idioma.Text("saludo", nombre=error)  # un error dentro de otro texto
    idioma.set_language("de")
    assert nested.again() == "Hallo, Hallo, Ana"
    assert idioma.ident(joined) == idioma.ident(
        idioma.Joined(idioma.Text("saludo", nombre="Ana"), idioma.Text("solo_es"))
    )
    # Un error sin Text cuenta por su tipo: su texto ya viene traducido.
    assert idioma.ident(KeyError("a")) == idioma.ident(KeyError("b")) == "KeyError"


@pytest.mark.parametrize(
    ("langid", "expected"), [(0x0804, "zh"), (0x0411, "ja"), (0x0404, None), (0x0C04, None)]
)
def test_traditional_chinese_windows_falls_back_instead_of_simplified(
    monkeypatch, langid, expected
):
    # C-65: el chino de Luzeira es el simplificado (China continental); en Taiwán y Hong
    # Kong se escribe el tradicional, así que ahí sale el inglés (y se puede elegir a mano).
    class Kernel32:
        def GetUserDefaultUILanguage(self):
            return langid

    monkeypatch.setattr(idioma.sys, "platform", "win32")
    monkeypatch.setattr(idioma.ctypes, "WinDLL", lambda name: Kernel32(), raising=False)
    assert REAL_WINDOWS_LANGUAGE() == expected
