"""Tests del motor de render, sin pantalla ni sensores reales."""

import os
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image, ImageFont

from luzeira.render import Renderer, format_clock, format_reading
from luzeira.sensores.base import Reading
from luzeira.temas.modelo import SensorWidget, Theme


def font_for(style):
    try:
        return ImageFont.truetype("segoeui.ttf", style.size)
    except OSError:
        return ImageFont.load_default(size=style.size)


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def make_theme(*widgets, orientation="landscape", background=None):
    data = {
        "format": "luzeira-theme/1",
        "name": "Prueba",
        "orientation": orientation,
        "widgets": list(widgets),
    }
    if background:
        data["background"] = background
    return Theme.model_validate(data)


def box(x=0, y=0, w=100, h=40):
    return {"x": x, "y": y, "width": w, "height": h}


# --- formatos ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("HH:MM", "09:05"),
        ("HH:MM:SS", "09:05:07"),
        ("date-short", "17/11/2026"),
        ("weekday", "martes"),
        ("date-long", "martes, 17 de noviembre"),
    ],
)
def test_clock_formats_in_spanish(fmt, expected):
    assert format_clock(fmt, datetime(2026, 11, 17, 9, 5, 7)) == expected


def sensor_widget(**kw):
    return SensorWidget.model_validate({"type": "sensor", "box": box(), "sensor": "x.y", **kw})


def test_reading_without_value_is_dash_never_zero():
    assert format_reading(sensor_widget(prefix="GPU "), Reading(None, "°C", "sin datos")) == "GPU —"


def test_reading_format_fields():
    w = sensor_widget(decimals=1, prefix="CPU ", suffix="!")
    assert format_reading(w, Reading(42.26, "%")) == "CPU 42,3 %!"  # coma decimal en español
    assert format_reading(w, Reading(42.26, "%"), decimal_sep=".") == "CPU 42.3 %!"
    assert format_reading(sensor_widget(show_unit=False), Reading(42.6, "%")) == "43"


def test_text_sensor_is_shown_and_clipped():
    # C-12: sensores de texto (nombre de la GPU…), recortados a max_chars con «…».
    w = sensor_widget(prefix="GPU: ", max_chars=10)
    assert format_reading(w, Reading(None, "", text="RTX 4070")) == "GPU: RTX 4070"
    assert format_reading(w, Reading(None, "", text="GeForce RTX 4070 SUPER")) == "GPU: GeForce R…"
    # Sin texto ni número: «—», como cualquier sensor sin dato.
    assert format_reading(w, Reading(None, "", reason="no")) == "GPU: —"


def test_reading_format_cannot_reach_python_internals():
    # El tema no puede inyectar un formato: prefix/suffix se pegan como texto literal.
    w = sensor_widget(prefix="{0.__class__}", suffix="{x}")
    assert format_reading(w, Reading(1, "")) == "{0.__class__}1{x}"


# --- capas y cambios ------------------------------------------------------------


def test_full_frame_has_theme_size_and_background():
    theme = make_theme(background={"color": "#102030"})
    r = Renderer(theme, font_for=font_for, read_sensor=lambda s: Reading(1, ""))
    frame = r.full_frame()
    assert frame.size == (480, 320)
    assert frame.getpixel((479, 319)) == (0x10, 0x20, 0x30)


def test_static_text_is_painted_once_and_never_sent_again():
    theme = make_theme(
        {"type": "text", "text": "Hola", "box": box()},
        {"type": "sensor", "sensor": "a.b", "box": box(0, 100)},
    )
    r = Renderer(theme, font_for=font_for, read_sensor=lambda s: Reading(1, ""))
    frame = r.full_frame()
    # El texto está pintado en la base (no es solo fondo negro)…
    assert frame.crop((0, 0, 100, 40)).getbbox() is not None
    # …y nunca se reenvía: solo podría salir el sensor, que no cambia.
    assert r.changed() == []


def test_zero_is_shown_as_zero_not_dash():
    # RQ-05 al revés: un 0 real (ventilador parado) se muestra «0», no «—».
    assert format_reading(sensor_widget(), Reading(0.0, "%")) == "0 %"


def test_only_changed_widgets_are_sent():
    values = {"a.temp": 40.0, "b.temp": 50.0}
    theme = make_theme(
        {"type": "sensor", "sensor": "a.temp", "box": box(0, 0)},
        {"type": "sensor", "sensor": "b.temp", "box": box(0, 100)},
    )
    clock = FakeClock()
    r = Renderer(
        theme,
        font_for=font_for,
        read_sensor=lambda s: Reading(values[s], "°C"),
        clock=clock,
    )
    r.full_frame()
    clock.t = 1.0
    assert r.changed() == []  # nada ha cambiado
    values["b.temp"] = 51.0
    clock.t = 2.0
    patches = r.changed()
    assert len(patches) == 1
    p = patches[0]
    # Solo el rectángulo que cambia, dentro de la caja del widget B (0,100 100x40)…
    assert p.x >= 0 and p.x + p.image.width <= 100
    assert p.y >= 100 and p.y + p.image.height <= 140
    # …y más pequeño que la caja entera: «50» → «51» solo cambia el segundo dígito.
    assert p.image.width * p.image.height < 100 * 40


