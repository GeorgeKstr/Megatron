"""
Media Module — VLC control, system volume, media library.
"""

import os
import re
import glob
from pathlib import Path
from tools.vlc_control import VLCTool
from tools.system_volume import SystemVolumeTool
from modules.media_index import matches as media_matches

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


def _scan_downloads() -> dict:
    """Scan ~/Downloads for media, grouped by category."""
    if not _DOWNLOADS.is_dir():
        return {"error": "~/Downloads not found"}

    videos = []   # movies / shows / standalone clips
    audio = []    # music / podcasts

    for root, dirs, files in os.walk(_DOWNLOADS):
        # Skip huge dirs that aren't media
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
                # Classify: series episode vs movie vs standalone clip
                name_clean = re.sub(r"\[.*?\]", "", f)  # strip tags
                name_clean = re.sub(r"\(.*?\)", "", name_clean)  # strip parens
                name_clean = re.sub(r"\s*1080p.*", "", name_clean, flags=re.I)  # strip quality
                name_clean = re.sub(r"\.(mp4|mkv|avi|webm)$", "", name_clean, flags=re.I).strip()

                # Detect S##E## pattern → TV series
                if re.search(r"S\d+E\d+", f, re.I):
                    show = name_clean.split(" - ")[0].strip()
                    videos.append({"type": "episode", "name": name_clean, "show": show, "path": full})
                elif depth == 0:
                    videos.append({"type": "clip", "name": name_clean, "path": full})
                else:
                    videos.append({"type": "movie", "name": name_clean, "path": full})

    return {"videos": videos, "audio": audio, "total_video": len(videos), "total_audio": len(audio)}

TOOL_DEFS = [
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
    if tool_name == "media_library":
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
            return volume.mute()  # toggle
        elif action == "get":
            return volume.get_volume()
        return {"ok": False, "error": f"Unknown volume action: {action}"}
    return {"ok": False, "error": f"Unknown media tool: {tool_name}"}


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
        # If the query matches a file in Downloads, definitely route here
        if media_matches(prompt_lower):
            score += 5.0
        else:
            score += 1.5  # lower — might be a YouTube query instead

    return score
