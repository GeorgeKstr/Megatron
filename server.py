#!/usr/bin/env python3
"""
Megatron — PC Remote Control via LM Studio

A Flask + SocketIO server that:
- Exposes a mobile-friendly web UI
- Connects to LM Studio for AI-powered tool calling
- Routes prompts to specialized modules (system, media, info, browser, input, timer)
"""
from __future__ import annotations

# ── eventlet monkey-patch MUST come before any other stdlib/3rd-party imports ──
import eventlet
eventlet.monkey_patch()

import asyncio
import json
import logging
import os
import sys
import threading
import traceback
from collections import deque
from datetime import datetime

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import requests
from pathlib import Path
from PIL import Image

# ── Project imports ──
from tools.lmstudio import LMStudioClient
from tools.lmstudio_manager import LMStudioManager
from tools.timer import TimerTool

# ── Modules (all tools available to the LLM in one conversation) ──
from modules import media_module, info_module, input_module, timer_module
from modules import browser_module, system_module

# ── Unified tool catalog ──
_ALL_MODULES = [system_module, media_module, info_module, browser_module, input_module, timer_module]

# Build combined TOOL_DEFS (deduplicated by name)
_TOOL_DEFS: list[dict] = []
_TOOL_NAME_TO_MODULE: dict[str, object] = {}
for _mod in _ALL_MODULES:
    for _td in getattr(_mod, 'TOOL_DEFS', []):
        _name = _td["function"]["name"]
        if _name not in _TOOL_NAME_TO_MODULE:
            _TOOL_DEFS.append(_td)
            _TOOL_NAME_TO_MODULE[_name] = _mod

# ── Download-aware media index ──
import glob
_MEDIA_EXTENSIONS = {".mp3", ".mp4", ".mkv", ".avi", ".flac", ".wav", ".m4a", ".webm", ".ogg", ".mov"}
_DOWNLOADS = Path.home() / "Downloads"
_media_index: list[str] = []

def _build_media_index():
    """Scan ~/Downloads once at startup for media files."""
    global _media_index
    if not _DOWNLOADS.is_dir():
        return
    for ext in _MEDIA_EXTENSIONS:
        _media_index.extend(glob.glob(str(_DOWNLOADS / f"**/*{ext}"), recursive=True))

# Logger is defined below, call after that

def _media_in_downloads(query: str) -> bool:
    """Check if any media file in Downloads matches the query."""
    query_lower = query.lower()
    keywords = [w for w in query_lower.split() if len(w) > 2]  # skip short words
    if not keywords:
        return False
    for path in _media_index:
        fname = Path(path).name.lower()
        # Match if ALL keywords appear in the filename
        if all(kw in fname for kw in keywords):
            return True
    return False

# Built after logger is defined (see below)

