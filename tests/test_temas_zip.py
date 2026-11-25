"""Tests de ``import_theme_zip``: zips válidos y batería de zips maliciosos (RQ-10).

Los zips maliciosos se fabrican aquí mismo en ``tmp_path``: no se guardan en el repo.
Tras cada rechazo se comprueba que no queda **nada** en la carpeta de temas.
"""

from __future__ import annotations

import io
import json
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from luzeira.temas import ThemeError, cargar, import_theme_zip, load_theme

FIXTURES = Path(__file__).parent / "fixtures" / "temas"


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (0, 120, 255)).save(buf, "PNG")
    return buf.getvalue()


def theme_json(name: str = "Mi tema", image: str | None = "fondo.png") -> bytes:
    theme: dict = {"format": "luzeira-theme/1", "name": name, "orientation": "portrait"}
    if image:
        theme["background"] = {"image": image}
    return json.dumps(theme, ensure_ascii=False).encode()


def good_entries() -> dict[str, bytes]:
    return {"theme.json": theme_json(), "fondo.png": png_bytes()}


def make_zip(path: Path, entries: dict[str, bytes] | list[zipfile.ZipInfo | tuple]) -> Path:
    """Crea un zip. Acepta un dict nombre→datos o una lista de (ZipInfo|nombre, datos)."""
    items = entries.items() if isinstance(entries, dict) else entries
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile avisa de nombres duplicados: queremos eso
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in items:
                zf.writestr(name, data)
    return path


def raw_info(name: str, **attrs: object) -> zipfile.ZipInfo:
    """ZipInfo con el nombre tal cual (sin que zipfile cambie «\\» por «/»)."""
    info = zipfile.ZipInfo("x")
    info.filename = name  # se asigna después: el constructor normaliza, esto no
    info.compress_type = zipfile.ZIP_DEFLATED
    for key, value in attrs.items():
        setattr(info, key, value)
    return info


def leftovers(themes: Path) -> list[str]:
    return sorted(p.name for p in themes.iterdir()) if themes.exists() else []


# --- Válidos ---------------------------------------------------------------------------


def test_import_valid_zip(tmp_path: Path) -> None:
    themes = tmp_path / "temas"
    final = import_theme_zip(make_zip(tmp_path / "t.zip", good_entries()), themes)
    assert final == (themes / "mi-tema").resolve()
    assert load_theme(final).theme.name == "Mi tema"
    assert leftovers(themes) == ["mi-tema"]  # sin la carpeta temporal


def test_import_fixture_as_zip(tmp_path: Path) -> None:
    src = FIXTURES / "ejemplo-completo"
    entries = {p.relative_to(src).as_posix(): p.read_bytes() for p in src.rglob("*") if p.is_file()}
    final = import_theme_zip(make_zip(tmp_path / "t.zip", entries), tmp_path / "temas")
    assert final.name == "ejemplo-completo-nandu"  # slug sin tildes ni «¡!»
    assert set(load_theme(final).files) == {"fondo.png", "img/logo.jpg", "img/anim.gif"}


def test_import_zip_with_wrapper_folder(tmp_path: Path) -> None:
    # Lo que sale al comprimir una carpeta con el explorador de Windows.
    entries = {f"Mi tema bonito/{k}": v for k, v in good_entries().items()}
    entries["Mi tema bonito/LICENCIA-fuente.txt"] = b"OFL"
    final = import_theme_zip(make_zip(tmp_path / "t.zip", entries), tmp_path / "temas")
    assert (final / "theme.json").is_file()
    assert (final / "LICENCIA-fuente.txt").is_file()


def test_import_never_overwrites(tmp_path: Path) -> None:
    themes = tmp_path / "temas"
    (themes / "mi-tema").mkdir(parents=True)
    (themes / "mi-tema" / "mio.txt").write_text("no me pises")
    zip_path = make_zip(tmp_path / "t.zip", good_entries())
    assert import_theme_zip(zip_path, themes).name == "mi-tema-2"
    assert import_theme_zip(zip_path, themes).name == "mi-tema-3"
    assert (themes / "mi-tema" / "mio.txt").read_text() == "no me pises"