def test_applying_patches_reproduces_the_full_frame():
    # La propiedad que importa: pegar los trozos sobre lo que había en pantalla da
    # exactamente la imagen completa nueva. Si el recorte fallara, quedarían restos.
    values = {"a.b": 9.0, "c.d": 100.0, "e.f": 0.0}
    theme = make_theme(
        {"type": "sensor", "sensor": "a.b", "box": box(0, 0, 200, 60), "style": {"size": 40}},
        {"type": "bar", "sensor": "c.d", "box": box(0, 80, 300, 20)},
        {"type": "sensor", "sensor": "e.f", "decimals": 1, "box": box(0, 120, 300, 60)},
    )
    clock = FakeClock()
    r = Renderer(
        theme, font_for=font_for, read_sensor=lambda s: Reading(values[s], "%"), clock=clock
    )
    screen = r.full_frame()
    for t, new in enumerate(
        [{"a.b": 10.0}, {"c.d": 35.0, "e.f": 12.3}, {"a.b": 1.0, "e.f": 8.8}], start=1
    ):
        values.update(new)
        clock.t = float(t)
        for p in r.changed():
            screen.paste(p.image, (p.x, p.y))
        expected = Renderer(
            theme, font_for=font_for, read_sensor=lambda s: Reading(values[s], "%")
        ).full_frame()
        assert screen.tobytes() == expected.tobytes()


def test_overlapping_widgets_compose_in_theme_order():
    # D-11: lo que va después en el tema queda encima. Una barra roja tapada por un texto
    # con fondo azul: donde se cruzan manda el texto; fuera, la barra.
    theme = make_theme(
        {
            "type": "bar",
            "sensor": "a.b",
            "fill": "#ff0000",
            "track": "#ff0000",
            "box": box(0, 0, 200, 40),
        },
        {
            "type": "text",
            "text": "",
            "style": {"background": "#0000ff"},
            "box": box(100, 0, 200, 40),
        },
    )
    frame = Renderer(theme, font_for=font_for, read_sensor=lambda s: Reading(50, "%")).full_frame()
    assert frame.getpixel((50, 20)) == (255, 0, 0)  # solo la barra
    assert frame.getpixel((150, 20)) == (0, 0, 255)  # el texto, encima de la barra
    assert frame.getpixel((250, 20)) == (0, 0, 255)  # solo el texto


def test_patches_with_overlaps_reproduce_the_full_frame():
    # Antes del D-11, un widget dinámico que se repintaba borraba al que tenía encima o
    # debajo, y el borrado se quedaba en pantalla. Ahora se recompone la zona entera.
    values = {"a.b": 10.0, "c.d": 90.0}
    theme = make_theme(
        {
            "type": "bar",
            "sensor": "a.b",
            "fill": "#00ff00",
            "track": "#123456",
            "box": box(0, 0, 300, 30),
        },
        {"type": "sensor", "sensor": "c.d", "box": box(100, 0, 150, 30), "style": {"size": 24}},
        {
            "type": "bar",
            "sensor": "c.d",
            "fill": "#ff00ff",
            "track": "#222222",
            "box": box(200, 10, 200, 40),
        },
    )
    clock = FakeClock()
    r = Renderer(
        theme, font_for=font_for, read_sensor=lambda s: Reading(values[s], "%"), clock=clock
    )
    screen = r.full_frame()
    for t, new in enumerate([{"a.b": 80.0}, {"c.d": 5.0}, {"a.b": 0.0, "c.d": 55.0}], start=1):
        values.update(new)
        clock.t = float(t)
        for p in r.changed():
            screen.paste(p.image, (p.x, p.y))
        expected = Renderer(
            theme, font_for=font_for, read_sensor=lambda s: Reading(values[s], "%")
        ).full_frame()
        assert screen.tobytes() == expected.tobytes()


def test_interval_is_respected():
    calls = []

    def read(sensor):
        calls.append(sensor)
        return Reading(len(calls), "")

    theme = make_theme({"type": "sensor", "sensor": "a.b", "interval": 5, "box": box()})
    clock = FakeClock()
    r = Renderer(theme, font_for=font_for, read_sensor=read, clock=clock)
    r.full_frame()
    clock.t = 4.0
    assert r.changed() == [] and len(calls) == 1
    clock.t = 5.0
    assert len(r.changed()) == 1 and len(calls) == 2


