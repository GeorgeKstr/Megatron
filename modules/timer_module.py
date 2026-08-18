"""
Timer Module — set, list, cancel timers with callbacks.
"""

from tools.timer import TimerTool

timer_tool = TimerTool()

TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "timer",
            "description": (
                "Manage timers. Actions: 'set' (create a timer that fires after N seconds), "
                "'list' (show active timers), 'cancel' (cancel a timer by ID). "
                "For 'set', provide 'seconds' and a 'description' of what it's for."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set", "list", "cancel"],
                    },
                    "seconds": {
                        "type": "integer",
                        "description": "Duration in seconds for 'set' action (max 3600)",
                    },
                    "description": {
                        "type": "string",
                        "description": "Human-readable label for the timer",
                    },
                    "timer_id": {
                        "type": "integer",
                        "description": "Timer ID to cancel",
                    },
                },
                "required": ["action"],
            },
        },
    },
]


def execute(tool_name: str, args: dict) -> dict:
    """Execute a timer tool."""
    if tool_name == "timer":
        action = args.get("action", "list")
        if action == "set":
            return timer_tool.set_timer(
                args.get("seconds", 60),
                args.get("description", ""),
                args.get("description", ""),  # action_prompt same as description
            )
        elif action == "list":
            return timer_tool.list_timers()
        elif action == "cancel":
            return timer_tool.cancel_timer(args.get("timer_id", 0))
        return {"ok": False, "error": "Invalid timer action"}

    return {"ok": False, "error": f"Unknown timer tool: {tool_name}"}


def route_score(prompt: str) -> float:
    """Score how relevant this module is for the given prompt."""
    prompt_lower = prompt.lower()
    score = 0.0

    if any(kw in prompt_lower for kw in [
        "timer", "alarm", "remind", "countdown", "in X minutes",
        "in X seconds", "notify me", "wake me",
    ]):
        score += 4.0

    return score
