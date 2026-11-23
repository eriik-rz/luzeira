"""Tests de ``load_theme``: temas válidos y batería de temas maliciosos (RQ-10).

Cada caso malicioso comprueba también **qué** mensaje sale: así un caso no puede
pasar «por casualidad» parado por otra defensa distinta de la que vigila.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import struct
import sys
import zlib
from collections.abc import Callable
from pathlib import Path

import pytest
from PIL import Image

from luzeira.temas import LoadedTheme, ThemeError, cargar, load_theme
from luzeira.temas.cargar import open_validated_image
from luzeira.temas.rutas import is_reserved_windows_name

FIXTURES = Path(__file__).parent / "fixtures" / "temas"
SEGOE = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "segoeui.ttf"


# --- Ayudas ----------------------------------------------------------------------------


def base_theme(**extra: object) -> dict:
    theme: dict = {"format": "luzeira-theme/1", "name": "Prueba", "orientation": "portrait"}
    theme.update(extra)
    return theme


def write_theme(folder: Path, theme: dict | str | bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    if isinstance(theme, dict):
        theme = json.dumps(theme, ensure_ascii=False)
    if isinstance(theme, str):
        theme = theme.encode("utf-8")
    (folder / "theme.json").write_bytes(theme)
    return folder


def png_bytes(size: tuple[int, int] = (16, 16)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(buf, "PNG")
    return buf.getvalue()


def jpeg_bytes(size: tuple[int, int] = (64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.effect_noise(size, 64).convert("RGB").save(buf, "JPEG")
    return buf.getvalue()


def png_header_only(width: int, height: int) -> bytes:
    """PNG diminuto en disco cuya cabecera dice medir width x height (bomba)."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    idat = zlib.compress(b"\0" * 1000)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def bg(rel: str) -> dict:
    return base_theme(background={"image": rel})


def font_widget(rel: str) -> dict:
    return base_theme(
        widgets=[
            {
                "type": "text",
                "text": "hola",
                "box": {"x": 0, "y": 0, "width": 100, "height": 30},
                "style": {"font": rel},
            }
        ]
    )


# --- Temas válidos ---------------------------------------------------------------------


@pytest.mark.parametrize("name", ["ejemplo-minimo", "ejemplo-completo"])
def test_fixture_themes_load(name: str) -> None:
    loaded = load_theme(FIXTURES / name)
    assert isinstance(loaded, LoadedTheme)
    assert loaded.directory == (FIXTURES / name).resolve()
    for rel, real in loaded.files.items():
        assert real.is_file()
        assert real.is_relative_to(loaded.directory), rel


def test_complete_fixture_lists_every_file() -> None:
    loaded = load_theme(FIXTURES / "ejemplo-completo")
    assert set(loaded.files) == {"fondo.png", "img/logo.jpg", "img/anim.gif"}
    assert loaded.theme.name == "Ejemplo completo ¡Ñandú!"


def test_uppercase_extension_is_fine(tmp_path: Path) -> None:
    # Windows no distingue mayúsculas: «FONDO.PNG» es un PNG normal.
    folder = write_theme(tmp_path / "t", bg("FONDO.PNG"))
    (folder / "FONDO.PNG").write_bytes(png_bytes())
    assert "FONDO.PNG" in load_theme(folder).files


@pytest.mark.skipif(not SEGOE.is_file(), reason="Segoe UI solo está en Windows")
def test_theme_with_real_font_loads(tmp_path: Path) -> None:
    folder = write_theme(tmp_path / "t", font_widget("fuentes/mia.ttf"))
    (folder / "fuentes").mkdir()
    shutil.copy(SEGOE, folder / "fuentes" / "mia.ttf")
    assert "fuentes/mia.ttf" in load_theme(folder).files


def test_image_at_size_limit_is_fine(tmp_path: Path) -> None:
    folder = write_theme(tmp_path / "t", bg("grande.png"))
    (folder / "grande.png").write_bytes(png_bytes((4096, 16)))
    load_theme(folder)


# --- Batería maliciosa -----------------------------------------------------------------
# Cada caso: (id, tema, preparación de ficheros, trozo del mensaje esperado).

Setup = Callable[[Path], None]


def nothing(_: Path) -> None:
    pass


def put(rel: str, data: bytes) -> Setup:
    def _put(folder: Path) -> None:
        target = folder / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    return _put


