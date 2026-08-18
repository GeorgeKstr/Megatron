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
import re
import sys
import threading
import traceback
from collections import deque
from datetime import datetime

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from PIL import Image

# ── Project imports ──
from tools.lmstudio import LMStudioClient
from tools.lmstudio_manager import LMStudioManager
from tools.timer import TimerTool
from modules.router import route as route_prompt, get_module, get_all_tools, set_lmstudio_client

# ── LM Studio config ──
LMSTUDIO_URL = os.environ.get("MEGATRON_LM_URL", "http://localhost:1234/v1")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("megatron")

# ── Flask + SocketIO ──
app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("MEGATRON_SECRET", "megatron-secret-" + os.urandom(8).hex())
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*", ping_timeout=30)

# ── Core services ──
lmstudio = LMStudioClient(base_url=LMSTUDIO_URL)
lm_manager = LMStudioManager()
timer_tool = TimerTool()

# Register LM Studio client for LLM-assisted routing
set_lmstudio_client(lmstudio)

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
    """Switch the active LM Studio model via LM Studio's local server API."""
    data = request.get_json(silent=True) or {}
    model = data.get("model", "")
    if not model:
        return {"ok": False, "error": "No model specified"}, 400
    try:
        # LM Studio local server supports model switching via POST to /v1/models/load
        # Fallback: just update the client and let the next request use it
        base = LMSTUDIO_URL.rstrip("/").rstrip("/v1")
        
        # Try the model load endpoint first
        try:
            resp = requests.post(
                f"{base}/v1/models/load",
                json={"model": model},
                timeout=30,
            )
            if resp.status_code == 200:
                lmstudio.model_id = model
                return {"ok": True, "model": model}
        except Exception:
            pass

        # Fallback: send a dummy chat request with the new model
        # LM Studio will load it if it's available
        resp = requests.post(
            f"{base}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 1,
            },
            timeout=60,
        )
        if resp.status_code == 200:
            lmstudio.model_id = model
            return {"ok": True, "model": model}

        # Check if it's a 400 with model not found error
        err = resp.text[:200]
        return {"ok": False, "error": f"Model not found or failed to load: {err}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/media/status")
def api_media_status():
    """Get VLC status for the media player bar."""
    try:
        from modules.media_module import vlc, volume
        status = vlc.status()
        vol = volume.get_volume()
        return {
            "vlc_running": status.get("ok", False),
            "now_playing": status.get("title", ""),
            "volume": vol.get("level", 50),
            "muted": vol.get("muted", False),
        }
    except Exception:
        return {"vlc_running": False}


@app.route("/api/media/playpause", methods=["POST"])
def api_media_playpause():
    """Toggle VLC play/pause."""
    try:
        from modules.media_module import vlc
        result = vlc.play_pause()
        return result
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
    """Toggle system mute."""
    try:
        from modules.media_module import volume
        current = volume.get_volume()
        if current.get("muted"):
            result = volume.set_volume(current.get("level", 50))
            result["muted"] = False
        else:
            result = {"ok": True, "muted": True}
            volume.mute()
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/media/volume", methods=["POST"])
def api_media_volume():
    """Set system volume."""
    data = request.get_json(silent=True) or {}
    try:
        from modules.media_module import volume
        return volume.set_volume(data.get("level", 50))
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.route("/api/system/power", methods=["POST"])
def api_power():
    """Sleep / hibernate / shutdown / reboot."""
    import subprocess
    data = request.get_json(silent=True) or {}
    action = data.get("action", "")
    try:
        if action == "sleep":
            subprocess.run(["systemctl", "suspend"], check=True)
        elif action == "hibernate":
            subprocess.run(["systemctl", "hibernate"], check=True)
        elif action == "shutdown":
            subprocess.run(["systemctl", "poweroff"], check=True)
        elif action == "reboot":
            subprocess.run(["systemctl", "reboot"], check=True)
        else:
            return {"ok": False, "error": f"Unknown action: {action}"}, 400
        return {"ok": True, "action": action}
    except subprocess.CalledProcessError as e:
        return {"ok": False, "error": str(e)}, 500
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


@app.route("/api/input/press", methods=["POST"])
def api_input_press():
    """Press a key directly (bypass model)."""
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


