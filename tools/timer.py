"""
Timer / scheduler tool — lets the LLM schedule future actions.

Supports:
- "Let me know when my download is finished" → poll every N seconds
- "Play X in 5 minutes" → delayed action
- "Check again in 30 seconds" → re-check pattern
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# Callback type: (description, action_prompt) -> None
TimerCallback = Callable[[str, str], None]


class TimerTool:
    """
    Simple in-process timer scheduler.

    The LLM calls set_timer(seconds, description, action_prompt).
    When the timer fires, the callback is invoked with the description
    and action_prompt so the server can re-run the agent loop.
    """

    def __init__(self):
        self._timers: list[dict] = []
        self._lock = threading.Lock()
        self._callback: Optional[TimerCallback] = None
        self._running = True

    def set_callback(self, cb: TimerCallback):
        """Register the function to call when a timer fires."""
        self._callback = cb

    def set_timer(self, seconds: int, description: str, action_prompt: str) -> dict:
        """
        Schedule an action after *seconds* seconds.

        Parameters
        ----------
        seconds : int
            Delay in seconds. Capped at 3600 (1 hour) for safety.
        description : str
            Human-readable reason, e.g. "Check if download finished".
        action_prompt : str
            The prompt to execute when the timer fires, e.g.
            "Check if the download is finished and tell the user."
        """
        seconds = max(1, min(3600, int(seconds)))
        fire_at = time.time() + seconds

        timer = {
            "id": len(self._timers) + 1,
            "seconds": seconds,
            "description": description,
            "action_prompt": action_prompt,
            "fire_at": fire_at,
            "fire_at_iso": datetime.fromtimestamp(fire_at).isoformat(),
            "created_at_iso": datetime.now().isoformat(),
        }

        with self._lock:
            self._timers.append(timer)

        # Start background thread to wait
        t = threading.Thread(
            target=self._wait_and_fire,
            args=(timer,),
            daemon=True,
            name=f"timer-{timer['id']}",
        )
        t.start()

        logger.info(
            "Timer #%d set: '%s' in %ds → %s",
            timer["id"], description, seconds, timer["fire_at_iso"],
        )

        return {
            "ok": True,
            "message": f"Timer set: '{description}' will fire in {seconds}s (at {timer['fire_at_iso']}).",
            "timer_id": timer["id"],
            "fire_at": timer["fire_at_iso"],
        }

    def list_timers(self) -> dict:
        """Return all pending timers."""
        with self._lock:
            now = time.time()
            pending = [
                {
                    "id": t["id"],
                    "description": t["description"],
                    "seconds_remaining": round(max(0, t["fire_at"] - now)),
                    "fire_at": t["fire_at_iso"],
                }
                for t in self._timers
                if t["fire_at"] > now
            ]
        return {"ok": True, "pending": pending, "count": len(pending)}

    def cancel_timer(self, timer_id: int) -> dict:
        """Cancel a pending timer by ID."""
        with self._lock:
            for t in self._timers:
                if t["id"] == timer_id:
                    t["fire_at"] = 0  # mark as cancelled
                    return {"ok": True, "message": f"Timer #{timer_id} cancelled."}
        return {"ok": False, "error": f"Timer #{timer_id} not found."}

    def stop(self):
        self._running = False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------
    def _wait_and_fire(self, timer: dict):
        delay = max(0, timer["fire_at"] - time.time())
        time.sleep(delay + 0.1)  # tiny buffer to ensure time has passed

        # Check if still valid (not cancelled)
        if timer["fire_at"] == 0:
            return

        logger.info("Timer #%d firing: %s", timer["id"], timer["description"])

        if self._callback:
            try:
                self._callback(timer["description"], timer["action_prompt"])
            except Exception as e:
                logger.error("Timer callback failed: %s", e)
