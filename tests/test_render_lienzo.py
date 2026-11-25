"""El motor pinta un tema en una pantalla de otro tamaño: escalado uniforme, centrado y
compuesto a la resolución de la pantalla (no la imagen ya hecha agrandada)."""

from __future__ import annotations

import pytest
from PIL import Image, ImageChops, ImageFont

from luzeira.render import Renderer
from luzeira.render.motor import fit
from luzeira.sensores.base import Reading
from luzeira.temas.modelo import Theme

ORANGE = (255, 140, 0)
BLUE = (0, 0, 255)


def make_theme(*widgets, orientation="portrait", canvas=None, background=None):
    data = {
        "format": "luzeira-theme/1",
        "name": "Prueba",
        "orientation": orientation,
        "background": background or {"color": "#102030"},
        "widgets": list(widgets),
    }
    if canvas:
        data["canvas"] = {"width": canvas[0], "height": canvas[1]}
    return Theme.model_validate(data)


def box(x, y, w, h):
    return {"x": x, "y": y, "width": w, "height": h}


class Fonts:
    """Fuente de pruebas que apunta los tamaños pedidos."""

    def __init__(self) -> None:
        self.sizes: list[int] = []

    def __call__(self, style):
        self.sizes.append(style.size)
        return ImageFont.load_default(size=style.size)


def renderer(theme, size=None, fonts=None, files=None, value=50.0):
    return Renderer(
        theme,
        font_for=fonts or Fonts(),
        read_sensor=lambda s: Reading(value, "%"),
        files=files,
        output_size=size,
    )


# --- la cuenta ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("theme_size", "screen", "expected"),
    [
        ((320, 480), (320, 480), (1, 0, 0, 320, 480)),
        ((320, 480), (480, 800), (1.5, 0, 40, 480, 720)),  # 5": franjas arriba y abajo
        ((320, 480), (600, 1024), (1.875, 0, 62, 600, 900)),  # 7"
        ((320, 480), (480, 480), (1, 80, 0, 320, 480)),  # 2,1": franjas a los lados
        ((320, 480), (480, 1920), (1.5, 0, 600, 480, 720)),  # 8,8"
        ((480, 320), (1920, 480), (1.5, 600, 0, 720, 480)),
        ((480, 800), (320, 480), (0.6, 16, 0, 288, 480)),  # uno grande en una pequeña
    ],
)
def test_fit_is_uniform_and_centred(theme_size, screen, expected):
    f = fit(theme_size, screen)
    assert f.scale == pytest.approx(expected[0])
    assert (f.x, f.y, f.width, f.height) == expected[1:]


def test_fit_keeps_touching_boxes_touching():
    f = fit((320, 480), (600, 1024))  # 1,875: muchos bordes caen en medio píxel
    for x in range(1, 300, 7):
        for width in (3, 7, 13):
            left = f.box(x, 0, width, 10)
            right = f.box(x + width, 0, 10, 10)
            assert left[2] == right[0]


# --- lo de siempre no cambia ----------------------------------------------------------


def test_same_size_is_exactly_as_without_output_size():
    theme = make_theme(
        {"type": "text", "text": "Hola", "box": box(10, 10, 200, 40)},
        {"type": "bar", "sensor": "x.y", "box": box(10, 100, 300, 20)},
        {"type": "sensor", "sensor": "x.y", "box": box(10, 200, 200, 50)},
    )
    plain = renderer(theme).full_frame()
    same = renderer(theme, (320, 480)).full_frame()
    assert plain.size == same.size == (320, 480)
    assert ImageChops.difference(plain, same).getbbox() is None
    # Y en su sitio exacto, sin desplazar (la comparación entera está en el informe de
    # la fase: los 32 temas incluidos, píxel a píxel contra la versión anterior).
    exact = make_theme(
        {"type": "text", "text": "", "box": box(10, 20, 30, 40), "style": {"background": "#0000ff"}}
    )
    frame = renderer(exact, (320, 480)).full_frame()
    assert frame.getpixel((10, 20)) == frame.getpixel((39, 59)) == BLUE
    assert frame.getpixel((9, 20)) != BLUE and frame.getpixel((40, 59)) != BLUE


# --- escalado -----------------------------------------------------------------------


def test_bigger_screen_scales_boxes_and_fills_bands_with_theme_background():
    theme = make_theme(
        {"type": "text", "text": "", "box": box(0, 0, 320, 480), "style": {"background": "#0000ff"}}
    )
    frame = renderer(theme, (480, 800)).full_frame()
    assert frame.size == (480, 800)
    # El tema ocupa 480x720 centrado: franjas de 40 px con el color de fondo del tema.
    assert frame.getpixel((0, 0)) == frame.getpixel((479, 799)) == (0x10, 0x20, 0x30)
    assert frame.getpixel((0, 39)) == (0x10, 0x20, 0x30)
    assert frame.getpixel((0, 40)) == frame.getpixel((479, 759)) == BLUE
    assert frame.getpixel((0, 760)) == (0x10, 0x20, 0x30)


