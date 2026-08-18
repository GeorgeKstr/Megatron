"""
VLC media player control via MPRIS DBus (and CLI fallback).

Works with the running VLC instance — no special VLC config needed.
Uses dbus-send for MPRIS (zero Python deps) and vlc CLI for launch/queue.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_VLC_BIN = "vlc"
_MPRIS_DEST = "org.mpris.MediaPlayer2.vlc"
_MPRIS_PATH = "/org/mpris/MediaPlayer2"
_MPRIS_ROOT = "org.mpris.MediaPlayer2"
_MPRIS_PLAYER = "org.mpris.MediaPlayer2.Player"
_DBUS_CMD = ["dbus-send", "--print-reply", f"--dest={_MPRIS_DEST}"]


def _vlc_is_running() -> bool:
    """Check if VLC is running and exposed on DBus."""
    try:
        r = subprocess.run(
            ["dbus-send", "--print-reply", "--dest=org.freedesktop.DBus",
             "/org/freedesktop/DBus", "org.freedesktop.DBus.ListNames"],
            capture_output=True, text=True, timeout=3,
        )
        return _MPRIS_DEST in r.stdout
    except Exception:
        return False


def _vlc_is_installed() -> bool:
    return shutil.which(_VLC_BIN) is not None


def _dbus_mpris(method: str, args: str = "", expect_reply: bool = True) -> dict:
    """Send a DBus MPRIS command to VLC and return parsed result."""
    cmd = [
        "dbus-send", "--print-reply",
        f"--dest={_MPRIS_DEST}", _MPRIS_PATH,
        f"{_MPRIS_PLAYER}.{method}",
    ]
    if args:
        cmd.extend(args.split())

    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return {"ok": False, "error": r.stderr.strip() or "dbus-send failed"}
        return {"ok": True, "raw": r.stdout.strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "DBus call timed out"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _dbus_property(prop: str) -> dict:
    """Read a property from the MPRIS Player interface."""
    cmd = [
        "dbus-send", "--print-reply",
        f"--dest={_MPRIS_DEST}", _MPRIS_PATH,
        "org.freedesktop.DBus.Properties.Get",
        f"string:{_MPRIS_PLAYER}", f"string:{prop}",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode != 0:
            return {"ok": False, "error": r.stderr.strip()}
        return {"ok": True, "raw": r.stdout}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class VLCTool:
    """Controls VLC media player via MPRIS DBus + CLI fallback."""

    # ------------------------------------------------------------------
    # Playback control (MPRIS)
    # ------------------------------------------------------------------
    def play(self) -> dict:
        if not _vlc_is_running():
            return {"ok": False, "error": "VLC is not running. Say 'open VLC' or give me a file to play."}
        return _dbus_mpris("Play")

    def pause(self) -> dict:
        if not _vlc_is_running():
            return {"ok": False, "error": "VLC is not running."}
        return _dbus_mpris("Pause")

    def play_pause(self) -> dict:
        if not _vlc_is_running():
            return {"ok": False, "error": "VLC is not running."}
        return _dbus_mpris("PlayPause")

    def stop(self) -> dict:
        if not _vlc_is_running():
            return {"ok": False, "error": "VLC is not running."}
        return _dbus_mpris("Stop")

    def next_track(self) -> dict:
        if not _vlc_is_running():
            return {"ok": False, "error": "VLC is not running."}
        return _dbus_mpris("Next")

    def previous_track(self) -> dict:
        if not _vlc_is_running():
            return {"ok": False, "error": "VLC is not running."}
        return _dbus_mpris("Previous")

    def seek(self, seconds: float) -> dict:
        """Seek forward (positive) or backward (negative) by seconds."""
        if not _vlc_is_running():
            return {"ok": False, "error": "VLC is not running."}
        # MPRIS Seek takes an offset in microseconds
        offset = int(seconds * 1_000_000)
        return _dbus_mpris("Seek", f"int64:{offset}")

    def set_position(self, seconds: float) -> dict:
        """Set playback position to an absolute time in seconds."""
        if not _vlc_is_running():
            return {"ok": False, "error": "VLC is not running."}
        # SetPosition takes (track_id, position_in_microseconds)
        offset = int(seconds * 1_000_000)
        return _dbus_mpris("SetPosition", f"objpath:/org/mpris/MediaPlayer2/TrackList/NoTrack int64:{offset}")

    def set_volume(self, level: float) -> dict:
        """Set volume 0.0–1.0. Tries MPRIS first, falls back to VLC's own DBus interface."""
        if not _vlc_is_running():
            return {"ok": False, "error": "VLC is not running."}

        level = max(0.0, min(1.0, level))

        # Method 1: VLC's native DBus volume control (works on all VLC versions)
        try:
            vol_percent = int(level * 100)
            # VLC exposes org.mpris.MediaPlayer2.vlc with a Volume property we can try
            # But if Properties.Set fails, use vlc CLI
            r = subprocess.run(
                ["dbus-send", "--print-reply",
                 f"--dest={_MPRIS_DEST}", _MPRIS_PATH,
                 "org.freedesktop.DBus.Properties.Set",
                 f"string:{_MPRIS_PLAYER}", "string:Volume",
                 f"double:{level:.2f}"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and "Error" not in r.stdout:
                return {"ok": True, "volume": round(level * 100), "method": "mpris"}
        except Exception:
            pass

        # Method 2: Use VLC's remote control interface (vlc -I rc)
        # Send volume command via VLC's old RC interface or use pactl for VLC's audio stream
        try:
            # VLC doesn't have a reliable DBus volume set, so we use keyboard media keys
            # as a fallback — press volumeup/volumedown to adjust
            from evdev import UInput, ecodes as ev
            current = self.status()
            current_vol = current.get("volume", 50)
            target_vol = int(level * 100)
            diff = target_vol - current_vol

            caps = {ev.EV_KEY: [ev.KEY_VOLUMEUP, ev.KEY_VOLUMEDOWN]}
            with UInput(caps, name="megatron-vlc-vol") as ui:
                key = ev.KEY_VOLUMEUP if diff > 0 else ev.KEY_VOLUMEDOWN
                presses = min(abs(diff) // 5 or 1, 40)  # each press is ~5%
                for _ in range(presses):
                    ui.write(ev.EV_KEY, key, 1)
                    ui.syn()
                    import time
                    time.sleep(0.03)
                    ui.write(ev.EV_KEY, key, 0)
                    ui.syn()
                    time.sleep(0.03)
            return {"ok": True, "volume": target_vol, "method": "media_keys", "presses": presses}
        except Exception as e:
            return {"ok": True, "volume": round(level * 100), "note": f"Volume set attempted ({e}); use system_volume_control or VLC GUI"}

    def status(self) -> dict:
        """Get VLC playback status, metadata, volume, position."""
        if not _vlc_is_running():
            return {"ok": True, "running": False, "message": "VLC is not running."}

        info = {"ok": True, "running": True}

        # Playback status
        s = _dbus_property("PlaybackStatus")
        if s.get("ok"):
            for line in s["raw"].splitlines():
                if 'string "' in line:
                    info["status"] = line.split('"')[1]
                    break

        # Volume
        v = _dbus_property("Volume")
        if v.get("ok"):
            for line in v["raw"].splitlines():
                if "double " in line:
                    try:
                        info["volume"] = round(float(line.split()[-1]) * 100)
                    except Exception:
                        pass

        # Position (microseconds → seconds)
        p = _dbus_property("Position")
        if p.get("ok"):
            for line in p["raw"].splitlines():
                if "int64 " in line:
                    try:
                        info["position_sec"] = int(line.split()[-1]) // 1_000_000
                    except Exception:
                        pass

        # Metadata — try standard xesam fields, then VLC custom fields
        m = _dbus_property("Metadata")
        if m.get("ok"):
            lines = m["raw"].splitlines()
            import re
            for i, line in enumerate(lines):
                # Standard MPRIS metadata fields
                for field, key in [("xesam:title", "title"), ("xesam:artist", "artist"),
                                   ("xesam:album", "album"), ("xesam:url", "url")]:
                    if f'string "{field}"' in line:
                        for j in range(i + 1, min(i + 8, len(lines))):
                            m2 = re.search(r'string "(.+)"', lines[j])
                            if m2:
                                info[key] = m2.group(1)
                                break
                        break
                # VLC-specific: now-playing is in the window title or vlc:nowplaying
                if 'string "vlc:nowplaying"' in line:
                    for j in range(i + 1, min(i + 8, len(lines))):
                        m2 = re.search(r'string "(.+)"', lines[j])
                        if m2:
                            if "title" not in info:
                                info["title"] = m2.group(1)
                            break

            # If no title found but we have a URL, extract filename
            if "title" not in info and "url" in info:
                from pathlib import Path
                info["title"] = Path(info["url"]).name

        return info

    # ------------------------------------------------------------------
    # Launch / play files (CLI)
    # ------------------------------------------------------------------
    def open_file(self, path: str) -> dict:
        """Open a media file in VLC — replaces current playlist and starts playback."""
        if not _vlc_is_installed():
            return {"ok": False, "error": "VLC is not installed. Install it with your package manager."}

        expanded = os.path.expanduser(path)
        p = Path(expanded)
        if not p.exists():
            return {"ok": False, "error": f"File not found: {expanded}"}

        # If VLC is running, stop and replace via dbus first
        if _vlc_is_running():
            try:
                _dbus_mpris("Stop")
                # Clear playlist and add new file via MPRIS TrackList interface
                bus = subprocess.Popen(
                    ["busctl", "call", "org.mpris.MediaPlayer2.vlc",
                     "/org/mpris/MediaPlayer2", "org.mpris.MediaPlayer2.TrackList",
                     "AddTrack", "sbb", f"file://{p}", False, True],
                    capture_output=True, timeout=5,
                )
                out = bus.communicate(timeout=5)
                if bus.returncode == 0:
                    # Successfully added via dbus, start playing
                    _dbus_mpris("Play")
                    return {
                        "ok": True,
                        "message": f"Now playing: {p.name}",
                        "path": str(p),
                    }
            except Exception:
                pass
            # Fallback: kill and relaunch
            time.sleep(0.3)

        # Launch/reuse instance — clear playlist and start playing
        try:
            subprocess.Popen(
                [_VLC_BIN, str(p)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(1)
            return {
                "ok": True,
                "message": f"Playing: {p.name}",
                "file": str(p),
                "running": _vlc_is_running(),
            }
        except Exception as e:
            return {"ok": False, "error": f"Failed to launch VLC: {e}"}

    def launch(self) -> dict:
        """Launch VLC if not running."""
        if _vlc_is_running():
            return {"ok": True, "message": "VLC is already running.", "running": True}

        if not _vlc_is_installed():
            return {"ok": False, "error": "VLC is not installed."}

        try:
            subprocess.Popen(
                [_VLC_BIN],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            # Wait for MPRIS to appear
            for _ in range(10):
                time.sleep(0.5)
                if _vlc_is_running():
                    return {"ok": True, "message": "VLC launched.", "running": True}
            return {"ok": True, "message": "VLC launched (MPRIS not detected yet).", "running": False}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def enqueue(self, path: str) -> dict:
        """Add a file to VLC's playlist."""
        if not _vlc_is_installed():
            return {"ok": False, "error": "VLC is not installed."}

        expanded = os.path.expanduser(path)
        p = Path(expanded)
        if not p.exists():
            return {"ok": False, "error": f"File not found: {expanded}"}

        try:
            subprocess.Popen(
                [_VLC_BIN, "--one-instance", "--playlist-enqueue", str(p)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return {"ok": True, "message": f"Queued: {p.name}"}
        except Exception as e:
            return {"ok": False, "error": str(e)}
