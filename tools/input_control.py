"""
Input control — mouse clicks, typing, key presses, scrolling.
Uses Linux evdev/uinput directly — works on both X11 and Wayland.

Key reliability improvements:
- Persistent keyboard device (created once, reused) — no device churn
- 50ms interval between keystrokes (was 20ms) — fewer dropped chars
- Helper _send_key() for clean event+sync pairs
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from evdev import UInput, ecodes as e, AbsInfo

logger = logging.getLogger(__name__)

# Key name → evdev keycode mapping (common keys the LLM might use)
_KEY_MAP = {
    "enter": e.KEY_ENTER,
    "space": e.KEY_SPACE,
    "tab": e.KEY_TAB,
    "backspace": e.KEY_BACKSPACE,
    "delete": e.KEY_DELETE,
    "home": e.KEY_HOME,
    "end": e.KEY_END,
    "pageup": e.KEY_PAGEUP,
    "pagedown": e.KEY_PAGEDOWN,
    "up": e.KEY_UP,
    "down": e.KEY_DOWN,
    "left": e.KEY_LEFT,
    "right": e.KEY_RIGHT,
    "volumeup": e.KEY_VOLUMEUP,
    "volumedown": e.KEY_VOLUMEDOWN,
    "mute": e.KEY_MUTE,
    "nexttrack": e.KEY_NEXTSONG,
    "prevtrack": e.KEY_PREVIOUSSONG,
    "playpause": e.KEY_PLAYPAUSE,
    "stopcd": e.KEY_STOPCD,
    "f1": e.KEY_F1, "f2": e.KEY_F2, "f3": e.KEY_F3,
    "f4": e.KEY_F4, "f5": e.KEY_F5, "f6": e.KEY_F6,
    "f7": e.KEY_F7, "f8": e.KEY_F8, "f9": e.KEY_F9,
    "f10": e.KEY_F10, "f11": e.KEY_F11, "f12": e.KEY_F12,
    "printscreen": e.KEY_SYSRQ,
    "insert": e.KEY_INSERT,
    "menu": e.KEY_MENU,
    "super": e.KEY_LEFTMETA,
    "win": e.KEY_LEFTMETA,
}

# Modifier keys for combos like ctrl+c
_MOD_MAP = {
    "ctrl": e.KEY_LEFTCTRL,
    "control": e.KEY_LEFTCTRL,
    "alt": e.KEY_LEFTALT,
    "shift": e.KEY_LEFTSHIFT,
    "super": e.KEY_LEFTMETA,
    "win": e.KEY_LEFTMETA,
    "meta": e.KEY_LEFTMETA,
}

# All keycodes the persistent keyboard needs
_KB_KEYS = [
    *[getattr(e, f"KEY_{c}", 0) for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"],
    *[getattr(e, f"KEY_{n}", 0) for n in "0123456789"],
    e.KEY_SPACE, e.KEY_ENTER, e.KEY_TAB, e.KEY_ESC, e.KEY_BACKSPACE,
    e.KEY_LEFTSHIFT, e.KEY_RIGHTSHIFT, e.KEY_LEFTCTRL, e.KEY_RIGHTCTRL,
    e.KEY_LEFTALT, e.KEY_RIGHTALT, e.KEY_LEFTMETA, e.KEY_RIGHTMETA,
    e.KEY_MINUS, e.KEY_EQUAL, e.KEY_LEFTBRACE, e.KEY_RIGHTBRACE,
    e.KEY_SEMICOLON, e.KEY_APOSTROPHE, e.KEY_COMMA, e.KEY_DOT,
    e.KEY_SLASH, e.KEY_BACKSLASH, e.KEY_GRAVE,
    e.KEY_UP, e.KEY_DOWN, e.KEY_LEFT, e.KEY_RIGHT,
    e.KEY_HOME, e.KEY_END, e.KEY_PAGEUP, e.KEY_PAGEDOWN,
    e.KEY_DELETE, e.KEY_INSERT,
    e.KEY_F1, e.KEY_F2, e.KEY_F3, e.KEY_F4, e.KEY_F5, e.KEY_F6,
    e.KEY_F7, e.KEY_F8, e.KEY_F9, e.KEY_F10, e.KEY_F11, e.KEY_F12,
]


class InputTool:
    """Simulates mouse and keyboard input via Linux uinput."""

    def __init__(self):
        self._screen_w = 1920
        self._screen_h = 1080
        self._detect_screen_size()
        self._keyboard: UInput | None = None  # persistent keyboard device

    def _get_keyboard(self) -> UInput:
        """Get or create a persistent keyboard device — reused across calls."""
        if self._keyboard is None:
            keys = [k for k in _KB_KEYS if k != 0]
            caps = {e.EV_KEY: keys}
            self._keyboard = UInput(caps, name="megatron-keyboard")
            logger.info("Created persistent keyboard device")
        return self._keyboard

    def _send_key(self, ui: UInput, keycode: int, pressed: bool):
        """Send a key press or release event."""
        ui.write(e.EV_KEY, keycode, 1 if pressed else 0)
        ui.syn()

    def _detect_screen_size(self):
        """Try to detect screen size from the filesystem."""
        try:
            import subprocess
            r = subprocess.run(
                ["xrandr"], capture_output=True, text=True, timeout=5,
                env={**os.environ, "XAUTHORITY": os.environ.get("XAUTHORITY", "")},
            )
            for line in r.stdout.splitlines():
                if " connected primary" in line or " connected" in line:
                    import re
                    m = re.search(r"(\d+)x(\d+)\+", line)
                    if m:
                        self._screen_w = int(m.group(1))
                        self._screen_h = int(m.group(2))
                        return
        except Exception:
            pass
        for card in ["card0", "card1"]:
            try:
                with open(f"/sys/class/drm/{card}/modes") as f:
                    mode = f.readline().strip()
                    w, h = mode.split("x")
                    self._screen_w, self._screen_h = int(w), int(h)
                    return
            except Exception:
                pass

    @property
    def screen_size(self) -> tuple[int, int]:
        return (self._screen_w, self._screen_h)

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------
    def _make_mouse(self) -> UInput:
        """Create a virtual mouse device."""
        caps = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
            e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL],
        }
        return UInput(caps, name="megatron-mouse")

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> dict:
        """Click at current cursor position."""
        btn = {"left": e.BTN_LEFT, "right": e.BTN_RIGHT, "middle": e.BTN_MIDDLE}.get(button, e.BTN_LEFT)
        with self._make_mouse() as ui:
            for _ in range(clicks):
                ui.write(e.EV_KEY, btn, 1)
                ui.syn()
                time.sleep(0.05)
                ui.write(e.EV_KEY, btn, 0)
                ui.syn()
                time.sleep(0.05)
        return {"ok": True, "action": f"{button} click x{clicks}",
                "note": "Injected at current cursor position; coordinates not available on Wayland"}

    def double_click(self, x: int, y: int) -> dict:
        return self.click(x, y, clicks=2)

    def right_click(self, x: int, y: int) -> dict:
        return self.click(x, y, button="right")

    def move_to(self, x: int, y: int) -> dict:
        return {"ok": False, "error": "Mouse movement via uinput not supported — use keyboard shortcuts only"}

    def drag(self, x1: int, y1: int, x2: int, y2: int) -> dict:
        return {"ok": False, "error": "Drag not supported on Wayland via uinput"}

    def scroll(self, clicks: int) -> dict:
        """Scroll the mouse wheel. Positive = up, negative = down."""
        with self._make_mouse() as ui:
            for _ in range(abs(clicks)):
                val = 1 if clicks > 0 else -1
                ui.write(e.EV_REL, e.REL_WHEEL, val)
                ui.syn()
                time.sleep(0.02)
        return {"ok": True, "action": f"scrolled {'up' if clicks > 0 else 'down'} {abs(clicks)} clicks"}

    def mouse_position(self) -> dict:
        return {"ok": True, "x": 0, "y": 0,
                "screen_width": self._screen_w, "screen_height": self._screen_h,
                "note": "Absolute position not available on Wayland"}

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------
    def type_text(self, text: str, interval: float = 0.05) -> dict:
        """Type text character by character using the persistent keyboard device."""
        ui = self._get_keyboard()
        typed = 0
        for char in text:
            code = ord(char)
            if 32 <= code <= 126:
                keycode = self._ascii_to_keycode(char)
                if keycode:
                    need_shift = char.isupper() or char in '~!@#$%^&*()_+{}|:"<>?'
                    if need_shift:
                        self._send_key(ui, e.KEY_LEFTSHIFT, True)
                    self._send_key(ui, keycode, True)
                    self._send_key(ui, keycode, False)
                    if need_shift:
                        self._send_key(ui, e.KEY_LEFTSHIFT, False)
                    time.sleep(interval)
                    typed += 1
                else:
                    logger.warning("Unknown keycode for char: %r (ord=%d)", char, code)
            elif char in ('\n', '\r'):
                self._send_key(ui, e.KEY_ENTER, True)
                self._send_key(ui, e.KEY_ENTER, False)
                time.sleep(interval)
                typed += 1
            elif char == '\t':
                self._send_key(ui, e.KEY_TAB, True)
                self._send_key(ui, e.KEY_TAB, False)
                time.sleep(interval)
                typed += 1
        return {"ok": True, "action": f"typed {typed} chars"}

    def press_key(self, key: str) -> dict:
        """Press a single key or key combo (e.g. 'enter', 'ctrl+c', 'alt+tab')."""
        parts = [k.strip().lower() for k in key.split("+")]
        ui = self._get_keyboard()

        modifiers = []
        regular = []
        for p in parts:
            if p in _MOD_MAP:
                modifiers.append(_MOD_MAP[p])
            elif p in _KEY_MAP:
                regular.append(_KEY_MAP[p])
            elif len(p) == 1:
                code = self._ascii_to_keycode(p)
                if code:
                    regular.append(code)

        # Press modifiers
        for m in modifiers:
            self._send_key(ui, m, True)
            time.sleep(0.03)

        # Press and release regular keys
        for r in regular:
            self._send_key(ui, r, True)
            time.sleep(0.05)
            self._send_key(ui, r, False)

        # Release modifiers (reverse order)
        for m in reversed(modifiers):
            self._send_key(ui, m, False)
            time.sleep(0.03)

        return {"ok": True, "action": f"pressed {key}"}

    def sequence(self, steps: list[dict]) -> dict:
        """Execute a sequence of input actions in one call."""
        results = []
        for step in steps:
            try:
                t = step.get("type", "")
                if t == "press":
                    r = self.press_key(step.get("key", ""))
                elif t == "type":
                    r = self.type_text(step.get("text", ""))
                elif t == "click":
                    r = self.click(0, 0)
                elif t == "right_click":
                    r = self.right_click(0, 0)
                elif t == "double_click":
                    r = self.double_click(0, 0)
                elif t == "scroll":
                    r = self.scroll(int(step.get("amount", 3)))
                elif t == "wait":
                    ms = int(step.get("ms", 100))
                    time.sleep(ms / 1000.0)
                    r = {"ok": True, "waited_ms": ms}
                else:
                    r = {"ok": False, "error": f"Unknown step type: {t}"}
                results.append(r)
                if not r.get("ok"):
                    break
            except Exception as ex:
                results.append({"ok": False, "error": str(ex)})
                break
        return {
            "ok": all(r.get("ok") for r in results),
            "steps_executed": len(results),
            "total_steps": len(steps),
            "results": results,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _ascii_to_keycode(char: str) -> int | None:
        """Map a single ASCII char to an evdev keycode."""
        lower = char.lower()
        if 'a' <= lower <= 'z':
            return getattr(e, f"KEY_{lower.upper()}", None)
        if '0' <= lower <= '9':
            return getattr(e, f"KEY_{lower}", None)
        return {
            ' ': e.KEY_SPACE, '-': e.KEY_MINUS, '=': e.KEY_EQUAL,
            '[': e.KEY_LEFTBRACE, ']': e.KEY_RIGHTBRACE,
            ';': e.KEY_SEMICOLON, "'": e.KEY_APOSTROPHE,
            ',': e.KEY_COMMA, '.': e.KEY_DOT, '/': e.KEY_SLASH,
            '\\': e.KEY_BACKSLASH, '`': e.KEY_GRAVE,
        }.get(lower, None)
