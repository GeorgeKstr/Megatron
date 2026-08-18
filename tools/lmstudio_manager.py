"""
LM Studio process manager — auto-start, model listing, model switching.

Handles:
- Detecting if LM Studio is running
- Launching it if not
- Listing downloaded models from the filesystem
- Switching models via GUI automation (pyautogui)
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LMSTUDIO_BIN = os.path.expanduser("~/.local/bin/lm-studio")
MODELS_DIR = Path.home() / ".lmstudio" / "models"
API_PORT = 1234  # LM Studio's OpenAI-compatible endpoint
_STARTUP_TIMEOUT = 30  # seconds to wait for LM Studio to boot


class LMStudioManager:
    """Manages the LM Studio application lifecycle."""

    # ------------------------------------------------------------------
    # Health / status
    # ------------------------------------------------------------------
    def is_running(self) -> bool:
        """Check if LM Studio's API server is responding."""
        import urllib.request
        try:
            req = urllib.request.Request(
                f"http://localhost:{API_PORT}/v1/models",
                method="GET",
            )
            urllib.request.urlopen(req, timeout=5)
            return True
        except Exception:
            return False

    def get_loaded_model(self) -> Optional[str]:
        """Return the ID of the currently loaded model, or None."""
        import urllib.request
        try:
            req = urllib.request.Request(
                f"http://localhost:{API_PORT}/v1/models",
                method="GET",
            )
            resp = urllib.request.urlopen(req, timeout=5)
            data = json.loads(resp.read())
            models = data.get("data", [])
            return models[0]["id"] if models else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Start / stop
    # ------------------------------------------------------------------
    def start(self) -> dict:
        """Launch LM Studio if it's not already running. Blocks until API is up."""
        if self.is_running():
            model = self.get_loaded_model()
            return {"ok": True, "message": "LM Studio is already running.", "model": model}

        if not os.path.exists(LMSTUDIO_BIN):
            return {
                "ok": False,
                "error": f"LM Studio binary not found at {LMSTUDIO_BIN}. "
                         "Install LM Studio or set the correct path.",
            }

        logger.info("Starting LM Studio …")
        try:
            subprocess.Popen(
                [LMSTUDIO_BIN],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,  # detach from our process
            )
        except Exception as e:
            return {"ok": False, "error": f"Failed to launch LM Studio: {e}"}

        # Wait for API to come up
        waited = 0
        while waited < _STARTUP_TIMEOUT:
            if self.is_running():
                model = self.get_loaded_model()
                return {
                    "ok": True,
                    "message": f"LM Studio started (waited {waited}s).",
                    "model": model,
                }
            time.sleep(2)
            waited += 2

        return {"ok": False, "error": f"LM Studio did not start within {_STARTUP_TIMEOUT}s. Check manually."}

    # ------------------------------------------------------------------
    # Model discovery
    # ------------------------------------------------------------------
    def list_models(self) -> dict:
        """
        Scan the LM Studio models directory and return available models.
        Each model has: id, path (relative), size_mb, is_vision (has mmproj).
        """
        if not MODELS_DIR.exists():
            return {"ok": True, "models": [], "models_dir": str(MODELS_DIR)}

        models = []
        for gguf in MODELS_DIR.rglob("*.gguf"):
            rel = gguf.relative_to(MODELS_DIR)
            # Model ID is the directory path relative to models dir, minus the filename
            model_id = str(rel.parent)

            size_mb = round(gguf.stat().st_size / (1024 * 1024), 1)
            name = gguf.stem

            # Check if there's a vision projector (mmproj) nearby
            parent = gguf.parent
            has_vision = any(
                f.name.lower().startswith("mmproj") for f in parent.iterdir()
                if f.suffix == ".gguf"
            )

            models.append({
                "id": model_id,
                "name": name,
                "file": str(rel),
                "size_mb": size_mb,
                "has_vision": has_vision,
                "full_path": str(gguf),
            })

        models.sort(key=lambda m: m["name"].lower())
        return {"ok": True, "models": models, "count": len(models), "models_dir": str(MODELS_DIR)}

    # ------------------------------------------------------------------
    # Model switching (GUI automation via pyautogui)
    # ------------------------------------------------------------------
    def switch_model(self, model_search: str) -> dict:
        """
        Switch to a different model by automating the LM Studio GUI.
        
        1. Focus the LM Studio window
        2. Click the model selector (top bar)
        3. Type the model name to filter
        4. Click the first result
        5. Wait for load
        
        *model_search* is a substring to match in model names.
        """
        if not self.is_running():
            return {"ok": False, "error": "LM Studio is not running. Start it first."}

        # Find matching models
        result = self.list_models()
        if not result.get("ok"):
            return result

        matches = [
            m for m in result["models"]
            if model_search.lower() in m["name"].lower()
            or model_search.lower() in m["id"].lower()
        ]
        if not matches:
            return {
                "ok": False,
                "error": f"No model matching '{model_search}' found. "
                         f"Available: {[m['name'] for m in result['models'][:10]]}",
            }

        target = matches[0]
        logger.info("Switching to model: %s", target["name"])

        try:
            import pyautogui
            pyautogui.FAILSAFE = True
        except ImportError:
            return {"ok": False, "error": "pyautogui not installed. Run: pip install pyautogui"}

        # --- Step 1: Focus LM Studio window ---
        try:
            # Try wmctrl first, then fall back to alt+tab
            subprocess.run(
                ["wmctrl", "-a", "LM Studio"],
                capture_output=True, timeout=5,
            )
            time.sleep(0.5)
        except Exception:
            # Fallback: use Alt+Tab (but this requires the GUI to be visible)
            logger.warning("Could not focus LM Studio window via wmctrl")

        # --- Step 2: Click the model selector ---
        # In LM Studio, the model selector is typically at the top center of the window.
        # The window is usually ~1200x800; the selector is around (600, 30).
        # We'll take a more robust approach: move mouse to the top-center area and click.
        screen_w, screen_h = pyautogui.size()
        # Try clicking in the top-center area where the model dropdown usually is
        pyautogui.click(screen_w // 2, 40)
        time.sleep(0.8)

        # --- Step 3: Clear search and type model name ---
        # Press Ctrl+A to select all, then type the search
        pyautogui.hotkey("ctrl", "a")
        time.sleep(0.2)
        pyautogui.typewrite(model_search, interval=0.03)
        time.sleep(1.0)

        # --- Step 4: Press Down to select first result, Enter to confirm ---
        pyautogui.press("down")
        time.sleep(0.3)
        pyautogui.press("enter")
        time.sleep(0.5)

        # --- Step 5: Wait for model to load ---
        # The model needs time to unload the old one and load the new one.
        logger.info("Waiting for model to load …")
        waited = 0
        max_wait = 60
        while waited < max_wait:
            time.sleep(3)
            waited += 3
            try:
                loaded = self.get_loaded_model()
                if loaded and target["name"].lower() in loaded.lower():
                    return {
                        "ok": True,
                        "message": f"Switched to model: {loaded} (took {waited}s).",
                        "model": loaded,
                        "target": target["name"],
                    }
            except Exception:
                pass

        # Even if we timed out, check what's loaded now
        current = self.get_loaded_model()
        return {
            "ok": True,
            "message": f"Model switch initiated. Now loaded: {current}. Target was: {target['name']}.",
            "model": current,
            "note": "Model may still be loading. Check LM Studio GUI.",
        }
