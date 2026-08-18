"""
System Module — screenshots, screen fragments, window management, terminal.
"""

from PIL import Image
from tools.screenshot import ScreenshotTool
from tools.window_control import WindowTool
from tools.terminal import TerminalTool

screenshot = ScreenshotTool()
window_ctrl = WindowTool()
terminal = TerminalTool()

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "stop_playback",
            "description": "Stop any currently playing media (VLC or browser). Use before switching to new content.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": (
                "Capture a full desktop screenshot. The image is shown to the user AND "
                "injected into your conversation so you can see the screen. "
                "Use this when you need visual context. Don't describe unless asked."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "show_screen_fragment",
            "description": (
                "Show a specific area of the screen to the user. "
                "Use action='crop' with explicit x, y, width, height to crop a region you saw in the screenshot. "
                "Use action='find' with a target description to let the vision model locate it. "
                "Region shortcuts for target: 'left', 'right', 'top', 'bottom', 'left half', 'right half', "
                "'left monitor', 'right monitor'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["find", "crop"],
                        "description": "'crop' for explicit coordinates, 'find' to describe what to search for",
                    },
                    "target": {
                        "type": "string",
                        "description": "For 'find': description of what to locate. "
                        "For region shortcuts: 'left', 'right', 'top', 'bottom', 'left half', 'right half', 'left monitor', 'right monitor'",
                    },
                    "x": {"type": "integer", "description": "X coordinate (action='crop')"},
                    "y": {"type": "integer", "description": "Y coordinate (action='crop')"},
                    "width": {"type": "integer", "description": "Width (action='crop')"},
                    "height": {"type": "integer", "description": "Height (action='crop')"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "window_control",
            "description": (
                "List, focus, tile, close, or move windows. "
                "Actions: 'list', 'focus', 'maximize', 'minimize', 'close', "
                "'tile_left', 'tile_right', 'center', 'move_left', 'move_right' "
                "(move between monitors). Use 'title' to match a window by name."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "list", "focus", "maximize", "minimize", "close",
                            "tile_left", "tile_right", "center",
                            "move_left", "move_right",
                        ],
                    },
                    "title": {
                        "type": "string",
                        "description": "Window title substring to match (for focus, close, etc.)",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "description": (
                "Run a safe shell command on the PC. Use for file listing, system info, git operations, "
                "Python/node scripts, or any read-only / informational shell commands. "
                "Forbidden: rm, shutdown, dd, chmod, chown, etc."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                },
                "required": ["command"],
            },
        },
    },
]


def execute(tool_name: str, args: dict) -> dict:
    """Execute a system tool."""
    if tool_name == "take_screenshot":
        img = screenshot.capture()
        if img.width > 1920 or img.height > 1200:
            scale = 1920 / max(img.width, img.height)
            display_img = img.resize(
                (int(img.width * scale), int(img.height * scale)),
                Image.Resampling.LANCZOS,
            )
        else:
            display_img = img
        b64 = screenshot.to_base64(display_img, quality=60)
        return {
            "ok": True,
            "message": f"Screenshot captured ({img.width}x{img.height}).",
            "_image_base64": b64,
        }

    elif tool_name == "show_screen_fragment":
        action = args.get("action", "find")
        if action == "crop":
            return _crop_fragment(args)
        else:
            target = args.get("target", "")
            return _find_fragment(target)

    elif tool_name == "window_control":
        return window_ctrl.execute(args.get("action", "list"), args.get("title", ""))

    elif tool_name == "run_terminal_command":
        cmd = args.get("command", "")
        return terminal.run(cmd)

    return {"ok": False, "error": f"Unknown system tool: {tool_name}"}


