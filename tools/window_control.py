"""
Window management via AT-SPI accessibility — programmatic window focus.
Lists all windows, focuses by name/title, then moves/tiles/closes them.
"""
from __future__ import annotations

import logging
import time

import pyatspi
from evdev import UInput, ecodes as e

logger = logging.getLogger(__name__)

_MOD_MAP = {
    "ctrl": e.KEY_LEFTCTRL, "alt": e.KEY_LEFTALT,
    "shift": e.KEY_LEFTSHIFT, "super": e.KEY_LEFTMETA,
}
_KEY_MAP = {
    "left": e.KEY_LEFT, "right": e.KEY_RIGHT,
    "up": e.KEY_UP, "down": e.KEY_DOWN,
    "enter": e.KEY_ENTER,
    "h": e.KEY_H, "f4": e.KEY_F4,
    "1": e.KEY_1, "2": e.KEY_2, "3": e.KEY_3,
    "4": e.KEY_4, "5": e.KEY_5, "6": e.KEY_6,
    "7": e.KEY_7, "8": e.KEY_8, "9": e.KEY_9,
}


def _press(combo: str) -> dict:
    parts = [p.strip().lower() for p in combo.split("+")]
    mods, keys = [], []
    for p in parts:
        if p in _MOD_MAP: mods.append(_MOD_MAP[p])
        elif p in _KEY_MAP: keys.append(_KEY_MAP[p])
        else: return {"ok": False, "error": f"Unknown key: {p}"}
    all_k = list(set(mods + keys))
    with UInput({e.EV_KEY: all_k}, name="megatron-win") as ui:
        for m in mods:
            ui.write(e.EV_KEY, m, 1); ui.syn(); time.sleep(0.03)
        for k in keys:
            ui.write(e.EV_KEY, k, 1); ui.syn(); time.sleep(0.05)
            ui.write(e.EV_KEY, k, 0); ui.syn()
        for m in reversed(mods):
            ui.write(e.EV_KEY, m, 0); ui.syn(); time.sleep(0.03)
    return {"ok": True}


class WindowTool:
    """Window control using AT-SPI for focus + keyboard shortcuts for actions."""

    # ------------------------------------------------------------------
    # AT-SPI window discovery
    # ------------------------------------------------------------------
    def _get_all_windows(self) -> list[dict]:
        """Return all visible windows with their titles, apps, and activate capability."""
        windows = []
        try:
            desktop = pyatspi.Registry().getDesktop(0)
            for i in range(desktop.childCount):
                app = desktop[i]
                for j in range(app.childCount):
                    try:
                        win = app[j]
                        if win.getRoleName() != "frame":
                            continue
                        name = win.name or ""
                        actions = []
                        try:
                            ai = win.queryAction()
                            actions = [ai.getName(k) for k in range(ai.nActions)]
                        except Exception:
                            pass
                        windows.append({
                            "title": name,
                            "app": app.name or "unknown",
                            "can_activate": "default.activate" in actions,
                            "_accessible": win,
                        })
                    except Exception:
                        pass
        except Exception as e:
            logger.warning("AT-SPI scan failed: %s", e)
        return windows

    # ------------------------------------------------------------------
    # Focus a window by name/partial title
    # ------------------------------------------------------------------
    def focus_window(self, target: str) -> dict:
        """
        Focus a window whose title contains *target* (case-insensitive).
        Uses AT-SPI default.activate action.
        """
        target_lower = target.lower().strip()
        windows = self._get_all_windows()

        # Find matching windows
        matches = [w for w in windows if target_lower in w["title"].lower()]

        if not matches:
            # List available windows in the error
            titles = [w["title"][:50] for w in windows]
            return {
                "ok": False,
                "error": f"No window matching '{target}' found.",
                "available_windows": titles,
            }

        if not matches[0]["can_activate"]:
            return {
                "ok": False,
                "error": f"Window '{matches[0]['title'][:60]}' has no activate action.",
            }

        # Activate the first match
        try:
            win = matches[0]["_accessible"]
            ai = win.queryAction()
            for k in range(ai.nActions):
                if ai.getName(k) == "default.activate":
                    ai.doAction(k)
                    time.sleep(0.3)
                    return {
                        "ok": True,
                        "focused": matches[0]["title"][:80],
                        "total_matches": len(matches),
                    }
        except Exception as e:
            return {"ok": False, "error": f"Activate failed: {e}"}

        return {"ok": False, "error": "default.activate not found"}

    # ------------------------------------------------------------------
    # List windows
    # ------------------------------------------------------------------
    def list_windows(self) -> dict:
        windows = self._get_all_windows()
        return {
            "ok": True,
            "windows": [{"title": w["title"][:80], "app": w["app"]} for w in windows],
            "count": len(windows),
        }

    # ------------------------------------------------------------------
    # Actions on the currently focused window
    # ------------------------------------------------------------------
    def move_to_monitor(self, direction: str) -> dict:
        d = direction.lower().strip()
        if d not in ("left", "right"):
            return {"ok": False, "error": "Direction must be 'left' or 'right'"}
        return _press(f"super+shift+{d}")

    def maximize(self) -> dict: return _press("super+up")
    def unmaximize(self) -> dict: return _press("super+down")
    def tile_left(self) -> dict: return _press("super+left")
    def tile_right(self) -> dict: return _press("super+right")
    def minimize(self) -> dict: return _press("super+h")
    def close(self) -> dict: return _press("alt+f4")
    def switch_workspace(self, num: int) -> dict:
        if not 1 <= num <= 9: return {"ok": False, "error": "Workspace 1-9"}
        return _press(f"super+{num}")
    def overview(self) -> dict: return _press("super")

    def info(self) -> dict:
        return {"ok": True, "backend": "AT-SPI", "actions": [
            "focus_window(name) — focus a window by partial title match",
            "list_windows — show all window titles",
            "move_to_monitor(left|right), maximize, tile_left, tile_right, minimize, close",
        ]}