# ── LM Studio config ──
LMSTUDIO_URL = os.environ.get("MEGATRON_LM_URL", "http://localhost:1234/v1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("megatron")

# ── Download-aware media index ──
_build_media_index()
logger.info("Media index: %d files in ~/Downloads", len(_media_index))

# ── Flask + SocketIO ──
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("MEGATRON_SECRET", "megatron-secret-" + os.urandom(8).hex())
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*", ping_timeout=30)

# ── Core services ──
lmstudio = LMStudioClient(base_url=LMSTUDIO_URL)
lm_manager = LMStudioManager()
timer_tool = TimerTool()

# Conversation memory — last N user/assistant exchanges for context
_memory: deque[dict] = deque(maxlen=6)  # 3 exchanges = 6 messages

# ── System prompt ──
SYSTEM_PROMPT = (
    "You are Megatron — an AI that controls this Linux PC via tools.\n\n"
    "Rules:\n"
    "- You see the user's screen via screenshots when needed.\n"
    "- When showing screen fragments, NEVER describe them unless the user asked.\n"
    "- Keep responses short and direct.\n"
    "- The Escape key is reserved — never use it.\n"
    "- For window movement between monitors, use move_left / move_right.\n"
    "- When you show the user an image or screen fragment, say nothing about it — the user already sees it.\n"
    "- Use show_screen_fragment with action='crop' when you know coordinates.\n"
    "- Use action='find' only when you need vision to locate something described in text.\n"
)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    model = lmstudio.model_id
    lm_ok = bool(model and model != "unreachable")
    if not lm_ok:
        try:
            models = lmstudio.list_models()
            if models:
                lmstudio.model_id = models[0]
                model = models[0]
                lm_ok = True
        except Exception:
            model = "unreachable"
            lm_ok = False
    return {
        "status": "ok",
        "lmstudio": {"reachable": lm_ok, "model": model},
    }


@app.route("/api/models")
def api_models():
    """List downloaded models for the UI dropdown."""
    return lm_manager.list_models()


@app.route("/api/model/switch", methods=["POST"])
def api_model_switch():
    """Switch the active LM Studio model."""
    data = request.get_json(silent=True) or {}
    model = data.get("model", "")
    if not model:
        return {"ok": False, "error": "No model specified"}, 400
    try:
        base = LMSTUDIO_URL.rstrip("/").rstrip("/v1")
        resp = requests.post(f"{base}/v1/models/load", json={"model": model}, timeout=30)
        if resp.status_code == 200:
            lmstudio.model_id = model
            return {"ok": True, "model": model}
    except Exception:
        pass
    try:
        base = LMSTUDIO_URL.rstrip("/").rstrip("/v1")
        resp = requests.post(f"{base}/v1/chat/completions", json={
            "model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1,
        }, timeout=60)
        if resp.status_code == 200:
            lmstudio.model_id = model
            return {"ok": True, "model": model}
        return {"ok": False, "error": resp.text[:200]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/media/status")
def api_media_status():
    try:
        from modules.media_module import vlc, volume
        status = vlc.status()
        vol = volume.get_volume()
        return {"vlc_running": status.get("ok", False), "now_playing": status.get("title", ""),
                "volume": vol.get("level", 50), "muted": vol.get("muted", False)}
    except Exception:
        return {"vlc_running": False}


@app.route("/api/media/playpause", methods=["POST"])
def api_media_playpause():
    try:
        from modules.media_module import vlc
        return vlc.play_pause()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/media/prev", methods=["POST"])
def api_media_prev():
    try:
        from modules.media_module import vlc
        return vlc.previous_track()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/media/next", methods=["POST"])
def api_media_next():
    try:
        from modules.media_module import vlc
        return vlc.next_track()
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/media/mute", methods=["POST"])
def api_media_mute():
    try:
        from modules.media_module import volume
        current = volume.get_volume()
        if current.get("muted"):
            return volume.set_volume(current.get("level", 50))
        volume.mute()
        return {"ok": True, "muted": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/media/volume", methods=["POST"])
def api_media_volume():
    data = request.get_json(silent=True) or {}
    try:
        from modules.media_module import volume
        return volume.set_volume(data.get("level", 50))
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/system/power", methods=["POST"])
def api_power():
    import subprocess
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    try:
        cmds = {"sleep": "suspend", "hibernate": "hibernate", "shutdown": "poweroff", "reboot": "reboot"}
        if action not in cmds:
            return {"ok": False, "error": f"Unknown action: {action}"}, 400
        subprocess.run(["systemctl", cmds[action]], check=True)
        return {"ok": True, "action": action}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/input/press", methods=["POST"])
def api_input_press():
    data = request.get_json(silent=True) or {}
    key = data.get("key", "")
    if not key:
        return {"ok": False, "error": "No key specified"}, 400
    try:
        from modules.input_module import controller
        return controller.press_key(key)
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ---------------------------------------------------------------------------
# SocketIO events
# ---------------------------------------------------------------------------
@socketio.on("connect")
def on_connect():
    logger.info("Client connected: %s", request.sid)
    emit("server_status", {"connected": True, "model": lmstudio.model_id})


@socketio.on("disconnect")
def on_disconnect():
    logger.info("Client disconnected: %s", request.sid)


@socketio.on("clear_history")
def on_clear_history():
    """Reset the conversation state."""
    logger.info("Clearing history for %s", request.sid)
    emit("history_cleared", {"message": "Session cleared."})


@socketio.on("prompt")
def on_prompt(data: dict):
    """
    Main entry point. Receives a user prompt, routes to the right module,
    and runs the tool-calling loop.
    """
    prompt_text = data.get("text", "").strip()
    if not prompt_text:
        emit("error", {"message": "Empty prompt"})
        return

    # ── Inject current time into every user message ──
    now = datetime.now().strftime("%A, %B %d %Y — %H:%M:%S")
    prompt_text = f"[Current time: {now}]\n\n{prompt_text}"

    logger.info("Prompt: %s", prompt_text[:120])

    try:
        result = run_agent_loop(prompt_text, request.sid)
        # Emit any images collected during tool execution
        for img_data in result.pop("_images", []):
            if img_data.get("type") == "fragment":
                emit("screen_fragment", {
                    "image": img_data["image"],
                    "bbox": img_data["bbox"],
                    "target": img_data["target"],
                })
            elif img_data.get("type") == "screenshot":
                emit("screenshot", {"image": img_data["image"]})
            elif img_data.get("type") == "images":
                emit("image_results", {"images": img_data["images"]})
        emit("response", result)
    except Exception as exc:
        logger.exception("Error handling prompt")
        emit("error", {"message": str(exc), "traceback": traceback.format_exc()})


def _execute_tool(tool_name: str, args: dict) -> dict:
    """Dispatch a tool call to the correct module."""
    mod = _TOOL_NAME_TO_MODULE.get(tool_name)
    if mod is None:
        return {"ok": False, "error": f"Unknown tool: {tool_name}"}
    return mod.execute(tool_name, args)


def run_agent_loop(user_prompt: str, sid: str) -> dict:
    """
    Single conversation loop — ALL tools visible to the LLM.
    The model can naturally chain any tools it needs.
    """
    if sid == "timer":
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.append({"role": "user", "content": user_prompt})
    else:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(list(_memory))
        messages.append({"role": "user", "content": user_prompt})

    max_turns = 10
    response_text = ""
    used_tools = False

    for turn in range(max_turns):
        raw = lmstudio.chat(messages=messages, tools=_TOOL_DEFS, max_tokens=2048)
        choice = raw["choices"][0]
        msg = choice["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            response_text = msg.get("content", "")
            break

        used_tools = True
        emit("status", {"message": "Running tool…"})

        for tc in tool_calls:
            func = tc["function"]
            tool_name = func["name"]
            try:
                args = json.loads(func.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}

            logger.info("Tool call: %s(%s)", tool_name, args)
            emit("tool_start", {"tool": tool_name, "args": args})

            try:
                tool_result = _execute_tool(tool_name, args)
            except Exception as e:
                tool_result = {"ok": False, "error": str(e)}

            # Collect image data for UI
            image_b64 = tool_result.pop("_image_base64", None) if isinstance(tool_result, dict) else None
            fragment_b64 = tool_result.pop("_fragment_base64", None) if isinstance(tool_result, dict) else None
            fragment_bbox = tool_result.pop("_fragment_bbox", None) if isinstance(tool_result, dict) else None
            fragment_target = tool_result.pop("_fragment_target", None) if isinstance(tool_result, dict) else None
            image_results = tool_result.pop("_image_results", None) if isinstance(tool_result, dict) else None

            pending_images = []
            if fragment_b64:
                pending_images.append({
                    "type": "fragment", "image": fragment_b64,
                    "bbox": fragment_bbox or {}, "target": fragment_target or "",
                })
            elif image_b64:
                pending_images.append({"type": "screenshot", "image": image_b64})
            if image_results:
                pending_images.append({"type": "images", "images": image_results})

            result_str = json.dumps(tool_result, ensure_ascii=False)
            emit("tool_result", {"tool": tool_name, "result": tool_result})

            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", f"call_{turn}"),
                "content": result_str,
            })

            # Inject screenshot into LLM conversation so the vision model sees it
            if image_b64:
                messages.append({
                    "role": "user",
                    "content": [
                        LMStudioClient.text_content(
                            "Here is the current screenshot. Use it to help answer the user's original request. "
                            "Only describe what's on screen if the user asked you to."
                        ),
                        LMStudioClient.image_content(image_b64),
                    ],
                })

            for img in pending_images:
                if img["type"] == "fragment":
                    emit("screen_fragment", {"image": img["image"], "bbox": img["bbox"], "target": img["target"]})
                elif img["type"] == "screenshot":
                    emit("screenshot", {"image": img["image"]})
                elif img["type"] == "images":
                    emit("image_results", {"images": img["images"]})

    if used_tools and not response_text:
        messages.append({"role": "user", "content": "Please summarize what happened in plain text."})
        raw = lmstudio.chat(messages=messages, tools=[], max_tokens=2048)
        response_text = raw["choices"][0]["message"].get("content", "Done.")

    if sid != "timer":
        _memory.append({"role": "user", "content": user_prompt})
        _memory.append({"role": "assistant", "content": response_text})

    result = {"text": response_text, "tool_calls_made": used_tools}
    return result


# ---------------------------------------------------------------------------
# Timer callback (re-enters the agent loop from a background thread)
# ---------------------------------------------------------------------------
def _fire_timer(description: str, action_prompt: str):
    """Called by the timer manager when a timer fires."""
    from flask_socketio import emit as sio_emit

    prompt = f"Timer '{description}' has expired. {action_prompt}"

    def _run():
        with app.app_context():
            request.sid = "timer"  # type: ignore
            try:
                result = run_agent_loop(prompt, "timer")
                sio_emit("response", result)
            except Exception as exc:
                logger.exception("Timer callback error")
                sio_emit("error", {"message": str(exc)})

    threading.Thread(target=_run, daemon=True).start()


# ---------------------------------------------------------------------------
# Boot
# ---------------------------------------------------------------------------
def main():
    logger.info("Starting Megatron on 0.0.0.0:8080")
    logger.info("LM Studio URL: %s", LMSTUDIO_URL)

    # Auto-start LM Studio if not running
    try:
        if not lm_manager.is_running():
            lm_manager.start()
    except Exception as e:
        logger.warning("Could not auto-start LM Studio: %s", e)

    try:
        model = lmstudio.model_id
        logger.info("LM Studio model loaded: %s", model or "none")
    except Exception as e:
        logger.warning("Could not connect to LM Studio: %s", e)

    # Timer retrigger helper
    timer_tool.set_callback(_fire_timer)

    socketio.run(app, host="0.0.0.0", port=8080, debug=False)


if __name__ == "__main__":
    main()
