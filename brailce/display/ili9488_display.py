"""
Brailce ILI9488 TFT Display – Raspberry Pi (480x320 SPI)
==========================================================

Visual display driver for the 3.5" ILI9488 / ILI9486 SPI panel.

Design notes (why it looks the way it does):
- Cheap SPI TFT panels wash out dark, low-contrast colours (navy turns into a
  blue gradient at an angle). The palette is therefore a near-neutral dark
  background with clearly brighter card surfaces and high-contrast text.
- All text uses the bundled DejaVu Sans (regular + bold, free licence, see
  fonts/LICENSE_DEJAVU.txt). It covers bullets, arrows and ellipses, so no
  glyph renders as a "tofu" box.
- Icons, bullets and Braille cells are drawn with shapes, never with font glyphs.
- One accent colour per mode (header pill, card titles, Braille dots, cursor).
"""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import re
import textwrap
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from brailce.core.events import AppMode
from brailce.display.base import DisplayDevice
from brailce.i18n.strings import get_string, get_mode_name

logger = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# Fonts
# -----------------------------------------------------------------------------

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_FONT_CANDIDATES: Dict[bool, List[str]] = {
    False: [
        os.path.join(_FONT_DIR, "DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",   # Raspberry Pi OS system copy
    ],
    True: [
        os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
}

# -----------------------------------------------------------------------------
# Design tokens
# -----------------------------------------------------------------------------


class Palette:
    BG = (9, 11, 17)
    SURFACE = (24, 28, 39)
    SURFACE_HI = (36, 42, 56)
    BORDER = (46, 53, 70)
    TEXT = (243, 246, 250)
    TEXT_2 = (178, 188, 204)
    TEXT_3 = (120, 131, 150)
    DOT_OFF = (60, 68, 88)
    DANGER = (248, 113, 113)
    DANGER_BG = (76, 20, 26)
    WARN = (251, 191, 36)
    OK = (74, 222, 128)
    STEP = (52, 211, 153)


# (header label, accent colour)
MODE_THEME: Dict[AppMode, Tuple[str, Tuple[int, int, int]]] = {
    AppMode.FREE_INPUT: ("FREE INPUT", (52, 211, 153)),
    AppMode.MATH: ("MATH MODE", (251, 191, 36)),
    AppMode.AI_ASSISTANT: ("AI ASSISTANT", (167, 139, 250)),
    AppMode.SCANNER: ("SCANNER", (56, 189, 248)),
}

# Screen geometry (480x320)
HEADER_H = 40
FOOTER_H = 30
MARGIN = 12
CONTENT_TOP = HEADER_H + 8          # 48
CONTENT_BOTTOM_GAP = 8               # space between content and footer
CARD_RADIUS = 10

# Short on-screen labels (EN / FIL). Kept local to the LCD so the frozen
# English speech/status strings in brailce.i18n.strings stay untouched.
LCD_LABELS: Dict[str, Dict[str, str]] = {
    "CHARACTER": {"en": "CHARACTER", "fil": "TITIK"},
    "BRAILLE": {"en": "BRAILLE", "fil": "BRAILLE"},
    "DOTS": {"en": "DOTS", "fil": "TULDOK"},
    "NO_DOTS": {"en": "No dots", "fil": "Walang tuldok"},
    "SPACE": {"en": "space", "fil": "espasyo"},
    "TYPED": {"en": "TYPED TEXT", "fil": "NAISULAT"},
    "SCANNED": {"en": "SCANNED TEXT", "fil": "NABASANG TEKSTO"},
    "EQUATION": {"en": "EQUATION", "fil": "EKSPRESYON"},
    "EMPTY_TYPED": {"en": "Type letters on the keypad to practice",
                    "fil": "Pumindot ng titik sa keypad para magsanay"},
    "EMPTY_SCANNED": {"en": "Nothing scanned yet", "fil": "Wala pang nababasa"},
    "EMPTY_EQUATION": {"en": "Type a problem, e.g. 2 + 3", "fil": "Mag-type ng problema, hal. 2 + 3"},
    "QUESTION": {"en": "YOUR QUESTION", "fil": "IYONG TANONG"},
    "QUESTION_SHORT": {"en": "QUESTION", "fil": "TANONG"},
    "Q_PLACEHOLDER": {"en": "Type your question on the Braille keypad",
                      "fil": "I-type ang iyong tanong sa Braille keypad"},
    "Q_EXAMPLES": {"en": "Try:  what is rain?  ·  tell a story",
                   "fil": "Subukan:  ano ang ulan?  ·  magkuwento ka"},
    "AI_ANSWER": {"en": "AI ANSWER", "fil": "SAGOT NG AI"},
    "CONTROLS": {"en": "CONTROLS", "fil": "MGA PINDUTAN"},
    "KEY_ASK": {"en": "Ask the AI", "fil": "Itanong sa AI"},
    "KEY_MODE": {"en": "Switch mode", "fil": "Palit mode"},
    "KEY_DELETE": {"en": "Delete", "fil": "Burahin"},
    "KEY_SCAN": {"en": "Scan page", "fil": "Basahin ang pahina"},
    "KEY_SOLVE": {"en": "Solve", "fil": "Lutasin"},
    "MATH_INTRO_TITLE": {"en": "GRADE 1 MATH", "fil": "MATEMATIKA (GRADE 1)"},
    "MATH_INTRO": {"en": "Type a math problem using the numbers 0 to 9.",
                   "fil": "Mag-type ng problema gamit ang 0 hanggang 9."},
    "EXAMPLES": {"en": "EXAMPLES", "fil": "HALIMBAWA"},
    "SCANNER_TITLE": {"en": "Braille Scanner", "fil": "Pagbasa ng Braille"},
    "SCANNER_TIP": {"en": "Hold the page flat, about one hand-span below the camera.",
                    "fil": "Ihiga ang pahina, mga isang dangkal sa ilalim ng kamera."},
    "ERROR": {"en": "Error", "fil": "Error"},
}


def _canvas_locked(fn):
    """Serialise canvas drawing: the scanner's preview thread and the mode/readout
    threads must never draw into the same PIL image at the same time."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._canvas_lock:
            return fn(self, *args, **kwargs)
    return wrapper


def _dots_from_unicode(ch: str) -> Optional[Set[int]]:
    """Return the raised dots (1-6) for a Unicode Braille pattern character."""
    if len(ch) != 1:
        return None
    code = ord(ch)
    if not (0x2800 <= code <= 0x28FF):
        return None
    bits = code - 0x2800
    return {i + 1 for i in range(6) if bits & (1 << i)}


def _dots_label(dots: Set[int]) -> str:
    return "-".join(str(d) for d in sorted(dots))


class LgpioShim:
    """Raspberry Pi 5 RP1 compatible GPIO shim for luma.core using native lgpio."""
    OUT = 1
    IN = 0
    HIGH = 1
    LOW = 0
    BCM = 11
    BOARD = 10

    def __init__(self, chip: int = 4) -> None:
        import lgpio
        self._lgpio = lgpio
        try:
            self._h = lgpio.gpiochip_open(chip)
        except Exception:
            self._h = lgpio.gpiochip_open(0)

    def setmode(self, mode: int) -> None:
        pass

    def setup(self, pin: int, mode: int) -> None:
        try:
            self._lgpio.gpio_claim_output(self._h, pin)
        except Exception:
            pass

    def output(self, pin: int, value: int) -> None:
        try:
            self._lgpio.gpio_write(self._h, pin, 1 if value else 0)
        except Exception:
            pass

    def cleanup(self, pin: Optional[int] = None) -> None:
        pass


class NoopBacklight:
    """Callable dummy backlight to prevent luma from claiming GPIO 18 (used by I2S audio)."""
    def __call__(self, val: bool = True) -> None:
        pass

    def cleanup(self) -> None:
        pass



class ILI9488Display(DisplayDevice):
    """
    ILI9488 SPI TFT display driver for Raspberry Pi (3.5", 480x320).
    Renders UI cards, Braille dot cells, mode indicators,
    battery percentage, and live camera preview frames.
    """

    def __init__(
        self,
        width: int = 480,
        height: int = 320,
        fb_path: Optional[str] = None,
        gpio_dc: int = 24,
        gpio_rst: int = 25,
        spi_port: int = 0,
        spi_device: int = 0,
    ) -> None:
        self._width = width
        self._height = height
        self._current_mode: Optional[AppMode] = None
        self._gpio_dc = gpio_dc
        self._gpio_rst = gpio_rst
        self._spi_port = spi_port
        self._spi_device = spi_device
        self._device = None  # luma.lcd device for direct SPI
        self._hardware_type = "canvas"
        self._init_error = None
        self._display_lock = threading.Lock()
        self._canvas_lock = threading.RLock()
        self._batch_depth = 0
        self._power_monitor: Optional[Any] = None
        self._battery_override: Optional[tuple[int, bool]] = None
        self._current_query: str = ""
        self._last_status_msg: str = "Ready"
        # Which screen layout is currently on the canvas ("input", "math_result", ...).
        self._layout: Optional[str] = None
        self._math_braille_box: Optional[Tuple[int, int, int, int]] = None

        if fb_path:
            self._fb_path = fb_path
        elif os.path.exists("/dev/fb1"):
            self._fb_path = "/dev/fb1"
        elif os.path.exists("/dev/fb0"):
            self._fb_path = "/dev/fb0"
        else:
            self._fb_path = "/dev/fb1"
        self._bpp = 16
        self._fb_file = None
        self._image = None
        self._draw = None
        self._language = "en"
        self._fonts: Dict[Tuple[int, bool], Any] = {}
        self._font_char = None
        self._font_title = None
        self._font_label = None
        self._font_body = None
        self._font_small = None
        self._font_medium = None

    # -------------------------------------------------------------------------
    # Fonts
    # -------------------------------------------------------------------------

    def _f(self, size: int, bold: bool = False) -> Any:
        """Cached TrueType font (DejaVu Sans), falling back to Pillow's default."""
        key = (int(size), bool(bold))
        if key in self._fonts:
            return self._fonts[key]
        font = None
        try:
            from PIL import ImageFont
            for path in _FONT_CANDIDATES[bool(bold)]:
                if os.path.isfile(path):
                    try:
                        font = ImageFont.truetype(path, int(size))
                        break
                    except Exception:
                        continue
            if font is None:
                try:
                    font = ImageFont.load_default(size=int(size))
                except TypeError:
                    font = ImageFont.load_default()
        except Exception:
            font = None
        self._fonts[key] = font
        return font

    def _load_font(self, size: int) -> Any:
        """Backward-compatible alias for the regular-weight font loader."""
        return self._f(size)

    @property
    def driver_name(self) -> str:
        return f"ILI9488 TFT (3.5\" 480x320, {self._hardware_type.upper()})"

    def set_power_monitor(self, monitor: Any) -> None:
        """Attach a power monitor to query live battery percentage."""
        self._power_monitor = monitor

    def initialize(self) -> None:
        """Initialize direct SPI connection, framebuffer, or buffer canvas."""
        # 0. Pillow drawing canvas (works on every platform)
        try:
            from PIL import Image, ImageDraw
            self._image = Image.new("RGB", (self._width, self._height), color=Palette.BG)
            self._draw = ImageDraw.Draw(self._image)
            self._font_char = self._f(72, True)
            self._font_title = self._f(20, True)
            self._font_label = self._f(14, True)
            self._font_body = self._f(15)
            self._font_small = self._f(13)
            self._font_medium = self._f(15, True)
            logger.info("ILI9488Display canvas initialized (%dx%d).", self._width, self._height)
        except ImportError:
            logger.warning("Pillow not installed; running in fallback mode.")


        # 1. Attempt direct SPI communication via luma.lcd (Primary for Raspberry Pi 5)
        if os.path.exists(f"/dev/spidev{self._spi_port}.{self._spi_device}"):
            for driver_mod in ["ili9488", "ili9486"]:
                if self._device is not None:
                    break
                try:
                    from luma.core.interface.serial import spi
                    import luma.lcd.device as devices
                    if hasattr(devices, driver_mod):
                        driver_cls = getattr(devices, driver_mod)
                        gpio_backend = LgpioShim(chip=4)
                        # Explicit 50ms hardware reset pulse so ILI9488 wakes reliably before SPI init
                        gpio_backend.setup(self._gpio_rst, gpio_backend.OUT)
                        gpio_backend.output(self._gpio_rst, gpio_backend.LOW)
                        time.sleep(0.05)
                        gpio_backend.output(self._gpio_rst, gpio_backend.HIGH)
                        time.sleep(0.15)
                        serial = spi(
                            port=self._spi_port,
                            device=self._spi_device,
                            gpio_DC=self._gpio_dc,
                            gpio_RST=self._gpio_rst,
                            bus_speed_hz=int(os.environ.get("BRAILCE_SPI_HZ", "4000000")),
                            gpio=gpio_backend,
                        )
                        # ILI9488 natively requires landscape (480, 320) with rotate=0
                        dev_kwargs = dict(width=480, height=320, rotate=0,
                                          gpio=gpio_backend, backlight=NoopBacklight())
                        # Partial refresh: only the changed region is sent over SPI, which
                        # removes most of the visible tearing on this slow panel.
                        # BRAILCE_LCD_FULL_REFRESH=1 restores full-frame updates.
                        if os.environ.get("BRAILCE_LCD_FULL_REFRESH", "0") != "1":
                            try:
                                from luma.core.framebuffer import diff_to_previous
                                dev_kwargs["framebuffer"] = diff_to_previous(num_segments=8)
                            except Exception:
                                pass
                        try:
                            self._device = driver_cls(serial, **dev_kwargs)
                        except TypeError:
                            dev_kwargs.pop("framebuffer", None)
                            self._device = driver_cls(serial, **dev_kwargs)
                        self._hardware_type = f"spi-{driver_mod}"
                        logger.info(
                            "%s connected via direct SPI (DC=GPIO%d, RST=GPIO%d).",
                            driver_mod.upper(), self._gpio_dc, self._gpio_rst
                        )
                except Exception as exc:
                    self._init_error = f"{driver_mod}: {exc}"
                    logger.warning("Direct SPI init with %s failed: %s", driver_mod, exc)

        # 2. Framebuffer fallback
        if not self._device and os.path.exists(self._fb_path):
            try:
                self._fb_file = open(self._fb_path, "wb")
                fb_id = os.path.basename(self._fb_path)
                bpp_path = f"/sys/class/graphics/{fb_id}/bits_per_pixel"
                if os.path.exists(bpp_path):
                    with open(bpp_path, "r") as f:
                        self._bpp = int(f.read().strip())
                elif self._fb_path == "/dev/fb0":
                    self._bpp = 32
                else:
                    self._bpp = 16
                self._hardware_type = "framebuffer"
                logger.info("Connected to framebuffer %s (%d bpp).", self._fb_path, self._bpp)
            except Exception as exc:
                logger.warning("Could not open %s: %s", self._fb_path, exc)

    def shutdown(self) -> None:
        if self._device:
            try:
                self._device.cleanup()
            except Exception:
                pass
        if self._fb_file:
            try:
                self._fb_file.close()
            except Exception:
                pass
        logger.info("ILI9488Display shut down.")

    def save_screenshot(self, output_path: str = "lcd_preview.png") -> bool:
        if self._image:
            try:
                self._image.save(output_path)
                return True
            except Exception as exc:
                logger.warning("Could not save screenshot: %s", exc)
        return False

    @contextlib.contextmanager
    def batch(self):
        """Group several show_* calls into ONE LCD update (no half-drawn screens)."""
        with self._canvas_lock:
            self._batch_depth += 1
            try:
                yield self
            finally:
                self._batch_depth -= 1
                if self._batch_depth == 0:
                    self._flush_to_hardware()

    def _flush_to_hardware(self) -> None:
        """Thread-safe blit of the current image buffer to the TFT via direct SPI."""
        if not self._image or self._batch_depth > 0:
            return

        with self._display_lock:
            # Direct SPI via luma.lcd (Raspberry Pi 5)
            if self._device is not None:
                try:
                    self._device.display(self._image)
                    return
                except Exception as exc:
                    logger.debug("Direct SPI display error: %s", exc)

            # Linux Framebuffer fallback
            if self._fb_file:
                try:
                    if self._bpp == 16:
                        pixels = self._image.getdata()
                        buf = bytearray(self._width * self._height * 2)
                        idx = 0
                        for r, g, b in pixels:
                            p = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
                            buf[idx] = p & 0xFF
                            buf[idx + 1] = (p >> 8) & 0xFF
                            idx += 2
                        raw_bytes = bytes(buf)
                    elif self._bpp == 32:
                        raw_bytes = self._image.convert("RGBA").tobytes()
                    else:
                        raw_bytes = self._image.tobytes()

                    self._fb_file.seek(0)
                    self._fb_file.write(raw_bytes)
                    self._fb_file.flush()
                except Exception as exc:
                    logger.debug("Framebuffer write error: %s", exc)


    # =========================================================================
    # Battery state
    # =========================================================================

    def _get_battery_info(self) -> tuple[int, bool]:
        """Fetch battery percentage and plugged/charging state."""
        if self._battery_override is not None:
            pct, charging = self._battery_override
            return max(0, min(100, int(pct))), bool(charging)
        if self._power_monitor:
            try:
                status = self._power_monitor.get_status()
                pct = status.percentage if status.percentage is not None else 100
                charging = bool(status.is_charging or status.is_plugged_in)
                return max(0, min(100, pct)), charging
            except Exception:
                pass
        return 100, True  # Default: 100% plugged directly into power supply

    def _battery_known(self) -> bool:
        """True only when a real battery reading exists (poller value or monitor percentage)."""
        if self._battery_override is not None:
            return True
        if self._power_monitor:
            try:
                return self._power_monitor.get_status().percentage is not None
            except Exception:
                return False
        return False

    @_canvas_locked
    def show_battery(self, percentage, charging: bool = False) -> None:
        """
        Refresh the header battery widget ("75%" / "75%+") with the value
        polled by the background battery poller. Small and non-intrusive:
        only the header band is redrawn.
        """
        if not isinstance(percentage, int):
            return
        self._battery_override = (max(0, min(100, percentage)), bool(charging))
        if self._draw and self._current_mode is not None:
            self._draw_header(self._current_mode)
            self._flush_to_hardware()

    # =========================================================================
    # Drawing primitives
    # =========================================================================

    def _L(self, key: str) -> str:
        entry = LCD_LABELS.get(key, {})
        return entry.get(self._language) or entry.get("en", key)

    def _accent(self, mode: Optional[AppMode] = None) -> Tuple[int, int, int]:
        theme = MODE_THEME.get(mode if mode is not None else self._current_mode)
        return theme[1] if theme else Palette.TEXT_2

    @property
    def _footer_y(self) -> int:
        return self._height - FOOTER_H

    @property
    def _content_bottom(self) -> int:
        return self._footer_y - CONTENT_BOTTOM_GAP

    def _tw(self, text: str, font: Any) -> float:
        """Rendered pixel width of a string."""
        if font is not None and hasattr(font, "getlength"):
            try:
                return float(font.getlength(text))
            except Exception:
                pass
        return len(text) * 7.5

    def _fit(self, text: str, font: Any, max_w: float) -> str:
        """Ellipsize text so it fits max_w pixels."""
        if self._tw(text, font) <= max_w:
            return text
        ell = "…"
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._tw(text[:mid].rstrip() + ell, font) <= max_w:
                lo = mid
            else:
                hi = mid - 1
        return text[:lo].rstrip() + ell

    def _wrap(self, text: str, font: Any, max_w: float, max_lines: Optional[int] = None,
              keep: str = "head") -> List[str]:
        """
        Word-wrap by pixel width. With max_lines, keep="head" ellipsizes the last
        visible line; keep="tail" shows the newest lines (typing buffers).
        """
        lines: List[str] = []
        for para in (text or "").split("\n"):
            words = para.split(" ")
            cur = ""
            for word in words:
                trial = word if not cur else f"{cur} {word}"
                if self._tw(trial, font) <= max_w:
                    cur = trial
                    continue
                if cur:
                    lines.append(cur)
                # hard-break words that are wider than the line
                while self._tw(word, font) > max_w and len(word) > 1:
                    cut = len(word)
                    while cut > 1 and self._tw(word[:cut], font) > max_w:
                        cut -= 1
                    lines.append(word[:cut])
                    word = word[cut:]
                cur = word
            if cur:
                lines.append(cur)
        if max_lines is not None and len(lines) > max_lines:
            if keep == "tail":
                lines = lines[-max_lines:]
            else:
                rest = " ".join(lines[max_lines - 1:])
                lines = lines[: max_lines - 1] + [self._fit(rest, font, max_w)]
        return lines

    def _rrect(self, box: Sequence[float], r: int, fill: Any = None, outline: Any = None, width: int = 1) -> None:
        x0, y0, x1, y1 = [int(round(v)) for v in box]
        fn = getattr(self._draw, "rounded_rectangle", None)
        if fn is not None and r > 0:
            try:
                fn([(x0, y0), (x1, y1)], radius=r, fill=fill, outline=outline, width=width)
                return
            except Exception:
                pass
        self._draw.rectangle([(x0, y0), (x1, y1)], fill=fill, outline=outline, width=width)

    def _circle(self, cx: float, cy: float, r: float, fill: Any = None, outline: Any = None, width: int = 1) -> None:
        self._draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=fill, outline=outline, width=width)

    def _text(self, xy: Tuple[float, float], s: str, size: int = 15, bold: bool = False,
              fill: Any = Palette.TEXT) -> None:
        self._draw.text((int(xy[0]), int(xy[1])), s, font=self._f(size, bold), fill=fill)

    def _text_center(self, cx: float, cy: float, s: str, size: int, bold: bool = False,
                     fill: Any = Palette.TEXT) -> None:
        """Draw text with its ink box centred on (cx, cy)."""
        font = self._f(size, bold)
        l = t = 0.0
        r, b = self._tw(s, font), float(size)
        if font is not None and hasattr(font, "getbbox"):
            try:
                l, t, r, b = font.getbbox(s)
            except Exception:
                pass
        self._draw.text((int(cx - (l + r) / 2), int(cy - (t + b) / 2)), s, font=font, fill=fill)

    def _polygon(self, pts: Sequence[Tuple[float, float]], fill: Any) -> None:
        fn = getattr(self._draw, "polygon", None)
        if fn is not None:
            fn([(int(x), int(y)) for x, y in pts], fill=fill)

    def _clear_content(self) -> None:
        self._draw.rectangle([(0, HEADER_H + 1), (self._width, self._footer_y - 1)], fill=Palette.BG)

    def _card(self, box: Sequence[float], title: Optional[str] = None,
              title_color: Any = None, fill: Any = Palette.SURFACE) -> int:
        """Rounded surface card with an optional small caption. Returns the content top y."""
        x0, y0, x1, y1 = box
        self._rrect(box, CARD_RADIUS, fill=fill)
        if title:
            accent = title_color or Palette.TEXT_3
            self._rrect((x0 + 14, y0 + 12, x0 + 17, y0 + 23), 1, fill=accent)
            self._text((x0 + 24, y0 + 10), title, size=11, bold=True, fill=accent)
            return int(y0 + 32)
        return int(y0 + 12)

    def _key_chip(self, x: float, y: float, key: str, label: str) -> float:
        """Keycap-style hint: [ENTER] Ask the AI. Returns x after the chip."""
        fk = self._f(11, True)
        kw = self._tw(key, fk) + 14
        self._rrect((x, y, x + kw, y + 20), 5, fill=Palette.SURFACE_HI, outline=Palette.BORDER)
        self._text_center(x + kw / 2, y + 10, key, 11, True, Palette.TEXT)
        self._text((x + kw + 7, y + 2), label, size=13, fill=Palette.TEXT_2)
        return x + kw + 7 + self._tw(label, self._f(13)) + 18

    def _key_chips(self, x0: float, y: float, x_max: float, items: Iterable[Tuple[str, str]],
                   line_h: int = 26) -> float:
        """Lay out key chips left-to-right, wrapping to a new row when needed. Returns next y."""
        x = x0
        for key, label in items:
            need = self._tw(key, self._f(11, True)) + 14 + 7 + self._tw(label, self._f(13))
            if x > x0 and x + need > x_max:
                x = x0
                y += line_h
            x = self._key_chip(x, y, key, label)
        return y + line_h

    def _braille_cell(self, x: float, y: float, dots: Set[int], pitch: float, r: float,
                      on: Any, numbers: bool = False) -> None:
        """Draw one 2x3 Braille cell; (x, y) is the centre of dot 1."""
        for n in range(1, 7):
            col = 0 if n <= 3 else 1
            row = (n - 1) % 3
            cx, cy = x + col * pitch, y + row * pitch
            if n in dots:
                self._circle(cx, cy, r, fill=on)
            else:
                self._circle(cx, cy, r, outline=Palette.DOT_OFF, width=2 if r >= 6 else 1)
            if numbers:
                nx = cx - r - 9 if col == 0 else cx + r + 9
                self._text_center(nx, cy, str(n), 10, False, Palette.TEXT_3)

    def _braille_row(self, cells: List[Set[int]], box: Sequence[float], on: Any,
                     numbers: bool = False) -> None:
        """Centre a row of Braille cells inside box, scaling them to fit."""
        x0, y0, x1, y1 = box
        n = max(1, len(cells))
        avail_w, avail_h = (x1 - x0), (y1 - y0)
        pitch = min(26.0, (avail_h - 4) / 2.6)
        while pitch > 6:
            r = pitch * 0.36
            cell_w = pitch + 2 * r
            gap = pitch * 0.9
            total = n * cell_w + (n - 1) * gap + (34 if numbers else 0)
            if total <= avail_w:
                break
            pitch -= 1
        r = pitch * 0.36
        cell_w = pitch + 2 * r
        gap = pitch * 0.9
        total = n * cell_w + (n - 1) * gap
        sx = x0 + (avail_w - total) / 2 + r
        sy = y0 + (avail_h - (2 * pitch + 2 * r)) / 2 + r
        for i, dots in enumerate(cells):
            self._braille_cell(sx + i * (cell_w + gap), sy, dots, pitch, r, on, numbers=numbers)

    # =========================================================================
    # Header, battery, footer
    # =========================================================================

    def _draw_header(self, mode: AppMode) -> None:
        """Top bar: mode pill (left), wordmark (centre), battery (right)."""
        if not self._draw:
            return
        label, accent = MODE_THEME.get(mode, ("BRAILCE", Palette.TEXT_2))
        self._draw.rectangle([(0, 0), (self._width, HEADER_H)], fill=Palette.BG)
        self._draw.line([(0, HEADER_H), (self._width, HEADER_H)], fill=Palette.BORDER, width=1)

        # Mode pill
        pill_font = self._f(13, True)
        pill_w = self._tw(label, pill_font) + 28
        self._rrect((MARGIN, 9, MARGIN + pill_w, 31), 11, fill=accent)
        self._text_center(MARGIN + pill_w / 2, 20, label, 13, True, Palette.BG)

        # Wordmark
        self._text_center(self._width / 2, 20, "BRAILCE", 14, True, Palette.TEXT_2)

        self._draw_battery()

    def _draw_battery(self) -> None:
        pct, is_charging = self._get_battery_info()
        font = self._f(13, True)
        known = self._battery_known()
        label = "--" if not known else (f"{pct}%+" if is_charging else f"{pct}%")
        tw = self._tw(label, font)
        tx = self._width - MARGIN - tw
        bw, bh = 26, 13
        bx1 = tx - 9
        bx0 = bx1 - bw
        by0 = 20 - bh / 2
        by1 = by0 + bh

        if not known:
            color = Palette.TEXT_3
        elif is_charging:
            color = (56, 189, 248)
        elif pct > 20:
            color = Palette.OK
        else:
            color = (239, 68, 68)

        self._rrect((bx0, by0, bx1, by1), 3, outline=color, width=2)
        self._draw.rectangle([(bx1 + 1, by0 + 4), (bx1 + 3, by1 - 4)], fill=color)
        if known:
            fill_w = max(2, int((bw - 6) * (pct / 100.0)))
            self._draw.rectangle([(bx0 + 3, by0 + 3), (bx0 + 3 + fill_w, by1 - 3)], fill=color)
            if is_charging:
                cx = (bx0 + bx1) / 2
                self._polygon(
                    [(cx + 2, by0 - 1), (cx - 5, 20 + 1), (cx - 0.5, 20 + 1),
                     (cx - 2, by1 + 1), (cx + 5, 20 - 1), (cx + 0.5, 20 - 1)],
                    fill=Palette.TEXT,
                )
        self._text_center(tx + tw / 2, 20, label, 13, True, Palette.TEXT if known else Palette.TEXT_3)

    def _draw_footer(self, message: str, dot: Any = None, bg: Any = Palette.BG,
                     fg: Any = Palette.TEXT_2) -> None:
        fy = self._footer_y
        self._draw.rectangle([(0, fy), (self._width, self._height)], fill=bg)
        self._draw.line([(0, fy), (self._width, fy)], fill=Palette.BORDER, width=1)
        cy = fy + FOOTER_H / 2
        self._circle(MARGIN + 6, cy, 4, fill=dot or self._accent())
        font = self._f(13)
        msg = self._fit(str(message), font, self._width - MARGIN * 2 - 20)
        self._text_center(MARGIN + 20 + self._tw(msg, font) / 2, cy, msg, 13, False, fg)

    # =========================================================================
    # Operating Modes Rendering
    # =========================================================================

    @_canvas_locked
    def show_mode(self, mode: AppMode) -> None:
        """Switch mode and draw its start screen."""
        self._current_mode = mode
        self._current_query = ""
        self._layout = None
        if not self._draw:
            return

        self._draw.rectangle([(0, 0), (self._width, self._height)], fill=Palette.BG)
        self._draw_header(mode)

        if mode == AppMode.FREE_INPUT:
            self._ensure_input_layout(force=True)
            self.show_status(get_string("FREE_INPUT_STATUS", self._language, count=0))
        elif mode == AppMode.MATH:
            self._draw_math_welcome()
        elif mode == AppMode.AI_ASSISTANT:
            self.show_ai_query_input("")
        elif mode == AppMode.SCANNER:
            self.show_scanner_viewfinder()

        self._flush_to_hardware()

    # -------------------------------------------------------------------------
    # Free Input / typing layout (also used by Math typing and Scanner readout)
    # -------------------------------------------------------------------------

    def _boxes(self) -> Dict[str, Tuple[int, int, int, int]]:
        w = self._width
        top = CONTENT_TOP
        mid = top + 150
        return {
            "char": (MARGIN, top, MARGIN + 156, mid),
            "braille": (MARGIN + 164, top, w - MARGIN, mid),
            "text": (MARGIN, mid + 8, w - MARGIN, self._content_bottom),
        }

    def _ensure_input_layout(self, force: bool = False) -> None:
        """Draw the three typing cards when another screen is on the canvas."""
        if self._layout == "input" and not force:
            return
        self._layout = "input"
        self._clear_content()
        self._draw_char_card(None)
        self._draw_braille_card([], "")
        self._draw_text_card("")

    def _draw_char_card(self, character: Optional[str]) -> None:
        box = self._boxes()["char"]
        x0, y0, x1, y1 = box
        top = self._card(box, self._L("CHARACTER"), self._accent())
        cx, cy = (x0 + x1) / 2, (top + y1) / 2 - 2
        inner_w = (x1 - x0) - 24

        if character is None:
            self._text_center(cx, cy, "–", 40, True, Palette.DOT_OFF)
            return

        stripped = character.strip()
        if not stripped:
            # Space: drawn as a little space-bar so it never looks empty
            self._draw.line([(cx - 34, cy + 6), (cx - 34, cy + 14), (cx + 34, cy + 14), (cx + 34, cy + 6)],
                            fill=Palette.TEXT_2, width=3)
            self._text_center(cx, cy - 16, self._L("SPACE"), 18, True, Palette.TEXT_2)
            return

        # "5 (from e)" / "# (Number Sign)" -> big symbol + small caption
        m = re.match(r"^(\S{1,2})\s+\((.+)\)$", stripped)
        if m:
            self._text_center(cx, cy - 12, m.group(1), 60, True, Palette.TEXT)
            cap = self._fit(m.group(2), self._f(12), inner_w)
            self._text_center(cx, y1 - 18, cap, 12, False, Palette.TEXT_3)
            return

        if len(stripped) <= 2:
            self._text_center(cx, cy, stripped, 76 if len(stripped) == 1 else 56, True, Palette.TEXT)
            return

        # Word labels ("Backspace", "[ Cleared ]", ...)
        font = self._f(20, True)
        lines = self._wrap(stripped, font, inner_w, max_lines=3)
        lh = 24
        y = cy - (len(lines) * lh) / 2 + lh / 2
        for line in lines:
            self._text_center(cx, y, line, 20, True, Palette.TEXT)
            y += lh

    def _parse_cells(self, unicode_char: str, dots_string: str) -> List[Set[int]]:
        cells = [_dots_from_unicode(c) for c in (unicode_char or "")]
        if unicode_char and all(c is not None for c in cells):
            return [c for c in cells if c is not None]
        # Fallback: "Dots 1-2-3" / "Dot 2 Dots 2-5-6" / "1" / "No dots"
        groups = re.findall(r"[1-6](?:-[1-6])*", dots_string or "")
        if not groups:
            return [set()]
        return [{int(d) for d in g.split("-")} for g in groups]

    def _draw_braille_card(self, cells: List[Set[int]], dots_text: str) -> None:
        box = self._boxes()["braille"]
        x0, y0, x1, y1 = box
        accent = self._accent()
        top = self._card(box, self._L("BRAILLE"), accent)

        split = x0 + 150
        cells_to_draw = cells[:5] if cells else [set()]
        self._braille_row(cells_to_draw, (x0 + 12, top - 4, split, y1 - 12), accent,
                          numbers=len(cells_to_draw) == 1)

        # Divider + dots description on the right
        self._draw.line([(split + 6, top + 4), (split + 6, y1 - 16)], fill=Palette.BORDER, width=1)
        tx = split + 20
        self._text((tx, top + 6), self._L("DOTS"), size=11, bold=True, fill=Palette.TEXT_3)
        if not dots_text:
            dots_text = "–" if not cells else " | ".join(_dots_label(c) for c in cells if c) or self._L("NO_DOTS")
        font = self._f(17, True)
        y = top + 26
        for line in self._wrap(dots_text, font, x1 - tx - 12, max_lines=3):
            self._text((tx, y), line, size=17, bold=True, fill=Palette.TEXT)
            y += 23

    def _draw_text_card(self, text: str) -> None:
        box = self._boxes()["text"]
        x0, y0, x1, y1 = box
        mode = self._current_mode
        if mode == AppMode.SCANNER:
            label, empty = self._L("SCANNED"), self._L("EMPTY_SCANNED")
        elif mode == AppMode.MATH:
            label, empty = self._L("EQUATION"), self._L("EMPTY_EQUATION")
        else:
            label, empty = self._L("TYPED"), self._L("EMPTY_TYPED")
        top = self._card(box, label, self._accent())

        if not text:
            self._text((x0 + 16, top + 4), empty, size=15, fill=Palette.TEXT_3)
            return

        # Typed content renders in lowercase at the display layer (all modes).
        font = self._f(19)
        max_w = (x1 - x0) - 40
        lines = self._wrap(text.lower(), font, max_w, max_lines=2, keep="tail") or [""]
        y = top + 1
        for line in lines:
            self._text((x0 + 16, y), line, size=19, fill=Palette.TEXT)
            y += 24
        # Cursor after the last character
        cx = x0 + 16 + self._tw(lines[-1], font) + 3
        cy0 = y - 24 + 2
        self._draw.rectangle([(cx, cy0), (cx + 2, cy0 + 20)], fill=self._accent())

    @_canvas_locked
    def show_character(self, character: str) -> None:
        if not self._draw or self._current_mode == AppMode.AI_ASSISTANT:
            return
        self._ensure_input_layout()
        self._draw_char_card(character)
        self._flush_to_hardware()

    @_canvas_locked
    def show_braille(self, unicode_char: str, dots_string: str, dots_visual: str) -> None:
        if not self._draw or self._current_mode == AppMode.AI_ASSISTANT:
            return
        cells = self._parse_cells(unicode_char, dots_string)

        if self._layout == "math_result" and self._math_braille_box:
            self._draw_math_braille(cells)
            self._flush_to_hardware()
            return

        self._ensure_input_layout()
        raw = dots_string or ""
        label = raw[5:] if raw.startswith("Dots ") else raw  # scanner passes "Dots 1-2-3"
        if len(cells) > 1 or re.search(r"Dots?\s", label):
            label = " | ".join(_dots_label(c) for c in cells if c) or self._L("NO_DOTS")
        if raw.strip().lower() in ("no dots", "") and not any(cells):
            label = self._L("NO_DOTS")
        self._draw_braille_card(cells, label)
        self._flush_to_hardware()

    @_canvas_locked
    def show_text_buffer(self, text: str) -> None:
        if not self._draw:
            return
        if self._current_mode == AppMode.AI_ASSISTANT:
            self.show_ai_query_input(text)
            return
        self._ensure_input_layout()
        self._draw_text_card(text)
        if self._current_mode == AppMode.MATH:
            self.show_status(get_string("MATH_STATUS_READY", self._language))
        elif self._current_mode != AppMode.SCANNER:
            # the Space/Enter hint belongs to Free Input, not to scanned text
            self.show_status(get_string("FREE_INPUT_STATUS", self._language, count=len(text)))
        self._flush_to_hardware()

    # -------------------------------------------------------------------------
    # AI Assistant Mode
    # -------------------------------------------------------------------------

    @_canvas_locked
    def show_ai_query_input(self, query: str) -> None:
        """Question card with live text + cursor, and a controls card."""
        if not self._draw:
            return
        query = (query or "").lower()  # typed query renders lowercase
        self._current_query = query
        self._layout = "ai_input"
        accent = self._accent(AppMode.AI_ASSISTANT)
        w = self._width

        self._clear_content()
        qbox = (MARGIN, CONTENT_TOP, w - MARGIN, CONTENT_TOP + 150)
        top = self._card(qbox, self._L("QUESTION"), accent)
        if query:
            font = self._f(19)
            lines = self._wrap(query, font, (qbox[2] - qbox[0]) - 40, max_lines=4, keep="tail") or [""]
            y = top + 2
            for line in lines:
                self._text((qbox[0] + 16, y), line, size=19, fill=Palette.TEXT)
                y += 26
            cx = qbox[0] + 16 + self._tw(lines[-1], font) + 3
            self._draw.rectangle([(cx, y - 26 + 2), (cx + 2, y - 26 + 22)], fill=accent)
        else:
            self._text((qbox[0] + 16, top + 8), self._L("Q_PLACEHOLDER"), size=16, fill=Palette.TEXT_2)
            self._text((qbox[0] + 16, top + 40), self._L("Q_EXAMPLES"), size=14, fill=Palette.TEXT_3)

        hbox = (MARGIN, qbox[3] + 8, w - MARGIN, self._content_bottom)
        htop = self._card(hbox, self._L("CONTROLS"))
        self._key_chips(hbox[0] + 16, htop - 2, hbox[2] - 12, [
            ("ENTER", self._L("KEY_ASK")),
            ("BKSP", self._L("KEY_DELETE")),
            ("TAB", self._L("KEY_MODE")),
        ])

        self.show_status(get_string("AI_STATUS_PROMPT", self._language, count=len(query)))
        self._flush_to_hardware()

    @_canvas_locked
    def show_ai_processing(self, query: Optional[str] = None) -> None:
        """Question recap and a 'thinking' card."""
        if not self._draw:
            return
        self._layout = "ai_busy"
        accent = self._accent(AppMode.AI_ASSISTANT)
        w = self._width
        self._clear_content()

        qbox = (MARGIN, CONTENT_TOP, w - MARGIN, CONTENT_TOP + 76)
        top = self._card(qbox, self._L("QUESTION_SHORT"))
        q_text = (query or (self._current_query or "...")).lower()
        font = self._f(16)
        for i, line in enumerate(self._wrap(q_text, font, (qbox[2] - qbox[0]) - 32, max_lines=2)):
            self._text((qbox[0] + 16, top + i * 20), line, size=16, fill=Palette.TEXT)

        box = (MARGIN, qbox[3] + 8, w - MARGIN, self._content_bottom)
        x0, y0, x1, y1 = box
        self._rrect(box, CARD_RADIUS, fill=Palette.SURFACE)
        # Three "thinking" dots
        for i, shade in enumerate((1.0, 0.65, 0.35)):
            col = tuple(int(Palette.SURFACE[k] + (accent[k] - Palette.SURFACE[k]) * shade) for k in range(3))
            self._circle(x0 + 26 + i * 18, y0 + 32, 6, fill=col)
        self._text((x0 + 86, y0 + 20), get_string("AI_THINKING_INDICATOR", self._language),
                   size=18, bold=True, fill=accent)
        sub = get_string("AI_THINKING_SUBTEXT", self._language)
        sy = y0 + 58
        for line in self._wrap(sub, self._f(14), (x1 - x0) - 32, max_lines=2):
            self._text((x0 + 16, sy), line, size=14, fill=Palette.TEXT_2)
            sy += 19
        # Indeterminate progress bar
        by = y1 - 26
        self._rrect((x0 + 16, by, x1 - 16, by + 8), 4, fill=Palette.SURFACE_HI)
        self._rrect((x0 + 16, by, x0 + 16 + (x1 - x0 - 32) * 0.62, by + 8), 4, fill=accent)

        self._flush_to_hardware()

    @_canvas_locked
    def show_ai_response(self, response: str, query: Optional[str] = None) -> None:
        """Compact question line and a large answer card."""
        if not self._draw:
            return
        self._layout = "ai_answer"
        accent = self._accent(AppMode.AI_ASSISTANT)
        w = self._width
        self._clear_content()

        qbox = (MARGIN, CONTENT_TOP, w - MARGIN, CONTENT_TOP + 52)
        top = self._card(qbox, self._L("QUESTION_SHORT"))
        q_text = query or self._current_query
        q_line = q_text.lower() if q_text else "(Student Query)"
        self._text((qbox[0] + 16, top - 4), self._fit(q_line, self._f(15), (qbox[2] - qbox[0]) - 32),
                   size=15, fill=Palette.TEXT)

        abox = (MARGIN, qbox[3] + 8, w - MARGIN, self._content_bottom)
        atop = self._card(abox, self._L("AI_ANSWER"), accent)
        font = self._f(16)
        lh = 21
        max_lines = max(1, int((abox[3] - 8 - atop) // lh))
        y = atop
        for line in self._wrap(response or "", font, (abox[2] - abox[0]) - 32, max_lines=max_lines):
            self._text((abox[0] + 16, y), line, size=16, fill=Palette.TEXT)
            y += lh

        self.show_status(get_string("AI_STATUS_COMPLETE", self._language))
        self._flush_to_hardware()

    # -------------------------------------------------------------------------
    # Math Mode
    # -------------------------------------------------------------------------

    def set_language(self, language: str) -> None:
        self._language = "fil" if (language or "en").lower() == "fil" else "en"

    def _draw_math_welcome(self) -> None:
        """Math mode start screen: short intro, example chips, key hints."""
        self._layout = "math_welcome"
        accent = self._accent(AppMode.MATH)
        self._clear_content()
        box = (MARGIN, CONTENT_TOP, self._width - MARGIN, self._content_bottom)
        x0, y0, x1, y1 = box
        top = self._card(box, self._L("MATH_INTRO_TITLE"), accent)
        self._text((x0 + 16, top + 2), self._L("MATH_INTRO"), size=16, fill=Palette.TEXT)

        self._text((x0 + 16, top + 40), self._L("EXAMPLES"), size=11, bold=True, fill=Palette.TEXT_3)
        x = x0 + 16
        ey = top + 58
        for ex in ("2 + 3", "7 - 4", "5 + 5"):
            ew = self._tw(ex, self._f(20, True)) + 28
            self._rrect((x, ey, x + ew, ey + 38), 8, fill=Palette.SURFACE_HI)
            self._text_center(x + ew / 2, ey + 19, ex, 20, True, accent)
            x += ew + 10

        self._key_chips(x0 + 16, y1 - 34, x1 - 12, [
            ("ENTER", self._L("KEY_SOLVE")),
            ("BKSP", self._L("KEY_DELETE")),
            ("TAB", self._L("KEY_MODE")),
        ])
        self.show_status(get_string("MATH_STATUS_READY", self._language))

    @_canvas_locked
    def show_math_result(
        self,
        equation: str,
        result: str,
        explanation: str,
        short_step: Optional[str] = None,
        language: Optional[str] = None,
    ) -> None:
        if not self._draw:
            return
        lang = language or getattr(self, "_language", "en")
        title = get_string("MATH_CARD_TITLE", lang)
        lbl_st = get_string("MATH_LABEL_STEPS", lang)
        lbl_ex = get_string("MATH_LABEL_EXPLANATION", lang)
        accent = self._accent(AppMode.MATH)

        self._layout = "math_result"
        self._clear_content()
        box = (MARGIN, CONTENT_TOP, self._width - MARGIN, self._content_bottom)
        x0, y0, x1, y1 = box
        top = self._card(box, title, accent)

        # Braille panel for the answer (top right), filled by show_braille()
        pbox = (x1 - 150, y0 + 10, x1 - 10, y0 + 84)
        self._math_braille_box = tuple(int(v) for v in pbox)  # type: ignore[assignment]
        self._draw_math_braille([])

        # Big equation: "11 + 3 =" in white, the answer in the accent colour
        eq_display = equation.lower() if equation else (equation or "")
        left = f"{eq_display} ="
        res = str(result)
        max_w = pbox[0] - (x0 + 16) - 12
        size = 32
        while size > 16 and self._tw(left + " " + res, self._f(size, True)) > max_w:
            size -= 2
        ey = top + 2
        self._text((x0 + 16, ey), left, size=size, bold=True, fill=Palette.TEXT)
        self._text((x0 + 16 + self._tw(left + " ", self._f(size, True)), ey), res, size=size, bold=True, fill=accent)

        # Steps line
        sy = y0 + 92
        if short_step:
            self._text((x0 + 16, sy + 3), lbl_st.upper(), size=11, bold=True, fill=Palette.TEXT_3)
            lx = x0 + 16 + self._tw(lbl_st.upper(), self._f(11, True)) + 10
            self._text((lx, sy), self._fit(short_step, self._f(15, True), x1 - 16 - lx),
                       size=15, bold=True, fill=Palette.STEP)
        dy = sy + 28
        self._draw.line([(x0 + 16, dy), (x1 - 16, dy)], fill=Palette.BORDER, width=1)

        # Explanation
        self._text((x0 + 16, dy + 10), lbl_ex.upper(), size=11, bold=True, fill=Palette.TEXT_3)
        font = self._f(15)
        lh = 19
        ty = dy + 28
        max_lines = max(1, int((y1 - 8 - ty) // lh))
        for line in self._wrap(explanation or "", font, (x1 - x0) - 32, max_lines=max_lines):
            self._text((x0 + 16, ty), line, size=15, fill=Palette.TEXT)
            ty += lh

        self.show_status(f"{eq_display} = {result}")
        self._flush_to_hardware()

    def _draw_math_braille(self, cells: List[Set[int]]) -> None:
        if not self._math_braille_box:
            return
        x0, y0, x1, y1 = self._math_braille_box
        self._rrect((x0, y0, x1, y1), 8, fill=Palette.SURFACE_HI)
        self._text((x0 + 10, y0 + 6), self._L("BRAILLE"), size=10, bold=True, fill=Palette.TEXT_3)
        if cells:
            self._braille_row(cells[:6], (x0 + 8, y0 + 20, x1 - 8, y1 - 6), self._accent(AppMode.MATH))

    # -------------------------------------------------------------------------
    # Scanner Mode (Camera Viewfinder)
    # -------------------------------------------------------------------------

    @_canvas_locked
    def show_scanner_viewfinder(self, status: Optional[str] = None) -> None:
        """Scanner start screen: illustration, instructions, key hints."""
        if not self._draw:
            return
        self._layout = "scanner"
        accent = self._accent(AppMode.SCANNER)
        self._clear_content()
        box = (MARGIN, CONTENT_TOP, self._width - MARGIN, self._content_bottom)
        x0, y0, x1, y1 = box
        self._rrect(box, CARD_RADIUS, fill=Palette.SURFACE)

        # Illustration: camera framing brackets around a line of Braille cells
        ix0, iy0, ix1, iy1 = x0 + 16, y0 + 16, x0 + 164, y0 + 136
        self._rrect((ix0, iy0, ix1, iy1), 8, fill=Palette.BG)
        arm = 16
        for (cx, cy, dx, dy) in ((ix0 + 10, iy0 + 10, 1, 1), (ix1 - 10, iy0 + 10, -1, 1),
                                 (ix0 + 10, iy1 - 10, 1, -1), (ix1 - 10, iy1 - 10, -1, -1)):
            self._draw.line([(cx, cy), (cx + dx * arm, cy)], fill=accent, width=3)
            self._draw.line([(cx, cy), (cx, cy + dy * arm)], fill=accent, width=3)
        self._braille_row([{1, 2, 5}, {1, 5}, {1, 2, 3}, {1, 3, 5}],
                          (ix0 + 22, iy0 + 36, ix1 - 22, iy1 - 36), accent)
        mid = (iy0 + iy1) / 2
        self._draw.line([(ix0 + 18, mid + 30), (ix1 - 18, mid + 30)], fill=Palette.DOT_OFF, width=1)

        # Text column
        tx = ix1 + 18
        self._text((tx, y0 + 18), self._L("SCANNER_TITLE"), size=20, bold=True, fill=Palette.TEXT)
        ty = y0 + 50
        for line in self._wrap(get_string("SCANNER_INSTRUCTION_BOX", self._language), self._f(14),
                               x1 - 16 - tx, max_lines=3):
            self._text((tx, ty), line, size=14, fill=Palette.TEXT_2)
            ty += 19
        ty += 6
        for line in self._wrap(self._L("SCANNER_TIP"), self._f(13), x1 - 16 - tx, max_lines=3):
            self._text((tx, ty), line, size=13, fill=Palette.TEXT_3)
            ty += 17

        self._key_chips(x0 + 16, y1 - 34, x1 - 12, [
            ("ENTER", self._L("KEY_SCAN")),
            ("TAB", self._L("KEY_MODE")),
        ])

        st = status or get_string("SCANNER_STATUS_POSITION", self._language)
        self.show_status(st)
        self._flush_to_hardware()

    def show_camera_preview(
        self,
        frame: Any,
        status: Optional[str] = None,
        overlays: Optional[List[Dict[str, Any]]] = None,
        distance_state: Optional[str] = None,
    ) -> None:
        """Render camera preview with letterboxed framing, decoded cell overlays, and non-blocking frame-skipping."""
        if frame is None:
            return

        # Initialize canvas if not yet created
        if self._image is None:
            from PIL import Image, ImageDraw
            self._image = Image.new("RGB", (self._width, self._height), color=Palette.BG)
            self._draw = ImageDraw.Draw(self._image)

        # Frame-skip if hardware SPI or display is currently busy to never starve audio/input threads
        if not self._canvas_lock.acquire(blocking=False):
            return  # another thread is drawing a screen: skip this preview frame
        if not self._display_lock.acquire(blocking=False):
            self._canvas_lock.release()
            return

        try:
            from PIL import Image, ImageDraw

            # 1. Normalise input frame into an RGB PIL Image
            if isinstance(frame, Image.Image):
                frame_pil = frame.convert("RGB")
                fw, fh = frame_pil.size
            else:
                import numpy as np
                arr = np.asarray(frame)
                if arr.size == 0:
                    return

                # Convert float / int arrays to uint8
                if arr.dtype != np.uint8:
                    if np.issubdtype(arr.dtype, np.floating):
                        max_val = float(np.nanmax(arr)) if arr.size else 1.0
                        if max_val <= 1.0:
                            arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
                        else:
                            arr = np.clip(arr, 0, 255).astype(np.uint8)
                    else:
                        arr = np.clip(arr, 0, 255).astype(np.uint8)

                if arr.ndim == 2:
                    arr_rgb = np.stack([arr, arr, arr], axis=-1)
                elif arr.ndim == 3:
                    channels = arr.shape[2]
                    if channels == 1:
                        arr_rgb = np.repeat(arr, 3, axis=-1)
                    elif channels in (3, 4):
                        from brailce.vision.camera import SWAP_RED_BLUE
                        arr_rgb = arr[:, :, :3]
                        if SWAP_RED_BLUE:
                            arr_rgb = arr_rgb[:, :, ::-1]
                    else:
                        arr_rgb = arr[:, :, :3]
                else:
                    return

                frame_pil = Image.fromarray(np.ascontiguousarray(arr_rgb))
                fw, fh = frame_pil.size

            # 2. Letterboxed scaling: keep aspect ratio without stretching
            usable_w = int(self._width)
            usable_h = max(10, int(self._footer_y - HEADER_H - 1))
            scale = min(float(usable_w) / max(1.0, float(fw)), float(usable_h) / max(1.0, float(fh)))
            nw = max(1, int(round(fw * scale)))
            nh = max(1, int(round(fh * scale)))

            resized_pil = frame_pil.resize(
                (nw, nh),
                Image.Resampling.BILINEAR if hasattr(Image, "Resampling") else Image.BILINEAR,
            )

            preview_img = Image.new("RGB", (self._width, self._height), color=Palette.BG)
            ox = int((self._width - nw) // 2)
            oy = int(HEADER_H + 1 + (usable_h - nh) // 2)
            preview_img.paste(resized_pil, (ox, oy))

            draw = ImageDraw.Draw(preview_img)
            self._image = preview_img
            self._draw = draw
            self._layout = "camera"

            # Centre reading window (HUD corner brackets + baseline)
            from brailce.vision.obr_engine import SHOW_ROI, ROI_W_FRAC, ROI_H_FRAC
            if SHOW_ROI:
                roi_w_disp = int(nw * ROI_W_FRAC)
                roi_h_disp = int(nh * ROI_H_FRAC)
                rx0 = ox + int((nw - roi_w_disp) // 2)
                ry0 = oy + int((nh - roi_h_disp) // 2)
                rx1 = rx0 + roi_w_disp
                ry1 = ry0 + roi_h_disp

                if distance_state == "ok":
                    corner_color = Palette.OK
                elif distance_state in ("too_close", "too_far"):
                    corner_color = Palette.WARN
                else:
                    corner_color = self._accent(AppMode.SCANNER)

                draw.rectangle([(rx0, ry0), (rx1, ry1)], outline=(0, 0, 0), width=1)
                cl = 22
                for (cx, cy, dx, dy) in ((rx0, ry0, 1, 1), (rx1, ry0, -1, 1), (rx0, ry1, 1, -1), (rx1, ry1, -1, -1)):
                    draw.line([(cx, cy), (cx + dx * cl, cy)], fill=corner_color, width=4)
                    draw.line([(cx, cy), (cx, cy + dy * cl)], fill=corner_color, width=4)
                cy = int((ry0 + ry1) // 2)
                draw.line([(rx0 + 4, cy), (rx0 + 16, cy)], fill=corner_color, width=2)
                draw.line([(rx1 - 16, cy), (rx1 - 4, cy)], fill=corner_color, width=2)

            # Decoded Braille cell letter overlays
            if overlays:
                ov_font = self._f(14, True)
                for ov in overlays:
                    ch = str(ov.get("char", "")).strip()
                    if not ch:
                        continue
                    active = bool(ov.get("active", False))
                    raw_bbox = ov.get("bbox")
                    if raw_bbox and len(raw_bbox) == 4:
                        if raw_bbox[2] > raw_bbox[0] and raw_bbox[3] > raw_bbox[1]:
                            bx1, by1, bx2, by2 = raw_bbox
                        else:
                            bx1, by1 = raw_bbox[0], raw_bbox[1]
                            bx2, by2 = bx1 + raw_bbox[2], by1 + raw_bbox[3]
                        sx1 = int(ox + bx1 * scale)
                        sy1 = int(oy + by1 * scale)
                        sx2 = int(ox + bx2 * scale)
                        sy2 = int(oy + by2 * scale)

                        box_color = Palette.WARN if active else Palette.STEP
                        draw.rectangle([(sx1, sy1), (sx2, sy2)], outline=box_color, width=2 if active else 1)

                        tw = max(18, int(self._tw(ch, ov_font)) + 10)
                        th = 20
                        bx = int((sx1 + sx2 - tw) // 2)
                        by = max(HEADER_H + 4, sy1 - th - 4)
                        self._rrect((bx, by, bx + tw, by + th), 5,
                                    fill=box_color if active else (6, 78, 59))
                        self._text_center(bx + tw / 2, by + th / 2, ch, 14, True,
                                          Palette.BG if active else Palette.TEXT)

            # Header with battery widget
            self._draw_header(AppMode.SCANNER)

            # Distance / detection pill (centred under the header)
            pill_text = None
            pill_color = Palette.STEP
            if distance_state == "ok":
                pill_text, pill_color = "SAKTO ANG DISTANSYA | READY", Palette.OK
            elif distance_state == "too_close":
                pill_text, pill_color = "MASYADONG MALAPIT | ILAYO", Palette.WARN
            elif distance_state == "too_far":
                pill_text, pill_color = "MASYADONG MALAYO | ILAPIT", Palette.WARN
            elif status and "detected" in str(status).lower():
                pill_text = str(status)[:46]
                low = pill_text.lower()
                pill_color = Palette.DANGER if ("no " in low or "not" in low) else Palette.STEP

            if pill_text:
                pf = self._f(12, True)
                pill_text = self._fit(pill_text, pf, self._width - 80)
                pw = self._tw(pill_text, pf) + 40
                px0 = (self._width - pw) / 2
                py0 = HEADER_H + 8
                self._rrect((px0, py0, px0 + pw, py0 + 24), 12, fill=(12, 15, 22), outline=pill_color)
                self._circle(px0 + 14, py0 + 12, 4, fill=pill_color)
                self._text_center(px0 + 24 + self._tw(pill_text, pf) / 2, py0 + 12, pill_text, 12, True, Palette.TEXT)

            # Bottom status bar
            lang = getattr(self, "_language", "en") or "en"
            st = str(status) if status else get_string("SCANNER_STATUS_POSITION", lang)
            self._draw_footer(st, dot=self._accent(AppMode.SCANNER))

            # Direct SPI / framebuffer flush (must be done while holding lock)
            if self._device is not None:
                self._device.display(self._image)
            elif self._fb_file and self._image:
                try:
                    import numpy as np
                    arr = np.array(self._image)
                    if self._bpp == 16:
                        r5 = (arr[:, :, 0] >> 3).astype(np.uint16)
                        g6 = (arr[:, :, 1] >> 2).astype(np.uint16)
                        b5 = (arr[:, :, 2] >> 3).astype(np.uint16)
                        rgb565 = (r5 << 11) | (g6 << 5) | b5
                        self._fb_file.seek(0)
                        self._fb_file.write(rgb565.tobytes())
                        self._fb_file.flush()
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("Error updating ILI9488 camera preview: %s", exc)
        finally:
            self._display_lock.release()
            self._canvas_lock.release()

    def close_camera_preview(self) -> None:
        self.clear()

    # -------------------------------------------------------------------------
    # Status & Alerts (Unified Bottom Bar Geometry)
    # -------------------------------------------------------------------------

    @_canvas_locked
    def show_status(self, message: str) -> None:
        """Bottom status bar: accent dot + message."""
        if not self._draw:
            return
        self._last_status_msg = message
        self._draw_footer(message)
        self._flush_to_hardware()

    @_canvas_locked
    def show_error(self, message: str) -> None:
        """Red error bar at the bottom."""
        if not self._draw:
            return
        fy = self._footer_y
        self._draw.rectangle([(0, fy), (self._width, self._height)], fill=Palette.DANGER_BG)
        self._draw.line([(0, fy), (self._width, fy)], fill=Palette.DANGER, width=1)
        cy = fy + FOOTER_H / 2
        self._circle(MARGIN + 8, cy, 8, fill=Palette.DANGER)
        self._text_center(MARGIN + 8, cy, "!", 12, True, Palette.DANGER_BG)
        msg = self._fit(f"{self._L('ERROR')}: {message}", self._f(13, True), self._width - MARGIN * 2 - 28)
        self._text_center(MARGIN + 24 + self._tw(msg, self._f(13, True)) / 2, cy, msg, 13, True, Palette.TEXT)
        self._flush_to_hardware()

    @_canvas_locked
    def clear(self) -> None:
        if self._draw:
            self._draw.rectangle([(0, 0), (self._width, self._height)], fill=Palette.BG)
            if self._current_mode:
                self.show_mode(self._current_mode)
            self._flush_to_hardware()