def _crop_fragment(args: dict) -> dict:
    """Crop screenshot to explicit coordinates."""
    img = screenshot.capture()
    orig_w, orig_h = img.width, img.height

    x = max(0, min(args.get("x", 0), orig_w - 1))
    y = max(0, min(args.get("y", 0), orig_h - 1))
    w = max(10, min(args.get("width", orig_w), orig_w - x))
    h = max(10, min(args.get("height", orig_h), orig_h - y))

    fragment = screenshot.crop_to_region(img, (x, y, x + w, y + h))
    fragment_b64 = screenshot.to_base64(fragment)

    return {
        "ok": True,
        "message": f"Cropped ({x},{y}) {w}x{h}. Do NOT describe — user sees it.",
        "_fragment_base64": fragment_b64,
        "_fragment_bbox": {"x": x, "y": y, "width": w, "height": h},
        "_fragment_target": f"crop at {x},{y}",
    }


def _find_fragment(target: str) -> dict:
    """Find and show a screen region by description or shortcut."""
    target_lower = target.lower().strip()

    img = screenshot.capture()
    orig_w, orig_h = img.width, img.height

    region_map = {
        "left": (0, 0, orig_w // 2, orig_h),
        "right": (orig_w // 2, 0, orig_w, orig_h),
        "left monitor": (0, 0, orig_w // 2, orig_h),
        "right monitor": (orig_w // 2, 0, orig_w, orig_h),
        "top": (0, 0, orig_w, orig_h // 2),
        "bottom": (0, orig_h // 2, orig_w, orig_h),
        "top half": (0, 0, orig_w, orig_h // 2),
        "bottom half": (0, orig_h // 2, orig_w, orig_h),
        "left half": (0, 0, orig_w // 2, orig_h),
        "right half": (orig_w // 2, 0, orig_w, orig_h),
    }

    if target_lower in region_map:
        x1, y1, x2, y2 = region_map[target_lower]
        fragment = screenshot.crop_to_region(img, (x1, y1, x2, y2))
        fragment_b64 = screenshot.to_base64(fragment)
        return {
            "ok": True,
            "message": f"Showing {target}. Do NOT describe — user sees it.",
            "_fragment_base64": fragment_b64,
            "_fragment_bbox": {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1},
            "_fragment_target": target,
        }

    # Vision-based element search — inject screenshot for model
    b64 = screenshot.to_base64(img, quality=70)
    return {
        "ok": True,
        "message": f"Screenshot provided. Looking for: '{target}'.",
        "_image_base64": b64,
    }


def route_score(prompt: str) -> float:
    """Score how relevant this module is for the given prompt."""
    prompt_lower = prompt.lower()
    score = 0.0

    if any(kw in prompt_lower for kw in ["screenshot", "screen", "what's on", "show me", "display"]):
        score += 3.0

    if any(kw in prompt_lower for kw in ["window", "focus", "switch", "tile", "maximize", "minimize", "close app"]):
        score += 2.5

    if any(kw in prompt_lower for kw in ["terminal", "run command", "shell", "execute", "install", "git"]):
        score += 2.0

    return score


def stop_playback() -> dict:
    """Stop any active playback (VLC or browser)."""
    from modules.router import playback_state
    results = []

    # Stop VLC if playing
    try:
        from modules.media_module import vlc
        status = vlc.status()
        if status.get("ok") and status.get("state") in ("playing", "paused"):
            r = vlc.stop()
            results.append({"module": "vlc", "result": r})
    except Exception:
        pass

    # Stop browser playback (close tab)
    if playback_state.get("app") == "browser":
        try:
            from modules.browser_module import browser, _run_async, _ensure_browser
            _ensure_browser()
            if browser._page:
                _run_async(browser._page.close())
                browser._page = None
            results.append({"module": "browser", "result": {"ok": True, "message": "Stopped browser playback"}})
        except Exception as e:
            results.append({"module": "browser", "result": {"ok": False, "error": str(e)}})

    from modules.router import set_playback_state
    set_playback_state(None, "")

    if not results:
        return {"ok": True, "message": "No active playback to stop"}
    return {"ok": True, "message": "Playback stopped", "details": results}