def truncated_png() -> bytes:
    data = png_bytes((64, 64))
    return data[: len(data) // 2]


def png_bad_crc() -> bytes:
    data = bytearray(png_bytes((64, 64)))
    i = data.index(b"IDAT")
    data[i + 6] ^= 0xFF
    return bytes(data)


def png_corrupt_tail() -> bytes:
    """PNG cuya imagen está bien pero lleva detrás un bloque tEXt con la suma CRC mal.

    load() deja de leer al acabar los píxeles y no lo ve; verify() recorre el fichero
    entero hasta IEND y sí. Es el caso que justifica llamar a los dos.
    """
    data = png_bytes((64, 64))
    iend = data.index(b"IEND") - 4
    body = b"tEXtComment\0hola"
    bad_crc = (zlib.crc32(body) ^ 0xFFFFFFFF) & 0xFFFFFFFF
    text = struct.pack(">I", len(body) - 4) + body + struct.pack(">I", bad_crc)
    return data[:iend] + text + data[iend:]


def truncated_jpeg() -> bytes:
    data = jpeg_bytes((256, 256))
    return data[: len(data) // 2]


def mkdir(rel: str) -> Setup:
    def _mk(folder: Path) -> None:
        (folder / rel).mkdir(parents=True)

    return _mk


def widgets(n: int) -> dict:
    w = {"type": "clock", "box": {"x": 0, "y": 0, "width": 10, "height": 10}}
    return base_theme(widgets=[w] * n)


MALICIOUS_JSON: list[tuple[str, dict | str, Setup, str]] = [
    # El JSON enorme es una forma barata de agotar memoria: se corta a 256 KB.
    ("json-gigante", json.dumps(base_theme()) + " " * 300_000, nothing, "demasiado grande"),
    # Un JSON mal escrito tiene que dar un error legible, no una traza.
    ("json-roto", '{"format": "luzeira-theme/1", ', nothing, "JSON está mal escrito"),
    # Otra versión del formato puede traer campos con otro significado.
    ("version-futura", base_theme(format="luzeira-theme/2"), nothing, "valor no permitido"),
    # Un campo desconocido (p. ej. «script») nunca se ignora en silencio.
    ("campo-extra", base_theme(script="rm -rf /"), nothing, "campo desconocido"),
    # Un tipo de widget inventado no puede colarse.
    (
        "widget-desconocido",
        base_theme(widgets=[{"type": "python", "box": {"x": 0, "y": 0, "width": 1, "height": 1}}]),
        nothing,
        "tipo de widget desconocido",
    ),
    # Más de 64 widgets: límite de trabajo por fotograma.
    ("demasiados-widgets", widgets(65), nothing, "demasiados elementos"),
    # Un widget fuera de la pantalla: el mensaje propio del modelo sale en español.
    (
        "widget-fuera",
        base_theme(
            widgets=[{"type": "clock", "box": {"x": 300, "y": 0, "width": 100, "height": 10}}]
        ),
        nothing,
        "se sale de la pantalla",
    ),
    # Path traversal clásico.
    ("traversal", bg("../fuera.png"), nothing, "no es un nombre de fichero válido"),
    # Ruta absoluta de Windows con letra de unidad.
    ("unidad", bg("C:/Windows/fondo.png"), nothing, "no es un nombre de fichero válido"),
    # Ruta absoluta estilo Unix.
    ("absoluta", bg("/etc/fondo.png"), nothing, "no es un nombre de fichero válido"),
    # Barra invertida: en Windows separa carpetas y permitiría «..\\».
    ("barra-invertida", bg("img\\fondo.png"), nothing, "no es un nombre de fichero válido"),
    # Ruta UNC: leería de otro equipo de la red.
    ("unc", bg("//servidor/c/x.png"), nothing, "no es un nombre de fichero válido"),
    # Flujo alternativo de NTFS («fichero:flujo»).
    ("ads", bg("fondo.png:oculto"), nothing, "no es un nombre de fichero válido"),
    # «CON.png» pasa el patrón del modelo, pero en Windows abre la consola.
    ("con", bg("CON.png"), nothing, "nombre reservado de Windows"),
    # En minúsculas y con otra extensión sigue siendo el dispositivo NUL.
    ("nul-fuente", font_widget("nul.ttf"), nothing, "nombre reservado de Windows"),
    # COM3 es justo el puerto de la pantalla: abrirlo como imagen lo bloquearía.
    ("com3", bg("COM3.png"), nothing, "nombre reservado de Windows"),
    # Una carpeta con nombre reservado también es un dispositivo.
    ("aux-carpeta", bg("aux/x.png"), nothing, "nombre reservado de Windows"),
    # SVG puede llevar scripts y referencias externas: fuera.
    ("svg", bg("fondo.svg"), put("fondo.svg", b"<svg/>"), "extensión no permitida"),
    # Un ejecutable disfrazado de recurso.
    ("exe", bg("fondo.exe"), put("fondo.exe", b"MZ"), "extensión no permitida"),
    # Una imagen en el hueco de la fuente (y viceversa) no vale.
    (
        "png-como-fuente",
        font_widget("fondo.png"),
        put("fondo.png", png_bytes()),
        "extensión no permitida",
    ),
    # Fichero que no existe.
    ("no-existe", bg("fondo.png"), nothing, "no existe"),
    # Una carpeta con nombre de imagen no es un fichero.
    ("carpeta", bg("fondo.png"), mkdir("fondo.png"), "no existe"),
    # Texto con extensión .png: la firma real no cuadra.
    ("png-falso", bg("fondo.png"), put("fondo.png", b"hola, soy un texto"), "no es de verdad"),
    # Un JPEG renombrado a .png: tampoco cuadra la firma.
    ("jpeg-como-png", bg("fondo.png"), put("fondo.png", jpeg_bytes()), "no es de verdad"),
    # Un ejecutable renombrado a .png.
    (
        "exe-como-png",
        bg("fondo.png"),
        put("fondo.png", b"MZ\x90\x00" + b"\0" * 100),
        "no es de verdad",
    ),
    # Firma de PNG buena pero el resto basura.
    (
        "png-cabecera-falsa",
        bg("fondo.png"),
        put("fondo.png", b"\x89PNG\r\n\x1a\n" + b"x" * 200),
        "dañada",
    ),
    # PNG cortado por la mitad.
    ("png-truncado", bg("fondo.png"), put("fondo.png", truncated_png()), "dañada"),
    # PNG con un byte cambiado (suma de control mal).
    ("png-crc", bg("fondo.png"), put("fondo.png", png_bad_crc()), "dañada"),
    # Basura tras los píxeles: load() no la ve, hace falta verify().
    ("png-cola-corrupta", bg("fondo.png"), put("fondo.png", png_corrupt_tail()), "dañada"),
    # JPEG cortado: verify() no lo ve, hace falta load().
    ("jpeg-truncado", bg("fondo.jpg"), put("fondo.jpg", truncated_jpeg()), "dañada"),
    # Bomba moderada: entre 1x y 2x MAX_IMAGE_PIXELS Pillow solo avisa; el límite propio para.
    ("png-5000", bg("fondo.png"), put("fondo.png", png_header_only(5000, 5000)), "máximo es 4096"),
    # Imagen alargada: pocos píxeles en total pero un lado enorme.
    ("png-tira", bg("fondo.png"), put("fondo.png", png_header_only(10000, 1)), "máximo es 4096"),
    # Bomba grande: Pillow ya lanza DecompressionBombError al abrir.
    ("png-bomba", bg("fondo.png"), put("fondo.png", png_header_only(20000, 20000)), "bomba"),
    # Más de 10 MB.
    (
        "demasiado-grande",
        bg("fondo.png"),
        put("fondo.png", png_bytes() + b"\0" * (10 * 1024 * 1024)),
        "el máximo es 10 MB",
    ),
    # Fuente que es texto.
    ("fuente-falsa", font_widget("f.ttf"), put("f.ttf", b"no soy una fuente"), "no es de verdad"),
    # Fuente con firma buena y el resto basura: FreeType la rechaza.
    ("fuente-basura", font_widget("f.otf"), put("f.otf", b"OTTO" + b"\xff" * 500), "dañada"),
]


@pytest.mark.parametrize(
    ("theme", "setup", "expected"),
    [pytest.param(t, s, e, id=i) for i, t, s, e in MALICIOUS_JSON],
)
def test_malicious_theme_rejected(
    tmp_path: Path, theme: dict | str, setup: Setup, expected: str
) -> None:
    folder = write_theme(tmp_path / "t", theme)
    setup(folder)
    with pytest.raises(ThemeError) as info:
        load_theme(folder)
    message = str(info.value)
    assert expected in message
    assert "Traceback" not in message


def test_missing_theme_json(tmp_path: Path) -> None:
    (tmp_path / "t").mkdir()
    with pytest.raises(ThemeError, match=r"Falta el fichero theme.json"):
        load_theme(tmp_path / "t")


def test_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(ThemeError, match="No encuentro la carpeta"):
        load_theme(tmp_path / "no-esta")


def test_error_names_the_file(tmp_path: Path) -> None:
    folder = write_theme(tmp_path / "t", bg("mi-fondo.png"))
    with pytest.raises(ThemeError, match=r"mi-fondo.png"):
        load_theme(folder)


# --- Enlaces simbólicos y junctions --------------------------------------------------


@pytest.mark.skipif(sys.platform != "win32", reason="las junctions son de Windows")
def test_junction_pointing_outside_is_rejected(tmp_path: Path) -> None:
    import _winapi

    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "x.png").write_bytes(png_bytes())
    folder = write_theme(tmp_path / "t", bg("img/x.png"))
    # Una junction no pide permisos de administrador: es el enlace que puede fabricar
    # cualquiera, así que es el que hay que probar.
    _winapi.CreateJunction(str(outside), str(folder / "img"))
    with pytest.raises(ThemeError, match="fuera de la carpeta"):
        load_theme(folder)


def test_symlink_pointing_outside_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "secreto.png"
    outside.write_bytes(png_bytes())
    folder = write_theme(tmp_path / "t", bg("x.png"))
    try:
        os.symlink(outside, folder / "x.png")
    except OSError:
        pytest.skip("crear enlaces simbólicos pide permisos en este Windows")
    with pytest.raises(ThemeError, match="fuera de la carpeta"):
        load_theme(folder)


# --- Nombres reservados ---------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["CON", "con", "CON.png", "Nul.tar.gz", "aux ", "COM1", "lpt9.gif", "COM¹.png", "CONIN$"],
)
def test_reserved_names(name: str) -> None:
    assert is_reserved_windows_name(name)


