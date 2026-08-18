"""
Router — LLM-assisted prompt routing.
"""

import json
import logging
from modules import system_module, browser_module, info_module, input_module, timer_module
from modules import media_module
from modules.media_index import matches as media_matches, get_count as media_count

logger = logging.getLogger(__name__)

MODULES = [
    ("media",    media_module),
    ("info",     info_module),
    ("timer",    timer_module),
    ("browser",  browser_module),
    ("input",    input_module),
    ("system",   system_module),
]

_TOOL_CATALOG = """
system: take_screenshot, show_screen_fragment, window_control, run_terminal_command
media: vlc_play, vlc_pause, vlc_stop, vlc_next, vlc_previous, vlc_status, vlc_open, vlc_enqueue, set_volume, volume_adjust
browser: browser_navigate, browser_get_content, browser_get_links, browser_get_forms, browser_click, browser_fill, browser_page_info, browser_evaluate
info: web_search, search_images, get_weather, check_email
input: input_action
timer: timer
"""

_ROUTER_SYSTEM = f"""You are a router. Classify the user's request into sequential steps.
Each step picks ONE module and a natural-language sub-prompt.

{_TOOL_CATALOG}
Rules:
- "play X" → if X is in Downloads, route to media with "play X". Otherwise route to browser with "Search YouTube for X and give me the first video URL".
- If the user wants to play something from YouTube, first use browser to get the URL, then use media's vlc_open with that URL.
- Volume commands → media
- If nothing matches, use system.
- Return ONLY JSON: [{{"module":"...","prompt":"..."}}]
"""

_lmstudio_ref = None

def set_lmstudio_client(client):
    global _lmstudio_ref
    _lmstudio_ref = client


def route(prompt: str) -> list[tuple[str, str]]:
    if _lmstudio_ref is None:
        raise RuntimeError("LM Studio client not registered.")

    ctx = ""
    pl = prompt.lower()
    for kw in ("play", "watch", "listen to", "put on"):
        idx = pl.find(kw)
        if idx >= 0:
            raw = pl[idx + len(kw):].strip()
            words = raw.split()
            matched = None
            for length in range(len(words), 0, -1):
                candidate = " ".join(words[:length])
                if media_matches(candidate):
                    matched = candidate
                    break
            if matched:
                ctx += f"\nDownloads contains: '{matched}'"
            else:
                ctx += f"\nDownloads has {media_count()} files, nothing matching."
            break

    ps = media_module.playback_state
    if ps["app"]:
        ctx += f"\nCurrently playing: {ps['title']} via {ps['app']}."

    resp = _lmstudio_ref.chat(
        messages=[
            {"role": "system", "content": _ROUTER_SYSTEM + ctx},
            {"role": "user", "content": prompt},
        ],
        tools=[],
        max_tokens=256,
        temperature=0,
    )
    text = resp["choices"][0]["message"].get("content", "").strip()
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