def run_agent_loop(user_prompt: str, sid: str) -> dict:
    """
    Route the prompt to one or more modules, then run the tool-calling loop
    for each module sequentially. Simple commands execute directly without the LLM.
    """
    if sid == "timer":
        route_steps = [("timer", user_prompt)]
    else:
        route_steps = route_prompt(user_prompt)

    logger.info("Routing: %s", route_steps)

    all_responses = []
    pending_images: list[dict] = []

    # Simple command patterns that bypass the LLM — executed directly
    SIMPLE_COMMANDS = {
        "stop playback": lambda: mod_execute("media", "stop_playback", {}),
    }

    def _is_simple_command(sub_prompt: str) -> tuple[bool, str]:
        """Check if a sub-prompt is a simple command. Returns (is_simple, tool_name)."""
        p = sub_prompt.lower().strip()
        if p == "stop playback":
            return True, "stop_playback"
        # "set volume to X%" → set_volume
        vol_match = re.match(r"set volume to (\d+)%?", p)
        if vol_match:
            return True, "set_volume"
        return False, ""

    def mod_execute(module: str, tool: str, args: dict) -> dict:
        """Execute a tool on a module directly."""
        m = get_module(module)
        return m.execute(tool, args)

    for step_idx, (module_name, sub_prompt) in enumerate(route_steps):
        is_simple, simple_tool = _is_simple_command(sub_prompt)

        if is_simple:
            # Execute directly — no LLM
            emit("status", {"message": f"Step {step_idx + 1}/{len(route_steps)}: {sub_prompt}"})
            logger.info("Direct execute: %s → %s", module_name, sub_prompt)

            if simple_tool == "stop_playback":
                tool_result = mod_execute("media", "stop_playback", {})
            elif simple_tool == "set_volume":
                vol = int(re.search(r"(\d+)", sub_prompt).group(1))
                tool_result = mod_execute("media", "set_volume", {"level": vol})
            else:
                tool_result = {"ok": False, "error": "Unknown simple command"}

            emit("tool_result", {"tool": simple_tool, "result": tool_result})
            all_responses.append(tool_result.get("message", ""))
            continue

        # Normal LLM-based execution
        mod = get_module(module_name)
        tool_defs = mod.TOOL_DEFS
        logger.info("Step %d: module=%s (%d tools) — %s", step_idx + 1, module_name, len(tool_defs), sub_prompt[:80])

        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(list(_memory))
        messages.append({"role": "user", "content": sub_prompt})

        max_turns = 5
        step_text = ""
        step_used_tools = False

        for turn in range(max_turns):
            raw = lmstudio.chat(messages=messages, tools=tool_defs, max_tokens=2048)
            choice = raw["choices"][0]
            msg = choice["message"]
            messages.append(msg)

            tool_calls = msg.get("tool_calls")
            if not tool_calls:
                step_text = msg.get("content", "")
                break

            step_used_tools = True
            emit("status", {"message": f"Step {step_idx + 1}/{len(route_steps)}: Running tool…"})

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
                    tool_result = mod.execute(tool_name, args)
                except Exception as e:
                    tool_result = {"ok": False, "error": str(e)}

                # Collect image data for UI
                image_b64 = tool_result.pop("_image_base64", None) if isinstance(tool_result, dict) else None
                fragment_b64 = tool_result.pop("_fragment_base64", None) if isinstance(tool_result, dict) else None
                fragment_bbox = tool_result.pop("_fragment_bbox", None) if isinstance(tool_result, dict) else None
                fragment_target = tool_result.pop("_fragment_target", None) if isinstance(tool_result, dict) else None
                image_results = tool_result.pop("_image_results", None) if isinstance(tool_result, dict) else None

                if fragment_b64:
                    pending_images.append({
                        "type": "fragment",
                        "image": fragment_b64,
                        "bbox": fragment_bbox or {},
                        "target": fragment_target or "",
                    })
                elif image_b64:
                    pending_images.append({"type": "screenshot", "image": image_b64})

                if image_results:
                    pending_images.append({"type": "images", "images": image_results})

                result_str = json.dumps(tool_result, ensure_ascii=False)
                emit("tool_result", {"tool": tool_name, "result": tool_result})

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", f"call_{step_idx}_{turn}"),
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

        if step_used_tools and not step_text:
            # Exhausted turns — ask for summary
            messages.append({"role": "user", "content": "Please summarize what happened in plain text."})
            raw = lmstudio.chat(messages=messages, tools=[], max_tokens=2048)
            step_text = raw["choices"][0]["message"].get("content", "Done.")

        all_responses.append(step_text)

    # Combine all step responses
    final_text = "\n\n".join(all_responses) if all_responses else "Done."

    if sid != "timer":
        _memory.append({"role": "user", "content": user_prompt})
        _memory.append({"role": "assistant", "content": final_text})

    result = {"text": final_text, "tool_calls_made": len(route_steps) > 0, "modules": [m[0] for m in route_steps]}
    if pending_images:
        result["_images"] = pending_images
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
        lm_manager.ensure_running()
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