def test_text_is_drawn_with_scaled_font_not_upscaled():
    fonts = Fonts()
    theme = make_theme(
        {"type": "text", "text": "88", "box": box(0, 0, 160, 60), "style": {"size": 20}}
    )
    renderer(theme, (480, 800), fonts).full_frame()
    assert 30 in fonts.sizes and 20 not in fonts.sizes  # 20 x 1,5, pintado a ese tamaño


def test_scaled_theme_is_drawn_like_the_same_theme_made_for_that_screen():
    # Compuesto a la resolución de la pantalla: un tema de 320x480 en la 5" sale igual,
    # píxel a píxel, que el mismo tema hecho a mano para 480x800 (cajas y letras x1,5,
    # 40 px más abajo). Agrandar la imagen ya hecha no daría esto (texto borroso).
    small = make_theme(
        {"type": "text", "text": "Luzeira 42", "box": box(0, 0, 320, 80), "style": {"size": 30}}
    )
    native = make_theme(
        {"type": "text", "text": "Luzeira 42", "box": box(0, 40, 480, 120), "style": {"size": 45}},
        canvas=(480, 800),
    )
    scaled = renderer(small, (480, 800)).full_frame()
    assert ImageChops.difference(scaled, renderer(native).full_frame()).getbbox() is None
    upscaled = renderer(small).full_frame().resize((480, 720), Image.Resampling.BICUBIC)
    assert ImageChops.difference(scaled.crop((0, 40, 480, 760)), upscaled).getbbox() is not None


def test_bar_scales_and_its_empty_outline_gets_thicker():
    theme = make_theme(
        {"type": "bar", "sensor": "x.y", "box": box(0, 0, 100, 20), "fill": "#ff8c00"}
    )
    frame = renderer(theme, (640, 960), value=100.0).full_frame()  # escala 2
    assert frame.getpixel((199, 39)) == ORANGE and frame.getpixel((200, 40)) != ORANGE
    empty = Renderer(
        theme, font_for=Fonts(), read_sensor=lambda s: Reading(None, "%"), output_size=(640, 960)
    ).full_frame()
    track = (0x2A, 0x2F, 0x3A)
    assert empty.getpixel((0, 10)) == empty.getpixel((1, 10)) == track  # 2 px de contorno
    assert empty.getpixel((2, 10)) != track


def test_background_image_covers_only_the_theme_area(tmp_path):
    img = tmp_path / "fondo.png"
    Image.new("RGB", (32, 48), ORANGE).save(img)
    theme = make_theme(background={"color": "#102030", "image": "fondo.png"})
    frame = renderer(theme, (480, 800), files={"fondo.png": img}).full_frame()
    assert frame.getpixel((10, 20)) == (0x10, 0x20, 0x30)
    assert frame.getpixel((10, 60)) == ORANGE


def test_image_widget_is_scaled_from_the_original(tmp_path):
    img = tmp_path / "foto.png"
    Image.new("RGB", (400, 400), ORANGE).save(img)
    theme = make_theme({"type": "image", "file": "foto.png", "box": box(0, 0, 100, 100)})
    frame = renderer(theme, (480, 800), files={"foto.png": img}).full_frame()
    assert frame.getpixel((149, 40 + 149)) == ORANGE  # 100 px del tema = 150 en pantalla
    assert frame.getpixel((151, 40 + 151)) == (0x10, 0x20, 0x30)


def test_canvas_theme_on_its_own_screen_and_on_a_small_one():
    theme = make_theme(
        {
            "type": "text",
            "text": "",
            "box": box(400, 700, 80, 100),
            "style": {"background": "#0000ff"},
        },
        canvas=(480, 800),
    )
    own = renderer(theme).full_frame()
    assert own.size == (480, 800) and own.getpixel((479, 799)) == BLUE
    small = renderer(theme, (320, 480)).full_frame()  # escala 0,6: 288x480 centrado
    assert small.size == (320, 480)
    assert small.getpixel((16 + 287, 479)) == BLUE
    assert small.getpixel((319, 479)) == (0x10, 0x20, 0x30)


# --- cambiar de pantalla con el tema ya cargado -----------------------------------------


def test_set_output_size_applies_on_next_full_frame():
    theme = make_theme({"type": "sensor", "sensor": "x.y", "box": box(0, 0, 160, 40)})
    r = renderer(theme)
    assert r.full_frame().size == (320, 480)
    r.set_output_size((480, 800))
    patches = r.changed()  # sin imagen en pantalla de ese tamaño: entera
    assert [(p.x, p.y, p.image.size) for p in patches] == [(0, 0, (480, 800))]
    assert r.size == (480, 800)
    r.set_output_size((480, 800))  # el mismo: no repinta nada
    assert r.changed() == []


