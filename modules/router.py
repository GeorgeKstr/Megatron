"""
Router — LLM-assisted prompt routing with keyword fallback.

Sends the prompt to the model with a minimal classification prompt that returns
JSON routing steps. Falls back to keyword matching if LM Studio is unreachable.
"""

import json
import re
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
- "play X" where X is in the user's Downloads → media module, sub-prompt: "play X"
- "play X" where X is NOT in Downloads → browser module, sub-prompt: "search youtube for: X"
- "play X at N%" → first stop current playback, then play, then set volume
- "play X from downloads" → media module even if X not in index
- "play X on youtube" → browser module
- Volume commands like "set volume to N%", "mute", "volume up" → media
- If nothing matches, use system module.
- Return ONLY a JSON array of {{"module": "...", "prompt": "..."}} objects.
- No explanation, no markdown, just JSON.
"""

def _llm_route(user_prompt: str, lmstudio) -> list[tuple[str, str]] | None:
    """Try LLM-assisted routing. Returns None if it fails."""
    try:
        # Enrich prompt with playback state info
        state_info = ""
        if playback_state["app"]:
            state_info = f"\nCurrently playing: {playback_state['title']} via {playback_state['app']}."

        resp = lmstudio.chat(
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM + state_info},
                {"role": "user", "content": user_prompt},
            ],
            tools=[],
            max_tokens=256,
            temperature=0,
        )
        text = resp["choices"][0]["message"].get("content", "").strip()
        # Strip markdown code fences if present
        text = re.sub(r'^```(?:json)?\s*', '', text)
        text = re.sub(r'\s*```$', '', text)
        steps = json.loads(text)
        if isinstance(steps, list) and all("module" in s and "prompt" in s for s in steps):
            return [(s["module"], s["prompt"]) for s in steps]
    except Exception as e:
        logger.debug("LLM routing failed, falling back to keywords: %s", e)
    return None


# ── Keyword fallback ───────────────────────────────────────────────────
_STOP_KEYWORDS = {"stop", "pause", "halt"}
_PLAY_KEYWORDS = {"play", "watch", "listen to", "put on", "start playing"}

def _extract_play_target(prompt: str) -> str | None:
    p = prompt.lower()
    for kw in _PLAY_KEYWORDS:
        idx = p.find(kw)
        if idx >= 0:
            target = p[idx + len(kw):].strip()
            target = re.sub(r'\s+at\s+\d+\s*%?', '', target)
            target = re.sub(r'\s+at\s+\d+:\d+', '', target)
            target = re.sub(r'\s+(system|master)\s+volume', '', target)
            target = re.sub(r'\s+from\s+.*$', '', target)
            target = re.sub(r'\s+on\s+(youtube|yt|vlc|netflix|spotify)', '', target)
            target = re.sub(r'\s+the\s+(song|track|video|music).*$', '', target)
            target = re.sub(r'\s+gets?\s+(loud|quiet).*$', '', target)
            target = target.strip()
            return target if target else None
    return None


def _keyword_route(prompt: str) -> list[tuple[str, str]]:
    prompt_lower = prompt.lower()
    play_target = _extract_play_target(prompt)
    vol_match = re.search(r'at\s+(\d+)\s*%', prompt_lower)
    target_volume = int(vol_match.group(1)) if vol_match else None

    platform_override = None
    if "on youtube" in prompt_lower or "on yt" in prompt_lower:
        platform_override = "browser"
    elif "on vlc" in prompt_lower or "from downloads" in prompt_lower:
        platform_override = "media"

    if play_target:
        steps = []
        current_app = playback_state.get("app")
        current_title = playback_state.get("title", "")
        if current_app and current_title.lower() not in play_target.lower() and play_target.lower() not in current_title.lower():
            steps.append(("media", "stop playback"))

        if platform_override == "browser":
            steps.append(("browser", f"search youtube for: {play_target}"))
            set_playback_state("browser", play_target)
        elif platform_override == "media":
            steps.append(("media", f"play {play_target}"))
            set_playback_state("vlc", play_target)
        elif media_in_downloads(play_target):
            steps.append(("media", f"play {play_target}"))
            set_playback_state("vlc", play_target)
        else:
            steps.append(("browser", f"search youtube for: {play_target}"))
            set_playback_state("browser", play_target)

        if target_volume is not None:
            steps.append(("media", f"set volume to {target_volume}%"))

        return steps if steps else [("system", prompt)]

    scores = []
    for name, mod in MODULES:
        scores.append((name, mod.route_score(prompt)))
    scores.sort(key=lambda x: x[1], reverse=True)
    if scores[0][1] < 1.0:
        return [("system", prompt)]
    return [(scores[0][0], prompt)]


# ── Public API ─────────────────────────────────────────────────────────

_lmstudio_ref = None

def set_lmstudio_client(client):
    """Set the LM Studio client for LLM-assisted routing."""
    global _lmstudio_ref
    _lmstudio_ref = client


def route(prompt: str) -> list[tuple[str, str]]:
    """Route a prompt to one or more (module, sub_prompt) steps."""
    # Try LLM first
    if _lmstudio_ref is not None:
        result = _llm_route(prompt, _lmstudio_ref)
        if result:
            logger.info("LLM routed: %s", result)
            return result

    # Fallback to keywords
    result = _keyword_route(prompt)
    logger.info("Keyword routed: %s", result)
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
