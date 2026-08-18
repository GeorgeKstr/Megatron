"""
Media Module — VLC control, system volume, media library.
"""

import os
import re
import glob
import time
import logging
from pathlib import Path
from urllib.parse import quote

from tools.vlc_control import VLCTool
from tools.system_volume import SystemVolumeTool
from modules.media_index import matches as media_matches

logger = logging.getLogger(__name__)

vlc = VLCTool()
volume = SystemVolumeTool()

_DOWNLOADS = Path.home() / "Downloads"
_MEDIA_EXT = {".mp3", ".mp4", ".mkv", ".avi", ".flac", ".wav", ".m4a", ".webm", ".ogg", ".mov", ".wmv"}

# Built once at import
_local_media_index: list[str] = []
def _build_index():
    global _local_media_index
    if not _DOWNLOADS.is_dir():
        return
    for ext in _MEDIA_EXT:
        _local_media_index.extend(glob.glob(str(_DOWNLOADS / f"**/*{ext}"), recursive=True))
_build_index()


def _query_matches_downloads(query: str) -> bool:
    """Check if query keywords match any file in ~/Downloads."""
    return media_matches(query)


def _search_youtube(query: str) -> str | None:
    """Search YouTube and return the first video URL (no browser needed)."""
    try:
        import requests
        from bs4 import BeautifulSoup
        headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        resp = requests.get(
            f"https://www.youtube.com/results?search_query={quote(query)}",
            headers=headers, timeout=10,
        )
        # YouTube stores initial data in a JS variable
        match = re.search(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
        if match:
            return f"https://www.youtube.com/watch?v={match.group(1)}"
        # Fallback: parse HTML links
        soup = BeautifulSoup(resp.text, "html.parser")
        link = soup.find("a", href=lambda h: h and "/watch?v=" in h)
        if link:
            href = link["href"]
            return f"https://www.youtube.com{href}" if href.startswith("/") else href
    except Exception as e:
        logger.warning("YouTube search failed: %s", e)
    return None


TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "play_youtube",
            "description": (
                "Search YouTube for a query and play the first result in VLC. "
                "VLC plays YouTube streams natively. Use this when the user says 'play X' "
                "and the content is NOT in their Downloads."
            ),
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
            "description": (
                "Scan ~/Downloads for media files (video/audio). "
                "Returns organized lists of what's available to play. "
                "Use this when the user says 'what can I watch', 'show me my movies', etc."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_play",
            "description": "Play VLC media.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_pause",
            "description": "Pause VLC media.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_stop",
            "description": "Stop VLC media.",
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
            "description": "Get the current VLC playback status (playing, paused, current track).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_open",
            "description": "Open a file or URL in VLC. If VLC isn't running, this launches it.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path or URL to open in VLC"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "vlc_enqueue",
            "description": "Add a file to the VLC queue without interrupting current playback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to queue"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set the system volume to a specific level (0–100).",
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
            "description": "Adjust system volume. Actions: 'up', 'down', 'mute', 'unmute', 'get'.",
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
    """Execute a media tool."""
    if tool_name == "play_youtube":
        query = args.get("query", "")
        if not query:
            return {"ok": False, "error": "No search query provided"}

        yt_url = _search_youtube(query)
        if not yt_url:
            return {"ok": False, "error": f"No YouTube results for '{query}'"}

        logger.info("YouTube URL for '%s': %s", query, yt_url)

        # Open in VLC — launches VLC if needed, stops current playback
        result = vlc.open_file(yt_url)
        if result.get("ok"):
            from modules.router import set_playback_state
            set_playback_state("vlc", query)
        return result

    elif tool_name == "media_library":
        return {"ok": True, "library": _scan_downloads()}

    elif tool_name == "stop_playback":
        from modules.system_module import stop_playback
        return stop_playback()

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
    elif tool_name == "vlc_enqueue":
        return vlc.enqueue(args.get("path", ""))
    elif tool_name == "set_volume":
        return volume.set_volume(args.get("level", 50))
    elif tool_name == "volume_adjust":
        action = args.get("action", "get")
        if action == "up":
            return volume.set_volume(min(100, volume.get_volume().get("level", 50) + 10))
        elif action == "down":
            return volume.set_volume(max(0, volume.get_volume().get("level", 50) - 10))
        elif action == "mute":
            return volume.mute()
        elif action == "unmute":
            return volume.mute()
        elif action == "get":
            return volume.get_volume()
        return {"ok": False, "error": f"Unknown volume action: {action}"}

    return {"ok": False, "error": f"Unknown media tool: {tool_name}"}


def _scan_downloads() -> dict:
    """Scan ~/Downloads for media, grouped by category."""
    if not _DOWNLOADS.is_dir():
        return {"error": "~/Downloads not found"}

    videos = []
    audio = []

    for root, dirs, files in os.walk(_DOWNLOADS):
        rel = Path(root).relative_to(_DOWNLOADS)
        parts = [p for p in rel.parts if not p.startswith(".")]
        depth = len(parts)

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


def route_score(prompt: str) -> float:
    """Score how relevant this module is for the given prompt."""
    prompt_lower = prompt.lower()
    score = 0.0

    if any(kw in prompt_lower for kw in [
        "volume", "vol", "loud", "quiet", "mute", "unmute",
        "sound", "speaker", "audio level",
    ]):
        score += 3.0

    if any(kw in prompt_lower for kw in [
        "vlc", "play", "pause", "media", "music", "song",
        "track", "playlist", "player", "next track", "previous",
        "video", "watch", "movie", "episode", "show",
        "what can i watch", "what do i have", "my movies",
        "my shows", "my downloads", "media library",
    ]):
        if media_matches(prompt_lower):
            score += 5.0
        else:
            score += 1.5

    return score
