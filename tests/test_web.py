"""La web (``web/``): versión en español y en inglés, enlazadas entre sí.

Lee el HTML de ``web/`` tal cual (sin montar ``_site/``, que necesita Node). Comprueba
lo que se rompe sin que se note al mirar una sola página: el ``lang``, los
``hreflang`` para buscadores, el selector ES / EN, las rutas relativas desde ``en/`` y
que la política de privacidad en inglés tenga las mismas piezas que la española.
"""

import importlib.util
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"
BASE = "https://eriik-rz.github.io/luzeira/"

# Lo que ``scripts/web.py`` genera en ``_site/`` y no está en ``web/``.
GENERATED = {"icono.png", "favicon.png", "temas.png", "demo/"}

# Página -> (idioma, la misma página en el otro idioma, URL pública de cada idioma).
PAGES = {
    "index.html": ("es", "en/index.html"),
    "privacidad.html": ("es", "en/privacidad.html"),
    "en/index.html": ("en", "index.html"),
    "en/privacidad.html": ("en", "privacidad.html"),
}


class _Page(HTMLParser):
    """Recoge lo que miran los tests: lang, alternates, enlaces y piezas del texto."""

    def __init__(self) -> None:
        super().__init__()
        self.lang: str | None = None
        self.alternates: dict[str, str] = {}
        self.refs: list[str] = []  # href y src de enlaces, imágenes, CSS, iframes
        self.switch: list[tuple[str, str | None]] = []  # (lang, href) del selector
        self.tags: list[str] = []  # h2, h3, li, p, strong: el esqueleto del texto
        self._in_switch = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        if tag == "link" and a.get("rel") == "alternate":
            self.alternates[a["hreflang"]] = a["href"]
        for key in ("href", "src"):
            if a.get(key):
                self.refs.append(a[key])
        if tag in {"h2", "h3", "li", "p", "strong"}:
            self.tags.append(tag)
        if tag == "span" and a.get("class") == "idiomas":
            self._in_switch = 1
        elif self._in_switch:
            if tag == "span":
                self._in_switch += 1
            if tag in {"a", "span"}:
                self.switch.append((a.get("lang"), a.get("href")))

    def handle_endtag(self, tag):
        if tag == "span" and self._in_switch:
            self._in_switch -= 1


def parse(rel: str) -> _Page:
    page = _Page()
    page.feed((WEB / rel).read_text(encoding="utf-8"))
    return page


def public_url(rel: str) -> str:
    """URL pública de una página: ``index.html`` se sirve como su carpeta."""
    return BASE + rel.removesuffix("index.html")


@pytest.mark.parametrize("rel", PAGES)
def test_each_page_declares_its_language(rel):
    assert parse(rel).lang == PAGES[rel][0]


@pytest.mark.parametrize("rel", PAGES)
def test_hreflang_points_to_both_versions(rel):
    lang, other = PAGES[rel]
    es, en = (rel, other) if lang == "es" else (other, rel)
    assert parse(rel).alternates == {
        "es": public_url(es),
        "en": public_url(en),
        "x-default": public_url(es),
    }


@pytest.mark.parametrize("rel", PAGES)
def test_language_switch_links_to_the_same_page_in_the_other_language(rel):
    lang, other = PAGES[rel]
    switch = parse(rel).switch
    # El idioma actual sin enlace; el otro, con enlace a su página.
    assert (lang, None) in switch
    other_lang = "en" if lang == "es" else "es"
    links = [href for code, href in switch if code == other_lang]
    assert len(links) == 1
    target = (WEB / rel).parent / links[0]
    if links[0].endswith("/"):
        target = target / "index.html"
    assert target.resolve() == (WEB / other).resolve()


@pytest.mark.parametrize("rel", PAGES)
def test_relative_links_exist_in_the_built_site(rel):
    """Cada ruta relativa apunta a un fichero de ``web/`` o a algo que genera web.py."""
    missing = []
    for ref in parse(rel).refs:
        parts = urlsplit(ref)
        if parts.scheme or not parts.path:  # http(s)://… o #ancla
            continue
        target = ((WEB / rel).parent / parts.path).resolve()
        if target.is_relative_to(WEB.resolve()):
            inside = target.relative_to(WEB.resolve()).as_posix()
            if parts.path.endswith("/") and inside + "/" in GENERATED:
                continue
            if inside in GENERATED:
                continue
            if target.is_dir():
                target = target / "index.html"
        if not target.is_file():
            missing.append(ref)
    assert missing == []


