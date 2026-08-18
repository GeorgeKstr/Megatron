"""
Shared download index — scanned once at startup, used by router.
"""

import glob
from pathlib import Path

_MEDIA_EXT = {".mp3", ".mp4", ".mkv", ".avi", ".flac", ".wav", ".m4a", ".webm", ".ogg", ".mov", ".wmv"}
_DOWNLOADS = Path.home() / "Downloads"
_index: list[str] = []

def build_index():
    """Scan ~/Downloads for media files."""
    global _index
    if not _DOWNLOADS.is_dir():
        return
    for ext in _MEDIA_EXT:
        _index.extend(glob.glob(str(_DOWNLOADS / f"**/*{ext}"), recursive=True))

def matches(query: str) -> bool:
    """Check if query content keywords appear in any Downloads filename."""
    STOP = {
        "play", "the", "and", "or", "in", "on", "at", "to", "for", "of", "my",
        "is", "a", "an", "again", "please", "now", "just", "can", "you",
        "me", "it", "do", "if", "no", "not", "this", "that",
    }
    q = query.lower()
    keywords = [w.strip(".,!?") for w in q.split() if len(w) > 2 and w.lower() not in STOP]
    if not keywords:
        return False
    for path in _index:
        fname = Path(path).name.lower()
        if all(kw in fname for kw in keywords):
            return True
    return False

def get_count() -> int:
    return len(_index)

build_index()
