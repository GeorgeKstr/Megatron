"""
Screenshot tool — xdg-desktop-portal only. No fallbacks, no mss, no black images.
"""
import base64
import io
import logging
import os
import random
import string
import subprocess
import time
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

logger = logging.getLogger(__name__)


class ScreenshotTool:
    """Captures screen via xdg-desktop-portal — the Wayland-native method."""

    def __init__(self):
        self._last_full: Optional[Image.Image] = None
        self._screenshot_dir = Path.home() / ".megatron" / "screenshots"
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    def capture(self) -> Image.Image:
        img = self._capture_portal()
        if img is None:
            raise RuntimeError(
                "Screenshot failed. Is xdg-desktop-portal-gnome running? "
                "Check: systemctl --user status xdg-desktop-portal-gnome"
            )
        self._last_full = img
        logger.info("Screenshot: %dx%d", img.width, img.height)
        return img

    def _capture_portal(self) -> Optional[Image.Image]:
        search_dirs = [
            Path.home() / "Pictures",
            Path.home() / "Pictures" / "Screenshots",
        ]

        # Track files before the call
        before = set()
        for d in search_dirs:
            if d.exists():
                for p in d.iterdir():
                    if p.suffix == ".png":
                        before.add(str(p))

        token = "megatron_" + "".join(random.choices(string.ascii_lowercase, k=8))

        result = subprocess.run(
            [
                "gdbus", "call", "--session",
                "--dest", "org.freedesktop.portal.Desktop",
                "--object-path", "/org/freedesktop/portal/desktop",
                "--method", "org.freedesktop.portal.Screenshot.Screenshot",
                "",
                f"{{'handle_token': <'{token}'>, 'interactive': <false>}}",
            ],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            logger.error("Portal call failed: %s", result.stderr)
            return None

        # Wait for the new file
        for _ in range(60):
            time.sleep(0.1)
            for d in search_dirs:
                if not d.exists():
                    continue
                for p in d.iterdir():
                    sp = str(p)
                    if sp not in before and p.suffix == ".png":
                        time.sleep(0.2)
                        try:
                            img = Image.open(sp).convert("RGB")
                            # Verify not empty/corrupt
                            if img.width < 100 or img.height < 100:
                                continue
                            return img
                        except Exception:
                            continue
        return None

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------
    @property
    def last_full(self) -> Optional[Image.Image]:
        return self._last_full

    def to_base64(self, img: Image.Image, fmt: str = "jpeg", quality: int = 75) -> str:
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format=fmt.upper(), quality=quality)
        return base64.b64encode(buf.getvalue()).decode("utf-8")

    def crop_to_region(self, img: Image.Image, bbox: Tuple[int, int, int, int]) -> Image.Image:
        return img.crop(bbox)

    def save(self, img: Image.Image, name: str = "capture") -> Path:
        path = self._screenshot_dir / f"{name}.jpg"
        img.convert("RGB").save(path, quality=85)
        return path