def test_english_page_opens_the_demo_in_english():
    demo = [r for r in parse("en/index.html").refs if "demo/" in r]
    assert demo, "la portada inglesa ya no enlaza la demo"
    assert all(r == "../demo/?lang=en" for r in demo), demo


@pytest.mark.parametrize("rel", ["index.html", "privacidad.html"])
def test_english_version_has_the_same_structure(rel):
    """Misma traducción, mismas piezas: mismos títulos, párrafos y puntos, y los
    mismos enlaces de fuera. Si alguien añade un compromiso solo en un idioma, cae."""
    es, en = parse(rel), parse("en/" + rel)
    assert en.tags == es.tags

    def external(page):
        return [r for r in page.refs if urlsplit(r).scheme]

    assert external(en) == external(es)


# --- Revisión de seguridad R1-06: CSP de la web y de la demo ----------------------------


class _Csp(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.csp: list[str] = []
        self.first_script_at: int | None = None
        self.csp_at: int | None = None
        self._n = 0

    def handle_starttag(self, tag, attrs):
        self._n += 1
        a = dict(attrs)
        if tag == "meta" and (a.get("http-equiv") or "").lower() == "content-security-policy":
            self.csp.append(a.get("content") or "")
            self.csp_at = self.csp_at or self._n
        if tag == "script" and self.first_script_at is None:
            self.first_script_at = self._n


def _directives(csp: str) -> dict[str, list[str]]:
    out = {}
    for part in csp.split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


@pytest.mark.parametrize("rel", PAGES)
def test_each_page_has_a_csp_without_scripts(rel):
    page = _Csp()
    page.feed((WEB / rel).read_text(encoding="utf-8"))
    assert len(page.csp) == 1
    rules = _directives(page.csp[0])
    assert rules["script-src"] == ["'none'"]
    assert rules["base-uri"] == ["'none'"]


class _Frames(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.frames: list[dict[str, str | None]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "iframe":
            self.frames.append(dict(attrs))


@pytest.mark.parametrize("rel", ["index.html", "en/index.html"])
def test_the_demo_runs_in_a_sandbox_without_its_origin(rel):
    # R1-06 (C-77.3): la demo corre con origen opaco, así que un fallo en el editor no
    # alcanza a la portada ni a nada del dominio. Funciona porque GitHub Pages sirve con
    # «Access-Control-Allow-Origin: *» (los módulos y los fetch de la demo pasan a ser
    # cross-origin); medido en Chrome el 2026-11-25, con la vista previa pintada.
    page = _Frames()
    page.feed((WEB / rel).read_text(encoding="utf-8"))
    assert page.frames
    for frame in page.frames:
        assert frame.get("sandbox") == "allow-scripts"


def _web_script():
    spec = importlib.util.spec_from_file_location("web_script", REPO / "scripts" / "web.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_web_build_puts_the_csp_in_the_demo_before_any_script(tmp_path):
    """La demo (GitHub Pages no deja poner cabeceras) lleva la CSP en un <meta>, delante
    de cualquier <script>. Se prueba sobre una copia de ``editor/index.html``."""
    web = _web_script()
    index = tmp_path / "index.html"
    index.write_bytes((REPO / "editor" / "index.html").read_bytes())
    web.add_demo_csp(index)
    page = _Csp()
    page.feed(index.read_text(encoding="utf-8"))
    assert page.csp == [web.DEMO_CSP]
    assert page.first_script_at is not None and page.csp_at < page.first_script_at
    rules = _directives(web.DEMO_CSP)
    assert rules["default-src"] == ["'self'"]  # scripts: solo los suyos
    assert rules["object-src"] == ["'none'"] and rules["base-uri"] == ["'none'"]
    assert "frame-ancestors" not in rules  # en un <meta> no vale (y da un aviso)
    assert "'unsafe-eval'" not in web.DEMO_CSP and "script-src" not in rules


def test_web_build_fails_if_the_editor_page_changes_shape(tmp_path):
    web = _web_script()
    index = tmp_path / "index.html"
    index.write_text("<!doctype html><meta charset=utf-8><script src=x.js></script>")
    with pytest.raises(SystemExit):
        web.add_demo_csp(index)


def test_editor_index_has_no_csp_meta():
    """En ``editor/index.html`` no: rompería ``npm run dev`` (Vite mete scripts en
    línea). En la app la CSP la pone el servidor local; en la web, ``scripts/web.py``."""
    page = _Csp()
    page.feed((REPO / "editor" / "index.html").read_text(encoding="utf-8"))
    assert page.csp == []
