"""
Input Module — keyboard and mouse control via evdev/uinput.
"""

from tools.input_control import InputTool

controller = InputTool()

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "input_action",
            "description": (
                "Send keyboard or mouse input to the system. "
                "Actions: 'key' (press a key), 'type' (type text), "
                "'mouse_click' (click at x,y), 'mouse_move' (move to x,y), "
                "'scroll' (scroll wheel), 'sequence' (chain multiple steps). "
                "For 'sequence', provide a list of steps like "
                "[{action: 'key', key: 'Super'}, {action: 'type', text: 'hello'}]. "
                "Note: Escape key is disabled."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["key", "type", "mouse_move", "mouse_click", "scroll", "sequence"],
                    },
                    "key": {"type": "string", "description": "Key name for 'key' action (e.g. 'Super_L', 'Return')"},
                    "text": {"type": "string", "description": "Text to type for 'type' action"},
                    "x": {"type": "integer", "description": "X coordinate for mouse actions"},
                    "y": {"type": "integer", "description": "Y coordinate for mouse actions"},
                    "steps": {
                        "type": "array",
                        "description": "List of {action, key, text, x, y} steps for 'sequence'",
                        "items": {"type": "object"},
                    },
                    "clicks": {"type": "integer", "description": "Scroll clicks (positive=down, negative=up)"},
                },
                "required": ["action"],
            },
        },
    },
]


def execute(tool_name: str, args: dict) -> dict:
    """Execute an input tool."""
    if tool_name == "input_action":
        action = args.get("action", "")
        if action == "key":
            return controller.press_key(args.get("key", ""))
        elif action == "type":
            return controller.type_text(args.get("text", ""))
        elif action == "mouse_move":
            return controller.move_to(args.get("x", 0), args.get("y", 0))
        elif action == "mouse_click":
            return controller.click(args.get("x", 0), args.get("y", 0))
        elif action == "scroll":
            return controller.scroll(args.get("clicks", 1))
        elif action == "sequence":
            return controller.sequence(args.get("steps", []))
        return {"ok": False, "error": f"Unknown input action: {action}"}

    return {"ok": False, "error": f"Unknown input tool: {tool_name}"}


def route_score(prompt: str) -> float:
    """Score how relevant this module is for the given prompt."""
    prompt_lower = prompt.lower()
    score = 0.0

    if any(kw in prompt_lower for kw in [
        "type", "keyboard", "key press", "press key",
        "mouse", "click", "move cursor", "scroll",
        "input", "sequence", "chain",
    ]):
        score += 3.0

    return score