@pytest.mark.parametrize("name", ["console.png", "icon.png", "com.png", "com10.png", "nulo.ttf"])
def test_not_reserved_names(name: str) -> None:
    assert not is_reserved_windows_name(name)


def test_check_relpath_rejects_reserved_names_by_itself() -> None:
    # Hoy el modelo ya para «CON.png» antes de llegar aquí; esta defensa del cargador
    # no depende de él, y su mensaje propio demuestra que es ella la que actúa.
    for rel in ["CON.png", "aux/x.png", "img/COM3.gif"]:
        with pytest.raises(ThemeError, match="nombre reservado de Windows"):
            cargar._check_relpath(rel, "La imagen")


# --- Fuentes que se abren pero fallan al pintar ----------------------------------------


def sfnt_table(data: bytes, tag: bytes) -> tuple[int, int]:
    """(desplazamiento, longitud) de la tabla ``tag`` de una fuente TrueType."""
    count = struct.unpack(">H", data[4:6])[0]
    for i in range(count):
        entry = data[12 + 16 * i : 28 + 16 * i]
        if entry[:4] == tag:
            _, offset, length = struct.unpack(">III", entry[4:])
            return offset, length
    raise LookupError(tag)


def font_broken_hinting() -> bytes:
    """Segoe UI con el programa «prep» (hinting) lleno de 0x28, un opcode sin definir.

    ``ImageFont.truetype`` la abre sin quejarse (FreeType no ejecuta ese bytecode al
    abrir) pero ``draw.text`` lanza ``OSError: invalid opcode``. Es determinista: no
    depende de una semilla de fuzzing sino de la estructura de la fuente.
    """
    data = bytearray(SEGOE.read_bytes())
    offset, length = sfnt_table(bytes(data), b"prep")
    data[offset : offset + length] = b"(" * length
    return bytes(data)


