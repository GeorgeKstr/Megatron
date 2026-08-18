"""
Terminal tool — safely executes shell commands with a whitelist/blocklist
and returns stdout/stderr.
"""
from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Commands that are NEVER allowed (even with arguments)
_FORBIDDEN_COMMANDS = {
    "rm", "shutdown", "reboot", "poweroff", "halt",
    "mkfs", "dd", "fdisk", "parted", "wipefs",
    "chmod", "chown",
}

# Commands allowed without restriction (read-only or informational)
_SAFE_COMMANDS = {
    "ls", "dir", "pwd", "echo", "cat", "head", "tail",
    "wc", "sort", "uniq", "grep", "find", "which",
    "date", "cal", "uptime", "whoami", "hostname",
    "uname", "ps", "top", "htop", "df", "du", "free",
    "ip", "ifconfig", "ping", "netstat", "ss",
    "python", "python3", "node", "npm", "pip", "pip3",
    "git", "docker", "kubectl",
    "mkdir", "touch", "cp", "mv",
    "pkill", "killall", "kill", "pgrep", "pidof",
    "pactl", "pamixer", "amixer", "wpctl",
    "vlc", "cvlc", "nvlc",
}

# Dangerous flags that are stripped even for safe commands.
# Only strip exact matches or flags that match exactly after a space/start.
_DANGEROUS_FLAGS = {
    "--rm", "--force", "--no-preserve-root",
    "-rf", "-r", "-f",
}

_MAX_OUTPUT_BYTES = 50_000  # 50 KB
_MAX_RUNTIME = 30  # seconds
_WORK_DIR = Path.home()


class TerminalTool:
    """Sandboxed command executor."""

    def __init__(self, allowed_dir: Path | None = None):
        self._cwd = allowed_dir or _WORK_DIR

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self, command: str, timeout: int = _MAX_RUNTIME) -> dict:
        """
        Execute *command* safely.
        Returns {"ok": True, "stdout": …, "stderr": …, "cwd": …} on success,
        or {"ok": False, "error": …} on failure.
        """
        if not command.strip():
            return {"ok": False, "error": "Empty command"}

        # Parse
        try:
            parts = shlex.split(command.strip())
        except ValueError as e:
            return {"ok": False, "error": f"Could not parse command: {e}"}

        if not parts:
            return {"ok": False, "error": "Empty command"}

        executable = os.path.basename(parts[0])
        args = parts[1:]

        # --- Safety checks ---
        if executable in _FORBIDDEN_COMMANDS:
            return {"ok": False, "error": f"Command '{executable}' is forbidden for safety."}

        if executable not in _SAFE_COMMANDS:
            return {
                "ok": False,
                "error": f"Command '{executable}' is not in the allowed list. "
                         f"Allowed: {', '.join(sorted(_SAFE_COMMANDS))}",
            }

        # Strip dangerous flags (exact match only — no substring matching)
        cleaned_args = []
        for a in args:
            if a in _DANGEROUS_FLAGS:
                continue
            # Only block single-letter flags that exactly match dangerous ones
            if a.startswith("-") and not a.startswith("--"):
                # e.g. "-rf" or "-r" — strip if exactly dangerous
                flags = a[1:]  # everything after the leading -
                if flags in _DANGEROUS_FLAGS or a in _DANGEROUS_FLAGS:
                    continue
            cleaned_args.append(a)

        final_cmd = [parts[0]] + cleaned_args
        cmd_str = " ".join(final_cmd)

        # Detect shell operators — if present, run with shell=True
        _SHELL_OPS = {"&&", "||", "|", ";", ">", ">>", "<", "&"}
        has_shell_ops = any(op in parts for op in _SHELL_OPS)

        logger.info("Executing: %s (shell=%s)", cmd_str, has_shell_ops)

        start = time.monotonic()
        try:
            proc = subprocess.run(
                cmd_str if has_shell_ops else final_cmd,
                cwd=str(self._cwd),
                capture_output=True,
                timeout=timeout,
                text=True,
                shell=has_shell_ops,
                env={**os.environ, "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Command timed out after {timeout}s"}
        except FileNotFoundError:
            return {"ok": False, "error": f"Command not found: {executable}"}
        except PermissionError:
            return {"ok": False, "error": f"Permission denied: {executable}"}

        elapsed = time.monotonic() - start

        # Truncate output
        stdout = proc.stdout[-_MAX_OUTPUT_BYTES:] if proc.stdout else ""
        stderr = proc.stderr[-_MAX_OUTPUT_BYTES:] if proc.stderr else ""

        return {
            "ok": True,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": proc.returncode,
            "elapsed": round(elapsed, 2),
            "cwd": str(self._cwd),
        }

    @property
    def safe_commands(self) -> list[str]:
        return sorted(_SAFE_COMMANDS)

    @property
    def forbidden_commands(self) -> list[str]:
        return sorted(_FORBIDDEN_COMMANDS)