def test_tick_jitter_does_not_skip_updates():
    # Hallazgo del revisor (fase 3): ticks que llegan unos ms antes de lo previsto
    # (reloj de Windows a saltos de 15,6 ms + poll de ~15 ms) se saltaban la mitad.
    calls = []

    def read(sensor):
        calls.append(sensor)
        return Reading(len(calls), "")

    theme = make_theme({"type": "sensor", "sensor": "a.b", "box": box()})
    clock = FakeClock()
    clock.t = 0.015
    r = Renderer(theme, font_for=font_for, read_sensor=read, clock=clock)
    r.full_frame()
    sent = 0
    for t in (1.012, 2.016, 3.013, 4.010, 5.015, 6.011):
        clock.t = t
        sent += len(r.changed())
    assert sent == 6


def test_after_suspend_it_does_not_burst():
    theme = make_theme({"type": "sensor", "sensor": "a.b", "box": box()})
    clock = FakeClock()
    value = [0]

    def read(sensor):
        value[0] += 1
        return Reading(value[0], "")

    r = Renderer(theme, font_for=font_for, read_sensor=read, clock=clock)
    r.full_frame()
    clock.t = 3600.0  # una hora suspendido
    assert len(r.changed()) == 1
    assert r.changed() == []  # mismo instante: no hay ráfaga para «ponerse al día»


