"""
Router — LLM-assisted prompt routing.

Sends the prompt to the model with a minimal classification prompt that returns
JSON routing steps. Each step is a (module_name, sub_prompt) tuple.
"""

import json
import logging
from modules import system_module, media_module, browser_module, info_module, input_module, timer_module
from modules.media_index import matches as media_in_downloads

logger = logging.getLogger(__name__)

MODULES = [
    ("info",     info_module),
    ("media",    media_module),
    ("timer",    timer_module),
    ("browser",  browser_module),
    ("input",    input_module),
    ("system",   system_module),
]

# ── Playback state ──────────────────────────────────────────────────────
playback_state: dict = {"app": None, "title": ""}

def get_playback_state() -> dict:
    return dict(playback_state)

def set_playback_state(app: str | None, title: str = ""):
    playback_state["app"] = app
    playback_state["title"] = title

# ── Tool catalog for the LLM classifier ────────────────────────────────
_TOOL_CATALOG = """
Available modules and their tools:

system: take_screenshot, show_screen_fragment, window_control, run_terminal_command, stop_playback
media: media_library, stop_playback, vlc_play, vlc_pause, vlc_stop, vlc_next, vlc_previous, vlc_status, vlc_open, vlc_enqueue, set_volume, volume_adjust
browser: browser_navigate, browser_get_content, browser_get_links, browser_get_forms, browser_click, browser_fill, browser_page_info, browser_evaluate
info: web_search, search_images, get_weather, check_email
input: input_action
timer: timer
"""

_ROUTER_SYSTEM = f"""You are a router. Classify the user's request into sequential steps.
Each step picks ONE module and a short sub-prompt for it.

{_TOOL_CATALOG}
Rules:
- "play X" where X matches the user's Downloads → media module, sub-prompt: "play X"
- "play X" where X is NOT in Downloads → browser module, sub-prompt: "search youtube for: X"
- "play X at N%" → first stop current playback, then play/search, then set volume
- "play X from downloads" or "play X on vlc" → media module
- "play X on youtube" or "play X on yt" → browser module
- Volume commands like "set volume to N%", "mute", "volume up" → media
- If nothing matches, use system module.
- Return ONLY a JSON array of {{"module": "...", "prompt": "..."}} objects.
- No explanation, no markdown, just JSON.
"""

_lmstudio_ref = None

def set_lmstudio_client(client):
    """Set the LM Studio client for LLM-assisted routing."""
    global _lmstudio_ref
    _lmstudio_ref = client


def route(prompt: str) -> list[tuple[str, str]]:
    """Route a prompt to one or more (module, sub_prompt) steps via the LLM."""
    if _lmstudio_ref is None:
        raise RuntimeError("LM Studio client not registered. Call set_lmstudio_client() first.")

    prompt_lower = prompt.lower()

    # Pre-check: does the prompt want to play something? If so, check Downloads
    play_info = ""
    from modules.media_index import matches as media_matches, get_count as media_count
    total = media_count()

    # Quick keyword scan for play intent before the LLM call
    for kw in ("play", "watch", "listen to", "put on"):
        idx = prompt_lower.find(kw)
        if idx >= 0:
            # Extract candidate target (rough, just for index lookup)
            raw = prompt_lower[idx + len(kw):].strip()
            # Try progressively shorter prefixes to find a match
            words = raw.split()
            matched_file = None
            for length in range(len(words), 0, -1):
                candidate = " ".join(words[:length])
                if media_matches(candidate):
                    matched_file = candidate
                    break
            if matched_file:
                play_info = f"\nDownloads contains: '{matched_file}'"
            else:
                play_info = f"\nDownloads has {total} media files, but nothing matching this request."
            break

    state_info = ""
    if playback_state["app"]:
        state_info = f"\nCurrently playing: {playback_state['title']} via {playback_state['app']}."

    context = state_info + play_info

    resp = _lmstudio_ref.chat(
        messages=[
            {"role": "system", "content": _ROUTER_SYSTEM + context},
            {"role": "user", "content": prompt},
        ],
        tools=[],
        max_tokens=256,
        temperature=0,
    )
    text = resp["choices"][0]["message"].get("content", "").strip()
    # Strip markdown code fences if present
    text = text.strip().strip('`').lstrip('json').strip()
    steps = json.loads(text)

    if not isinstance(steps, list) or not all("module" in s and "prompt" in s for s in steps):
        raise ValueError(f"Router returned invalid JSON: {text[:200]}")

    result = [(s["module"], s["prompt"]) for s in steps]
    logger.info("Routed: %s", result)
    return result


def get_module(name: str):
    for mod_name, mod in MODULES:
        if mod_name == name:
            return mod
    return system_module


def get_all_tools():
    all_tools = []
    for _, mod in MODULES:
        all_tools.extend(mod.TOOL_DEFS)
    return all_tools