@pytest.mark.parametrize(
    ("name", "slug"),
    [("CON", "tema-con"), ("¡¿!?", "tema"), ("  Ñu  Ártico  ", "nu-artico"), ("a" * 80, "a" * 40)],
)
def test_slug(name: str, slug: str) -> None:
    assert cargar.slugify(name) == slug


def test_real_limits_match_the_spec() -> None:
    # Los tests de abajo bajan algunos límites para ir rápido; aquí se fijan los reales.
    assert cargar.MAX_ZIP_BYTES == 20 * 1024 * 1024
    assert cargar.MAX_UNZIPPED_BYTES == 50 * 1024 * 1024
    assert cargar.MAX_ZIP_ENTRIES == 200
    assert cargar.MAX_FILE_BYTES == 10 * 1024 * 1024
    assert cargar.MAX_JSON_BYTES == 256 * 1024
    assert cargar.MAX_IMAGE_SIDE == 4096


# --- Batería maliciosa -----------------------------------------------------------------

Builder = Callable[[Path], Path]


def entries_with(name: str, data: bytes = b"x") -> Builder:
    def build(path: Path) -> Path:
        e = good_entries()
        e[name] = data
        return make_zip(path, e)

    return build


def items_with(*extra: tuple) -> Builder:
    def build(path: Path) -> Path:
        return make_zip(path, [*good_entries().items(), *extra])

    return build


def not_a_zip(path: Path) -> Path:
    path.write_bytes(b"PK\x03\x04 esto no es un zip")
    return path


def too_big_zip(path: Path) -> Path:
    # Se mira el tamaño del fichero antes de abrirlo.
    with path.open("wb") as fh:
        fh.truncate(20 * 1024 * 1024 + 1)
    return path


def too_many(path: Path) -> Path:
    e = good_entries()
    e.update({f"x{i}.txt": b"" for i in range(200)})
    return make_zip(path, e)


def deflate_bomb(path: Path) -> Path:
    # 9 MB de ceros se quedan en ~9 KB: ratio ~1000. Por debajo de 10 MB por fichero
    # para que lo pare la regla del ratio y no la del tamaño.
    return entries_with("relleno.png", b"\0" * (9 * 1024 * 1024))(path)


def single_file_over_10mb(path: Path) -> Path:
    import random

    rnd = random.Random(1)
    return entries_with("grande.png", rnd.randbytes(10 * 1024 * 1024 + 10))(path)


def patch_entry(path: Path, name: bytes, local_off: int, central_off: int, value: bytes) -> None:
    """Reescribe un campo binario de la entrada ``name`` en sus dos cabeceras del zip."""
    data = bytearray(path.read_bytes())
    for sig, offset, name_at in ((b"PK", local_off, 30), (b"PK", central_off, 46)):
        start = 0
        while (i := data.find(sig, start)) != -1:
            start = i + 4
            if data[i + name_at : i + name_at + len(name)] == name:
                data[i + offset : i + offset + len(value)] = value
    path.write_bytes(bytes(data))


def lying_header(path: Path) -> Path:
    # La cabecera dice que «fondo.png» descomprimido ocupa 10 bytes; en realidad ocupa
    # más. zipfile corta en el tamaño declarado y la suma CRC no cuadra → dañado.
    make_zip(path, good_entries())
    patch_entry(path, b"fondo.png", 22, 24, (10).to_bytes(4, "little"))
    return path


def encrypted(path: Path) -> Path:
    # zipfile no escribe zips cifrados: se activa a mano el bit 0 de «flags».
    make_zip(path, good_entries())
    patch_entry(path, b"fondo.png", 6, 8, (0x1).to_bytes(2, "little"))
    return path


def missing_theme_json(path: Path) -> Path:
    return make_zip(path, {"fondo.png": png_bytes()})


def fake_image(path: Path) -> Path:
    return entries_with("fondo.png", b"MZ ejecutable disfrazado")(path)


