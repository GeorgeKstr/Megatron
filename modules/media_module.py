"""
Media + Router Module — merged. Handles routing and all media playback.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from urllib.parse import quote

from tools.vlc_control import VLCTool
from tools.system_volume import SystemVolumeTool
from modules.media_index import matches as media_in_downloads

logger = logging.getLogger(__name__)

vlc = VLCTool()
volume = SystemVolumeTool()

# ── Playback state ──────────────────────────────────────────────────────
playback_state: dict = {"app": None, "title": ""}

def get_playback_state() -> dict:
    return dict(playback_state)

def set_playback_state(app: str | None, title: str = ""):
    playback_state["app"] = app
    playback_state["title"] = title

# ── Downloads media library ────────────────────────────────────────────
_DOWNLOADS = Path.home() / "Downloads"
_MEDIA_EXT = {".mp3", ".mp4", ".mkv", ".avi", ".flac", ".wav", ".m4a", ".webm", ".ogg", ".mov", ".wmv"}

def _scan_downloads() -> dict:
    """Scan ~/Downloads for media, grouped by category."""
    if not _DOWNLOADS.is_dir():
        return {"error": "~/Downloads not found"}
    videos, audio = [], []
    for root, dirs, files in os.walk(_DOWNLOADS):
        rel = Path(root).relative_to(_DOWNLOADS)
        depth = len([p for p in rel.parts if not p.startswith(".")])
        for f in files:
            ext = os.path.splitext(f)[1].lower()
            if ext not in _MEDIA_EXT:
                continue
            full = str(Path(root) / f)
            if ext in (".mp3", ".flac", ".wav", ".m4a", ".ogg"):
                audio.append({"name": f, "path": full})
            else:
                name_clean = re.sub(r"\[.*?\]", "", f)
                name_clean = re.sub(r"\(.*?\)", "", name_clean)
                name_clean = re.sub(r"\s*1080p.*", "", name_clean, flags=re.I)
                name_clean = re.sub(r"\.(mp4|mkv|avi|webm)$", "", name_clean, flags=re.I).strip()
                if re.search(r"S\d+E\d+", f, re.I):
                    show = name_clean.split(" - ")[0].strip()
                    videos.append({"type": "episode", "name": name_clean, "show": show, "path": full})
                elif depth == 0:
                    videos.append({"type": "clip", "name": name_clean, "path": full})
                else:
                    videos.append({"type": "movie", "name": name_clean, "path": full})
    return {"videos": videos, "audio": audio, "total_video": len(videos), "total_audio": len(audio)}

def _search_youtube(query: str) -> str | None:
    """Search YouTube via browser and extract first video URL."""
    try:
        from modules.browser_module import browser, _run_async, _ensure_browser
        _ensure_browser()
        page = browser._page
        if not page:
            return None
        url = f"https://www.youtube.com/results?search_query={quote(query)}"
        _run_async(page.goto(url, wait_until="domcontentloaded", timeout=15000))
        return _run_async(page.evaluate("""
            () => {
                const link = document.querySelector('a#thumbnail[href*="/watch?"]');
                if (link && link.href) return link.href;
                const fallback = document.querySelector('a[href*="/watch?"]');
                return fallback ? fallback.href : null;
            }
        """))
    except Exception as e:
        logger.warning("YouTube search failed: %s", e)
    return None

# ── Tool definitions ───────────────────────────────────────────────────
TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "play_youtube",
            "description": "Search YouTube for a query and play the first result in VLC.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for on YouTube"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "media_library",
            "description": "Scan ~/Downloads for media files.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_play",
            "description": "Play/resume VLC.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_pause",
            "description": "Pause VLC.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_stop",
            "description": "Stop VLC.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_next",
            "description": "Skip to next track in VLC.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_previous",
            "description": "Go to previous track in VLC.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_status",
            "description": "Get VLC playback status.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_open",
            "description": "Open a file or URL in VLC.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path or URL"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set system volume 0–100.",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                "required": ["level"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "volume_adjust",
            "description": "Adjust volume: 'up', 'down', 'mute', 'unmute', 'get'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["up", "down", "mute", "unmute", "get"]},
                },
                "required": ["action"],
            },
        },
    },
]

def execute(tool_name: str, args: dict) -> dict:
    if tool_name == "play_youtube":
        query = args.get("query", "")
        if not query:
            return {"ok": False, "error": "No search query"}
        yt_url = _search_youtube(query)
        if not yt_url:
            return {"ok": False, "error": f"No YouTube results for '{query}'"}
        logger.info("YouTube URL for '%s': %s", query, yt_url)
        result = vlc.open_file(yt_url)
        if result.get("ok"):
            set_playback_state("vlc", query)
        return result

    elif tool_name == "media_library":
        return {"ok": True, "library": _scan_downloads()}

    elif tool_name == "vlc_play":
        return vlc.play()
    elif tool_name == "vlc_pause":
        return vlc.pause()
    elif tool_name == "vlc_stop":
        return vlc.stop()
    elif tool_name == "vlc_next":
        return vlc.next_track()
    elif tool_name == "vlc_previous":
        return vlc.previous_track()
    elif tool_name == "vlc_status":
        return vlc.status()
    elif tool_name == "vlc_open":
        return vlc.open_file(args.get("path", ""))
    elif tool_name == "set_volume":
        return volume.set_volume(args.get("level", 50))
    elif tool_name == "volume_adjust":
        action = args.get("action", "get")
        if action == "up":
            return volume.set_volume(min(100, volume.get_volume().get("level", 50) + 10))
        elif action == "down":
            return volume.set_volume(max(0, volume.get_volume().get("level", 50) - 10))
        elif action in ("mute", "unmute"):
            return volume.mute()
        elif action == "get":
            return volume.get_volume()
        return {"ok": False, "error": f"Unknown action: {action}"}

    return {"ok": False, "error": f"Unknown tool: {tool_name}"}