def font_theme(rel: str, *sizes: int) -> dict:
    box = {"x": 0, "y": 0, "width": 100, "height": 30}
    return base_theme(
        widgets=[
            {"type": "text", "text": "x", "box": box, "style": {"font": rel, "size": n}}
            for n in sizes
        ]
    )


@pytest.mark.skipif(not SEGOE.is_file(), reason="Segoe UI solo está en Windows")
def test_font_that_fails_only_when_painting_is_rejected(tmp_path: Path) -> None:
    from PIL import ImageDraw, ImageFont

    broken = tmp_path / "rota.ttf"
    broken.write_bytes(font_broken_hinting())
    # Precondiciones: se abre (por eso no bastaba con truetype) y falla al pintar.
    font = ImageFont.truetype(str(broken), 20)
    with pytest.raises(OSError):
        ImageDraw.Draw(Image.new("L", (8, 8))).text((0, 0), "0", font=font)
    del font

    folder = write_theme(tmp_path / "t", font_widget("rota.ttf"))
    shutil.copy(broken, folder / "rota.ttf")
    with pytest.raises(ThemeError, match="falla al pintar texto a tamaño 20"):
        load_theme(folder)


@pytest.mark.skipif(not SEGOE.is_file(), reason="Segoe UI solo está en Windows")
def test_font_is_painted_at_every_size_the_theme_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import ImageFont

    folder = write_theme(tmp_path / "t", font_theme("f.ttf", 37, 90, 37))
    shutil.copy(SEGOE, folder / "f.ttf")
    seen: list[int] = []
    real = ImageFont.truetype

    def spy(path, size, *args, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(size)
        return real(path, size, *args, **kwargs)

    monkeypatch.setattr(ImageFont, "truetype", spy)
    load_theme(folder)
    assert sorted(seen) == [20, 37, 90]  # los del tema, sin repetir, más el fijo


# --- open_validated_image ----------------------------------------------------------------


def two_frame_gif() -> bytes:
    frames = [Image.new("RGB", (8, 8), c) for c in ((255, 0, 0), (0, 0, 255))]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=100)
    return buf.getvalue()