def test_failed_rebuild_keeps_the_old_size(monkeypatch):
    # Con un sensor (se repinta en cada imagen completa): si el escalado a medias se
    # quedara, su texto saldría con la letra de la pantalla que falló.
    theme = make_theme({"type": "sensor", "sensor": "x.y", "box": box(0, 0, 100, 40)})
    r = renderer(theme)
    before = r.full_frame()

    def broken(widget):
        raise OSError("la imagen ha cambiado en disco")

    monkeypatch.setattr(r, "_make_layer", broken)
    r.set_output_size((480, 800))
    with pytest.raises(OSError):
        r.full_frame()
    monkeypatch.undo()
    assert r.size == (320, 480)
    r.set_output_size((320, 480))
    assert ImageChops.difference(r.full_frame(), before).getbbox() is None


# --- remate de la revisión ------------------------------------------------------------


def _edge_widgets(width, height):
    """Barras y sensores de 1-3 px pegados a los cuatro bordes y esquinas."""
    out = []
    for k in (1, 2, 3):
        for x, y in ((0, 0), (width - k, 0), (0, height - k), (width - k, height - k)):
            out.append({"type": "bar", "sensor": "x.y", "box": box(x, y, k, k)})
        out.append({"type": "sensor", "sensor": "x.y", "box": box(width - k, height // 2, k, k)})
        out.append({"type": "bar", "sensor": "x.y", "box": box(width // 2, height - k, k, k)})
    return out


def test_every_box_and_patch_stays_inside_every_screen():
    # Con escala < 1, una caja de 1-2 px pegada al borde redondeaba fuera: el driver
    # rechazaba el parche y el hilo del servicio moría (revisor de la fase 4).
    from luzeira.temas.modelo import CANVAS_SIZES

    for canvas in CANVAS_SIZES:
        orientation = "landscape" if canvas[0] > canvas[1] else "portrait"
        theme = make_theme(*_edge_widgets(*canvas), orientation=orientation, canvas=canvas)
        for screen in CANVAS_SIZES:
            value = [10.0]
            r = Renderer(
                theme,
                font_for=Fonts(),
                read_sensor=lambda s, v=value: Reading(v[0], "%"),
                output_size=screen,
                clock=lambda v=value: 100.0 * v[0],
            )
            assert r.full_frame().size == screen
            for layer in r._layers:
                left, top, right, bottom = layer.box
                assert 0 <= left < right <= screen[0] and 0 <= top < bottom <= screen[1], (
                    canvas,
                    screen,
                    layer.box,
                )
            value[0] = 90.0
            for p in r.changed():
                assert p.x >= 0 and p.y >= 0
                assert p.x + p.image.width <= screen[0] and p.y + p.image.height <= screen[1]


def test_scaled_font_size_is_rounded_not_truncated():
    fonts = Fonts()
    theme = make_theme(
        {"type": "text", "text": "8", "box": box(0, 0, 320, 100), "style": {"size": 18}}
    )
    renderer(theme, (600, 1024), fonts).full_frame()  # 18 x 1,875 = 33,75 → 34
    assert 34 in fonts.sizes and 33 not in fonts.sizes


def test_text_that_fit_in_the_theme_is_shrunk_to_fit_when_scaled():
    # Una fuente cuyo ancho no escala en proporción (como el hinting al redondear): a
    # tamaño 30 mide 100 px, a 45 mide 160 (1,6x en vez de 1,5x).
    theme = make_theme(
        {"type": "text", "text": "activo", "box": box(0, 0, 100, 40), "style": {"size": 30}}
    )

    class Wide:
        def __init__(self, size):
            self.size = size
            self.inner = ImageFont.load_default(size=size)

        def getbbox(self, text, anchor=None):
            width = 100 if self.size <= 30 else round(self.size * 160 / 45)
            return (-width // 2, -10, width - width // 2, 10)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    r = Renderer(
        theme,
        font_for=lambda style: Wide(style.size),
        read_sensor=lambda s: Reading(1, "%"),
        output_size=(480, 800),
    )
    widget = theme.widgets[0]
    style = r._text_style(widget, "activo", (150, 60))
    assert style.size < 45  # encogida
    assert not r._overflows(style, "activo", (150, 60))
    # Uno que ya se salía en el tema no se toca.
    wide_theme = make_theme(
        {"type": "text", "text": "activo", "box": box(0, 0, 90, 40), "style": {"size": 30}}
    )
    r2 = Renderer(
        wide_theme,
        font_for=lambda style: Wide(style.size),
        read_sensor=lambda s: Reading(1, "%"),
        output_size=(480, 800),
    )
    assert r2._text_style(wide_theme.widgets[0], "activo", (135, 60)).size == 45