def font_failing_when_painting(path: Path) -> Path:
    # R2-03: FreeType tenía abierta la fuente rechazada (viva en la traza del error) y
    # en Windows la carpeta temporal no se podía borrar: quedaba «.importando-*».
    from test_temas_cargar import SEGOE, font_broken_hinting

    if not SEGOE.is_file():
        pytest.skip("hace falta Segoe UI para fabricar la fuente rota")
    theme = {
        "format": "luzeira-theme/1",
        "name": "Fuente rota",
        "orientation": "portrait",
        "widgets": [
            {
                "type": "text",
                "text": "hola",
                "box": {"x": 0, "y": 0, "width": 100, "height": 30},
                "style": {"font": "rota.ttf"},
            }
        ],
    }
    return make_zip(
        path, {"theme.json": json.dumps(theme).encode(), "rota.ttf": font_broken_hinting()}
    )


MALICIOUS_ZIPS: list[tuple[str, Builder, str]] = [
    # No es un zip: mensaje claro, no BadZipFile.
    ("no-zip", not_a_zip, "no es un zip válido"),
    # Zip gigante: se rechaza sin abrirlo.
    ("zip-gigante", too_big_zip, "demasiado grande"),
    # 202 entradas: más de 200.
    ("demasiadas-entradas", too_many, "demasiados ficheros"),
    # Path traversal clásico.
    ("traversal", entries_with("../evil.png"), "salir de la carpeta"),
    # Traversal escondido en medio de la ruta.
    ("traversal-medio", entries_with("img/../../evil.png"), "salir de la carpeta"),
    # Ruta absoluta.
    ("absoluta", entries_with("/evil.png"), "ruta absoluta"),
    # Letra de unidad con barra.
    ("unidad", entries_with("C:/evil.png"), "letra de unidad"),
    # Letra de unidad sin barra («C:evil» es relativa a la carpeta actual de C:).
    ("unidad-relativa", entries_with("C:evil.png"), "letra de unidad"),
    # Barra invertida cruda en el nombre (zipfile la convierte en «/» al leer en Windows).
    ("barra-invertida", items_with((raw_info("img\\evil.png"), b"x")), "«\\»"),
    # Traversal con barras invertidas.
    ("traversal-invertido", items_with((raw_info("..\\evil.png"), b"x")), "«\\»"),
    # Ruta UNC.
    ("unc", items_with((raw_info("\\\\srv\\c\\evil.png"), b"x")), "«\\»"),
    # Nombres reservados de Windows.
    ("con", entries_with("CON.png"), "reservado de Windows"),
    ("com3", entries_with("com3.gif"), "reservado de Windows"),
    ("aux-carpeta", entries_with("aux/x.png"), "reservado de Windows"),
    # Enlace simbólico (modo Unix 0o120777 en los atributos externos).
    (
        "symlink",
        items_with((raw_info("enlace.png", external_attr=0o120777 << 16), b"/etc/passwd")),
        "enlace simbólico",
    ),
    # Entrada repetida: la segunda pisaría a la primera ya validada.
    ("duplicada", items_with(("fondo.png", b"otra")), "repetido"),
    # Duplicada solo en mayúsculas: en Windows es el mismo fichero.
    ("duplicada-mayusculas", items_with(("FONDO.png", b"otra")), "repetido"),
    # Entrada cifrada: no se puede inspeccionar.
    ("cifrada", encrypted, "cifrado"),
    # Compresión rara (bzip2): más superficie de ataque sin ganar nada.
    (
        "bzip2",
        items_with((raw_info("b.png", compress_type=zipfile.ZIP_BZIP2), b"x")),
        "compresión no admitida",
    ),
    # Extensiones peligrosas o simplemente fuera de la lista blanca.
    *[
        (f"ext-{ext}", entries_with(f"cosa.{ext}"), "tipo de fichero no permitido")
        for ext in ("svg", "py", "exe", "dll", "bat", "ps1", "lnk", "html", "js", "json", "pyc")
    ],
    # Sin extensión.
    ("sin-extension", entries_with("LEEME"), "» no tiene extensión"),
    # Doble extensión engañosa (el patrón solo admite un punto).
    ("doble-extension", entries_with("foto.exe.png"), "nombre no válido"),
    # Nombre con espacio final (Windows lo recorta y cambiaría de fichero).
    ("espacio", entries_with("img /x.png"), "nombre no válido"),
    # Bomba de descompresión.
    ("bomba-ratio", deflate_bomb, "bomba de compresión"),
    # Un fichero de más de 10 MB.
    ("fichero-gigante", single_file_over_10mb, "más de 10 MB"),
    # Cabecera que miente sobre el tamaño.
    ("cabecera-mentirosa", lying_header, "dañado"),
    # Sin theme.json.
    ("sin-theme-json", missing_theme_json, "Falta el fichero theme.json"),
    # Extrae bien pero la imagen es falsa: lo para load_theme y no deja restos.
    ("imagen-falsa", fake_image, "no es de verdad"),
    # R2-07: zipfile corta el nombre en el primer nulo: «b.png\0/../../evil.png» se
    # extraía como «b.png». Lo que se comprueba tiene que ser lo que dice el zip.
    # (raw_info: el constructor de ZipInfo también lo cortaría al escribir.)
    (
        "nulo",
        items_with((raw_info("img/b.png\x00/../../../evil.png"), png_bytes())),
        "nombre no válido",
    ),
    # R2-03: la fuente rechazada no puede dejar la carpeta temporal a medias.
    ("fuente-rota", font_failing_when_painting, "falla al pintar"),
]


