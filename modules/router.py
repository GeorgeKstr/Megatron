"""
Router — analyzes user prompts and routes to the best module(s).
Supports multi-module routing for compound prompts like
"play tame impala at 10%" → browser (search) + media (volume).
"""

import re
from modules import system_module, media_module, browser_module, info_module, input_module, timer_module
from modules.media_index import matches as media_in_downloads

# All available modules
MODULES = [
    ("info",     info_module),
    ("media",    media_module),
    ("timer",    timer_module),
    ("browser",  browser_module),
    ("input",    input_module),
    ("system",   system_module),   # fallback — broadest toolset
]

# --- Playback state ---------------------------------------------------
# Tracks what media is currently playing so we can stop it when switching
playback_state: dict = {
    "app": None,       # "vlc" | "browser" | None
    "title": "",
}

def get_playback_state() -> dict:
    return dict(playback_state)

def set_playback_state(app: str | None, title: str = ""):
    playback_state["app"] = app
    playback_state["title"] = title

# --- Keyword-based routing --------------------------------------------

# Words that signal "stop current playback"
_STOP_KEYWORDS = {"stop", "pause", "halt"}

# Words that signal a new "play" request
_PLAY_KEYWORDS = {"play", "watch", "listen to", "put on", "start playing"}

def _extract_play_target(prompt: str) -> str | None:
    """Extract what the user wants to play from a prompt."""
    p = prompt.lower()
    for kw in _PLAY_KEYWORDS:
        idx = p.find(kw)
        if idx >= 0:
            target = p[idx + len(kw):].strip()
            # Strip trailing volume specs: "at 10%", "at 50 percent"
            target = re.sub(r'\s+at\s+\d+\s*%?s?', '', target)
            # Strip platform specs: "on youtube", "on vlc", "from downloads"
            target = re.sub(r'\s+on\s+(youtube|yt|vlc|netflix|spotify)', '', target)
            target = re.sub(r'\s+from\s+(downloads|my files|my downloads)', '', target)
            target = target.strip()
            if target:
                return target
    return None


def route(prompt: str) -> list[tuple[str, str]]:
    """
    Route a prompt to one or more modules.

    Returns: list of (module_name, sub_prompt) tuples.
    Examples:
        "take a screenshot" → [("system", "take a screenshot")]
        "play tame impala at 10%" → [("media", "stop playback"), ("browser", "search youtube: tame impala"), ("media", "set volume 10%")]
        "play creature commandos" → [("media", "play creature commandos")]
    """
    prompt_lower = prompt.lower()

    # ---- Multi-step: "play X at Y%" ----
    play_target = _extract_play_target(prompt)

    # Check for volume override in play prompt
    vol_match = re.search(r'at\s+(\d+)\s*%', prompt_lower)
    target_volume = int(vol_match.group(1)) if vol_match else None

    # Check for platform override
    platform_override = None
    if "on youtube" in prompt_lower or "on yt" in prompt_lower:
        platform_override = "browser"
    elif "on vlc" in prompt_lower or "from downloads" in prompt_lower or "from my files" in prompt_lower:
        platform_override = "media"

    if play_target:
        steps = []

        # Step 1: Stop current playback if it's a different content
        current_app = playback_state.get("app")
        current_title = playback_state.get("title", "")
        if current_app and current_title.lower() not in play_target.lower() and play_target.lower() not in current_title.lower():
            steps.append(("media", "stop playback"))

        # Step 2: Decide where to play from
        if platform_override == "browser":
            steps.append(("browser", f"search youtube for: {play_target}"))
            set_playback_state("browser", play_target)
        elif platform_override == "media":
            steps.append(("media", f"play {play_target}"))
            set_playback_state("vlc", play_target)
        else:
            # Auto-decide: in Downloads → VLC, otherwise → YouTube
            if media_in_downloads(play_target):
                steps.append(("media", f"play {play_target}"))
                set_playback_state("vlc", play_target)
            else:
                steps.append(("browser", f"search youtube for: {play_target}"))
                set_playback_state("browser", play_target)

        # Step 3: Set volume if specified
        if target_volume is not None:
            steps.append(("media", f"set volume to {target_volume}%"))

        return steps if steps else [("system", prompt)]

    # ---- Single-module routing for non-play prompts ----
    scores = []
    for name, mod in MODULES:
        score = mod.route_score(prompt)
        scores.append((name, score))

    scores.sort(key=lambda x: x[1], reverse=True)

    # If top score is low, default to system module
    if scores[0][1] < 1.0:
        return [("system", prompt)]

    return [(scores[0][0], prompt)]


def get_module(name: str):
    """Get a module by name."""
    for mod_name, mod in MODULES:
        if mod_name == name:
            return mod
    return system_module


def get_all_tools():
    """Get combined tool definitions for all modules (fallback)."""
    all_tools = []
    for _, mod in MODULES:
        all_tools.extend(mod.TOOL_DEFS)
    return all_tools