def test_open_validated_image_returns_detached_rgba_copy(tmp_path: Path) -> None:
    path = tmp_path / "fondo.png"
    path.write_bytes(png_bytes((16, 8)))
    img = open_validated_image(path, "png")
    assert img.mode == "RGBA"
    assert img.size == (16, 8)
    path.unlink()  # en Windows fallaría si el fichero siguiera abierto
    assert img.getpixel((0, 0)) == (200, 30, 30, 255)


def test_open_validated_image_takes_first_gif_frame(tmp_path: Path) -> None:
    path = tmp_path / "anim.gif"
    path.write_bytes(two_frame_gif())
    assert open_validated_image(path, "GIF").getpixel((0, 0))[:3] == (255, 0, 0)


@pytest.mark.parametrize(
    ("name", "ext", "data", "expected"),
    [
        # Un PNG con extensión .jpg: solo se prueba el decodificador JPEG.
        ("x.jpg", "jpg", png_bytes(), "dañada o no es una imagen válida"),
        # Cambiada en disco tras la carga por una imagen enorme (solo cabecera).
        ("x.png", "png", png_header_only(5000, 5000), "máximo es 4096"),
        ("x.png", "png", png_header_only(10000, 1), "máximo es 4096"),
        ("x.png", "png", b"no soy una imagen", "dañada o no es una imagen válida"),
        ("x.bmp", "bmp", b"BM" + b"x" * 64, "extensión no permitida"),
    ],
)
def test_open_validated_image_rejects(
    tmp_path: Path, name: str, ext: str, data: bytes, expected: str
) -> None:
    path = tmp_path / name
    path.write_bytes(data)
    with pytest.raises(ThemeError, match=expected):
        open_validated_image(path, ext)


# --- C-15: GIF con demasiados fotogramas -------------------------------------------------


def gif_bytes(frames: int) -> bytes:
    # Cada fotograma distinto del anterior: Pillow junta los iguales al guardar.
    images = [Image.new("L", (4, 4), (i * 37) % 256) for i in range(frames)]
    buf = io.BytesIO()
    images[0].save(buf, "GIF", save_all=True, append_images=images[1:], duration=10, loop=0)
    return buf.getvalue()


def test_gif_up_to_the_frame_limit_loads(tmp_path: Path) -> None:
    folder = write_theme(tmp_path / "t", bg("anim.gif"))
    (folder / "anim.gif").write_bytes(gif_bytes(cargar.MAX_GIF_FRAMES))
    load_theme(folder)


def test_gif_over_the_frame_limit_is_rejected(tmp_path: Path) -> None:
    folder = write_theme(tmp_path / "t", bg("anim.gif"))
    (folder / "anim.gif").write_bytes(gif_bytes(cargar.MAX_GIF_FRAMES + 1))
    with pytest.raises(ThemeError, match="fotogramas"):
        load_theme(folder)


@pytest.mark.parametrize(
    "theme_json",
    [
        '{"name": 5}',  # theme.json que no cumple el formato: lista de problemas
        '{"format": "luzeira-theme/1", "name": "x", "orientation": "landscape",'
        ' "background": {"image": "no-esta.png"}}',  # un fichero del tema que falta
    ],
)
def test_a_theme_error_is_the_same_notice_in_every_language(tmp_path, theme_json):
    # C-64: el aviso de la bandeja se compara con ``ident`` y se reescribe con ``again``
    # al cambiar de idioma: el mensaje entero (también sus detalles) tiene que llevar Text.
    from luzeira import idioma
    from luzeira.servicio import _theme_message

    (tmp_path / "theme.json").write_text(theme_json, encoding="utf-8")

    def notice():
        with pytest.raises(cargar.ThemeError) as caught:
            cargar.load_theme(tmp_path)
        return _theme_message(tmp_path, caught.value)

    spanish = notice()
    idioma.set_language("en")
    english = notice()
    assert idioma.ident(spanish) == idioma.ident(english)
    assert idioma.again(spanish) == str(english)  # el español, reescrito: todo en inglés