@pytest.mark.parametrize(
    ("build", "expected"), [pytest.param(b, e, id=i) for i, b, e in MALICIOUS_ZIPS]
)
def test_malicious_zip_rejected(tmp_path: Path, build: Builder, expected: str) -> None:
    themes = tmp_path / "temas"
    zip_path = build(tmp_path / "t.zip")
    with pytest.raises(ThemeError) as info:
        import_theme_zip(zip_path, themes)
    assert expected in str(info.value)
    assert leftovers(themes) == []  # ni carpeta temporal ni tema a medias
    # Nada se ha escrito fuera de la carpeta de temas.
    assert not list(tmp_path.rglob("evil.png"))


def test_total_unzipped_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Se baja el límite total para no fabricar 50 MB; se cuentan bytes reales leídos.
    monkeypatch.setattr(cargar, "MAX_UNZIPPED_BYTES", 100_000)
    import random

    rnd = random.Random(2)
    e = good_entries()
    e["a.png"] = rnd.randbytes(60_000)
    e["b.png"] = rnd.randbytes(60_000)
    themes = tmp_path / "temas"
    with pytest.raises(ThemeError, match="descomprimido ocupa más"):
        import_theme_zip(make_zip(tmp_path / "t.zip", e), themes)
    assert leftovers(themes) == []


@pytest.mark.parametrize(
    "error", [MemoryError(), OSError("C:/Users/alguien/rename"), Image.DecompressionBombError("x")]
)
def test_unexpected_error_is_a_theme_error_and_cleans_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    # R2-04: la bandeja y el editor solo saben contar ThemeError; lo demás era un 500.
    def fail(_path: object) -> None:
        raise error

    monkeypatch.setattr(cargar, "load_theme", fail)
    themes = tmp_path / "temas"
    with pytest.raises(ThemeError, match="fallo inesperado") as info:
        import_theme_zip(make_zip(tmp_path / "t.zip", good_entries()), themes)
    assert "alguien" not in str(info.value)  # ni rutas ni trazas en el mensaje
    assert leftovers(themes) == []


def test_existing_theme_untouched_after_failure(tmp_path: Path) -> None:
    themes = tmp_path / "temas"
    (themes / "mi-tema").mkdir(parents=True)
    (themes / "mi-tema" / "theme.json").write_bytes(theme_json(image=None))
    with pytest.raises(ThemeError):
        import_theme_zip(fake_image(tmp_path / "t.zip"), themes)
    assert leftovers(themes) == ["mi-tema"]
    assert load_theme(themes / "mi-tema").theme.name == "Mi tema"


def test_imported_theme_never_takes_a_builtin_name(tmp_path):
    # Revisor fase 4: un zip con name «Esencial» creaba temas/esencial, tapado por el
    # incluido; no aparecía en el menú aunque el aviso dijera «importado».
    from luzeira.temas.cargar import _free_name

    assert _free_name(tmp_path, "esencial").name == "esencial-2"
    assert _free_name(tmp_path, "mi-tema").name == "mi-tema"