def test_clock_hhmm_only_changes_once_per_minute():
    moment = [datetime(2026, 11, 17, 9, 5, 0)]
    theme = make_theme({"type": "clock", "format": "HH:MM", "box": box(w=200, h=60)})
    clock = FakeClock()
    r = Renderer(
        theme,
        font_for=font_for,
        read_sensor=lambda s: Reading(None, ""),
        now=lambda: moment[0],
        clock=clock,
    )
    r.full_frame()
    sent = 0
    for second in range(1, 61):
        clock.t = second
        moment[0] = datetime(2026, 11, 17, 9, 5 + second // 60, second % 60)
        sent += len(r.changed())
    assert sent == 1  # solo al pasar a las 09:06


def test_bar_fill_and_clamp():
    theme = make_theme(
        {
            "type": "bar",
            "sensor": "a.b",
            "fill": "#ff0000",
            "track": "#123456",  # distinto del fondo, para que el test vea la pista
            "box": box(w=100, h=10),
        }
    )
    value = [50.0]
    r = Renderer(theme, font_for=font_for, read_sensor=lambda s: Reading(value[0], "%"))
    frame = r.full_frame()
    assert frame.getpixel((49, 5)) == (255, 0, 0)
    assert frame.getpixel((50, 5)) == (0x12, 0x34, 0x56)
    value[0] = 250.0  # fuera de rango: se queda en lleno, no se sale
    assert r.full_frame().getpixel((99, 5)) == (255, 0, 0)


def test_bar_without_value_is_not_drawn_like_zero():
    # RQ-05: sin dato solo el contorno; con 0 % la pista llena. Se tienen que distinguir.
    def bar_frame(value):
        theme = make_theme(
            {"type": "bar", "sensor": "a.b", "fill": "#ff0000", "track": "#123456", "box": box()}
        )
        r = Renderer(theme, font_for=font_for, read_sensor=lambda s: Reading(value, "%"))
        return r.full_frame()

    empty, zero = bar_frame(None), bar_frame(0.0)
    assert empty.getpixel((0, 0)) == (0x12, 0x34, 0x56)  # contorno
    assert empty.getpixel((50, 20)) == (0, 0, 0)  # interior: fondo
    assert zero.getpixel((50, 20)) == (0x12, 0x34, 0x56)  # 0 %: pista llena


def test_cover_background_with_extreme_aspect_is_cheap(tmp_path):
    # Revisor fase 3: un fondo 4096x1 hacía reservar ~1,26 GB al escalar antes de recortar.
    strip = tmp_path / "tira.png"
    Image.new("RGB", (4096, 1), "blue").save(strip)
    theme = make_theme(background={"image": "tira.png"})
    r = Renderer(
        theme, font_for=font_for, read_sensor=lambda s: Reading(None, ""), files={"tira.png": strip}
    )
    assert r.full_frame().getpixel((240, 160)) == (0, 0, 255)


def test_broken_theme_font_falls_back_instead_of_crashing():
    calls = []

    class BrokenFont(ImageFont.FreeTypeFont):
        """Fuente real que falla al rasterizar, como el bytecode de hinting roto."""

        def getmask2(self, *a, **k):
            raise OSError("invalid opcode")

    try:
        broken = BrokenFont(ImageFont.truetype("segoeui.ttf", 20).path, 20)
    except OSError:
        pytest.skip("hace falta Segoe UI para fabricar la fuente rota")

    def font(style):
        calls.append(style.font)
        return broken if style.font else font_for(style)

    theme = make_theme(
        {"type": "text", "text": "Hola", "style": {"font": "rota.ttf"}, "box": box()}
    )
    frame = Renderer(theme, font_for=font, read_sensor=lambda s: Reading(None, "")).full_frame()
    assert calls == ["rota.ttf", None]  # probó la del tema y cayó a la de Luzeira
    assert frame.crop((0, 0, 100, 40)).getbbox() is not None  # y pintó algo


def test_image_widget_requires_validated_file(tmp_path):
    img = tmp_path / "logo.png"
    Image.new("RGB", (10, 10), "red").save(img)
    theme = make_theme({"type": "image", "file": "logo.png", "box": box(0, 0, 20, 20)})
    with pytest.raises(KeyError):
        Renderer(theme, font_for=font_for, read_sensor=lambda s: Reading(None, ""))
    r = Renderer(
        theme,
        font_for=font_for,
        read_sensor=lambda s: Reading(None, ""),
        files={"logo.png": img},
    )
    assert r.full_frame().getpixel((10, 10)) == (255, 0, 0)


# --- C-62: textos del tema traducidos -------------------------------------------------------


def test_prefix_and_suffix_follow_the_language_when_the_theme_translates_them():
    from luzeira import idioma

    w = sensor_widget(
        prefix="usada ", prefix_i18n={"en": "used "}, suffix="", suffix_i18n={"en": "!"}
    )
    assert format_reading(w, Reading(4.0, "GB")) == "usada 4 GB"
    idioma.set_language("en")
    assert format_reading(w, Reading(4.0, "GB")) == "used 4 GB!"
    idioma.set_language("de")  # sin traducción de ese idioma: el texto de siempre
    assert format_reading(w, Reading(4.0, "GB")).startswith("usada ")


def test_a_text_widget_is_drawn_in_the_active_language(monkeypatch):
    from luzeira import idioma

    drawn = []
    monkeypatch.setattr(Renderer, "_draw_text", lambda self, image, text, style: drawn.append(text))
    widget = {"type": "text", "box": box(), "text": "RED", "text_i18n": {"en": "NET"}}
    theme = make_theme(widget)

    def render():
        # Al crearse, el Renderer pinta ya las capas fijas (los textos).
        Renderer(theme, font_for=font_for, read_sensor=lambda s: Reading(1, ""))

    render()
    idioma.set_language("en")
    render()
    assert drawn == ["RED", "NET"]


@pytest.mark.parametrize(
    "bad",
    [
        {"EN": "x"},
        {"eng": "x"},
        {"e": "x"},
        {"en": "x" * 201},
        {f"a{c}": "x" for c in "abcdefghijklmnopqrstu"},
    ],
)
def test_theme_translations_are_short_language_codes_and_short_texts(bad):
    from pydantic import ValidationError

    from luzeira.temas.modelo import TextWidget

    with pytest.raises(ValidationError):
        TextWidget.model_validate({"type": "text", "box": box(), "text": "RED", "text_i18n": bad})


# --- C-62: fecha y coma decimal en el idioma activo -----------------------------------------


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("HH:MM", "09:05"),
        ("date-short", "2026-11-07"),  # ISO: nadie lo lee al revés (C-63)
        ("weekday", "Saturday"),
        ("date-long", "Saturday, November 7"),
    ],
)
def test_clock_formats_in_english(fmt, expected):
    from luzeira import idioma

    idioma.set_language("en")
    assert format_clock(fmt, datetime(2026, 11, 7, 9, 5, 7)) == expected


def test_decimal_separator_follows_the_language():
    from luzeira import idioma

    w = sensor_widget(decimals=1)
    assert format_reading(w, Reading(14.06, "GB")) == "14,1 GB"
    idioma.set_language("en")
    assert format_reading(w, Reading(14.06, "GB")) == "14.1 GB"
    assert format_reading(w, Reading(14.06, "GB"), decimal_sep=",") == "14,1 GB"  # a mano manda


YAHEI = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"


@pytest.mark.skipif(not YAHEI.is_file(), reason="Microsoft YaHei viene con Windows")
def test_chinese_text_is_drawn_with_the_windows_cjk_font():
    # C-65: ni la fuente del tema ni Segoe UI tienen ideogramas; sin la reserva saldrían
    # cuadros. Tiene que salir lo mismo que pintando directamente con YaHei.
    from luzeira import idioma

    idioma.set_language("zh")
    theme = make_theme({"type": "text", "text": "内存", "box": box(), "style": {"size": 20}})

    def frame(fonts):
        return Renderer(theme, font_for=fonts, read_sensor=lambda s: Reading(None, "")).full_frame()

    yahei = frame(lambda style: ImageFont.truetype(str(YAHEI), style.size))
    assert frame(font_for).tobytes() == yahei.tobytes()
