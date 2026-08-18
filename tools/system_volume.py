"""
System volume control — adjusts the host PC's master volume.
Supports PipeWire/PulseAudio (pactl) and ALSA (amixer).
"""
from __future__ import annotations

import logging
import re
import subprocess
from typing import Optional

logger = logging.getLogger(__name__)


class SystemVolumeTool:
    """Reads and sets the system master volume."""

    def __init__(self):
        self._backend: Optional[str] = None

    def _detect_backend(self) -> str:
        """Detect which audio system is available."""
        if self._backend:
            return self._backend

        # Check for PipeWire/PulseAudio
        try:
            subprocess.run(["pactl", "info"], capture_output=True, timeout=5, check=True)
            self._backend = "pactl"
            return self._backend
        except Exception:
            pass

        # Check for wpctl (wireplumber)
        try:
            subprocess.run(["wpctl", "status"], capture_output=True, timeout=5, check=True)
            self._backend = "wpctl"
            return self._backend
        except Exception:
            pass

        # Check for ALSA
        try:
            subprocess.run(["amixer", "sget", "Master"], capture_output=True, timeout=5, check=True)
            self._backend = "amixer"
            return self._backend
        except Exception:
            pass

        self._backend = "none"
        return self._backend

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_volume(self) -> dict:
        """Return current system volume (0-100), mute state, and backend."""
        backend = self._detect_backend()

        if backend == "pactl":
            return self._get_volume_pactl()
        elif backend == "wpctl":
            return self._get_volume_wpctl()
        elif backend == "amixer":
            return self._get_volume_amixer()
        else:
            return {"ok": False, "error": "No supported audio backend found (tried pactl, wpctl, amixer)"}

    def set_volume(self, level: int) -> dict:
        """
        Set system volume to *level* (0–100).
        """
        level = max(0, min(100, int(level)))
        backend = self._detect_backend()

        if backend == "pactl":
            return self._set_volume_pactl(level)
        elif backend == "wpctl":
            return self._set_volume_wpctl(level)
        elif backend == "amixer":
            return self._set_volume_amixer(level)
        else:
            return {"ok": False, "error": "No supported audio backend found"}

    def mute(self) -> dict:
        """Toggle mute on/off."""
        backend = self._detect_backend()
        if backend == "pactl":
            subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"], capture_output=True)
        elif backend == "wpctl":
            # wpctl doesn't have a simple toggle, so get current and set
            info = self._get_volume_wpctl()
            if info.get("ok"):
                subprocess.run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"], capture_output=True)
        elif backend == "amixer":
            subprocess.run(["amixer", "set", "Master", "toggle"], capture_output=True)
        return self.get_volume()

    # ------------------------------------------------------------------
    # pactl (PipeWire / PulseAudio)
    # ------------------------------------------------------------------
    def _get_volume_pactl(self) -> dict:
        try:
            r = subprocess.run(
                ["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                capture_output=True, text=True, timeout=5,
            )
            # Example: "Volume: front-left: 32768 /  50% / -18.06 dB,   front-right: 32768 /  50% / -18.06 dB"
            m = re.search(r"(\d+)%", r.stdout)
            vol = int(m.group(1)) if m else 0

            r2 = subprocess.run(
                ["pactl", "get-sink-mute", "@DEFAULT_SINK@"],
                capture_output=True, text=True, timeout=5,
            )
            muted = "yes" in r2.stdout.lower()

            return {"ok": True, "volume": vol, "muted": muted, "backend": "pactl"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _set_volume_pactl(self, level: int) -> dict:
        try:
            subprocess.run(
                ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"],
                capture_output=True, timeout=5,
            )
            return self._get_volume_pactl()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # wpctl (WirePlumber)
    # ------------------------------------------------------------------
    def _get_volume_wpctl(self) -> dict:
        try:
            r = subprocess.run(
                ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                capture_output=True, text=True, timeout=5,
            )
            # Example: "Volume: 0.50 [MUTED]"
            m = re.search(r"([\d.]+)", r.stdout)
            vol = int(float(m.group(1)) * 100) if m else 0
            muted = "MUTED" in r.stdout
            return {"ok": True, "volume": vol, "muted": muted, "backend": "wpctl"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _set_volume_wpctl(self, level: int) -> dict:
        try:
            frac = level / 100.0
            subprocess.run(
                ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{frac:.2f}"],
                capture_output=True, timeout=5,
            )
            return self._get_volume_wpctl()
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # amixer (ALSA)
    # ------------------------------------------------------------------
    def _get_volume_amixer(self) -> dict:
        try:
            r = subprocess.run(
                ["amixer", "sget", "Master"],
                capture_output=True, text=True, timeout=5,
            )
            m = re.search(r"\[(\d+)%\]", r.stdout)
            vol = int(m.group(1)) if m else 0
            muted = "[off]" in r.stdout.lower()
            return {"ok": True, "volume": vol, "muted": muted, "backend": "amixer"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _set_volume_amixer(self, level: int) -> dict:
        try:
            subprocess.run(
                ["amixer", "set", "Master", f"{level}%"],
                capture_output=True, timeout=5,
            )
            return self._get_volume_amixer()
        except Exception as e:
            return {"ok": False, "error": str(e)}
