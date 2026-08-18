"""
Info Module — Google search, weather, images, email.
"""

import json
import re
import time
from urllib.parse import quote, urlencode

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

# ---------- Google / DuckDuckGo Search ----------

def _search_web(query: str, num_results: int = 5) -> list[dict]:
    """Search DuckDuckGo (no API key needed) and return results."""
    if not HAS_REQUESTS:
        return [{"title": "Error", "snippet": "requests library not installed"}]

    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=num_results))
        return [
            {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in results
        ]
    except Exception:
        # Fallback: scrape DuckDuckGo HTML
        try:
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(
                f"https://html.duckduckgo.com/html/?q={quote(query)}",
                headers=headers, timeout=10,
            )
            results = []
            for m in re.finditer(
                r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
                resp.text, re.DOTALL,
            ):
                url = m.group(1)
                title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
                results.append({"title": title, "url": url, "snippet": ""})
            return results[:num_results]
        except Exception as e:
            return [{"title": "Search Error", "url": "", "snippet": str(e)}]


def _search_images(query: str, num_results: int = 4) -> list[dict]:
    """Search for images using DuckDuckGo."""
    if not HAS_REQUESTS:
        return []

    try:
        from duckduckgo_search import DDGS
        with DDGS() as ddgs:
            results = list(ddgs.images(query, max_results=num_results))
        return [
            {"title": r.get("title", ""), "image_url": r.get("image", ""), "source": r.get("source", "")}
            for r in results
        ]
    except Exception:
        return []


def _get_weather(location: str = "") -> str:
    """Get weather from wttr.in (no API key)."""
    if not HAS_REQUESTS:
        return "requests library not installed"

    try:
        loc = quote(location) if location else ""
        resp = requests.get(f"https://wttr.in/{loc}?format=%l:+%c+%t+%w+%h", timeout=10)
        return resp.text.strip()
    except Exception as e:
        return f"Weather fetch failed: {e}"


def _check_email(max_emails: int = 5) -> list[dict]:
    """Check Gmail via IMAP. Requires MEGATRON_GMAIL_USER and MEGATRON_GMAIL_APP_PASSWORD."""
    import os
    import imaplib
    import email as emaillib
    from email.header import decode_header

    gmail_user = os.environ.get("MEGATRON_GMAIL_USER", "")
    gmail_pass = os.environ.get("MEGATRON_GMAIL_APP_PASSWORD", "")

    if not gmail_user or not gmail_pass:
        # Fallback: try local mail
        return _check_local_mail(max_emails)

    emails = []
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(gmail_user, gmail_pass)
        mail.select("INBOX", readonly=True)

        # Search for recent emails
        status, data = mail.search(None, "ALL")
        if status != "OK":
            return [{"subject": "IMAP search failed", "from": "", "date": ""}]

        msg_ids = data[0].split()
        # Get the most recent ones (IMAP returns oldest first)
        recent_ids = msg_ids[-max_emails:][::-1]  # reverse to get newest first

        for mid in recent_ids:
            status, msg_data = mail.fetch(mid, "(RFC822.HEADER)")
            if status != "OK":
                continue

            raw = msg_data[0][1]
            msg = emaillib.message_from_bytes(raw)

            subject = _decode_header_value(msg.get("Subject", ""))
            sender = _decode_header_value(msg.get("From", ""))
            date = _decode_header_value(msg.get("Date", ""))

            emails.append({"subject": subject, "from": sender, "date": date})

        mail.logout()
    except imaplib.IMAP4.error as e:
        error_msg = str(e)
        if "invalid credentials" in error_msg.lower() or "auth" in error_msg.lower():
            emails.append({"subject": "Gmail auth failed", "from": "", "date": "",
                          "error": "Check MEGATRON_GMAIL_USER and MEGATRON_GMAIL_APP_PASSWORD"})
        else:
            emails.append({"subject": f"Gmail error: {error_msg}", "from": "", "date": ""})
    except Exception as e:
        emails.append({"subject": f"Email check failed: {e}", "from": "", "date": ""})

    return emails


def _decode_header_value(value: str) -> str:
    """Decode MIME-encoded email headers."""
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return " ".join(decoded)


def _check_local_mail(max_emails: int = 5) -> list[dict]:
    """Fallback: check local mail spool."""
    import os
    mail_paths = [
        os.path.expanduser("~/Maildir/new"),
        os.path.expanduser("~/.local/share/mail/new"),
        "/var/mail/" + os.environ.get("USER", ""),
    ]

    emails = []
    for path in mail_paths:
        if os.path.isdir(path):
            try:
                for fname in sorted(os.listdir(path))[:max_emails]:
                    fpath = os.path.join(path, fname)
                    with open(fpath, "r", errors="replace") as f:
                        content = f.read(2000)
                    subject = ""
                    sender = ""
                    for line in content.split("\n")[:30]:
                        if line.lower().startswith("subject:"):
                            subject = line[8:].strip()
                        elif line.lower().startswith("from:"):
                            sender = line[5:].strip()
                    emails.append({"subject": subject, "from": sender, "date": ""})
                break
            except PermissionError:
                continue

    if not emails:
        emails.append({
            "subject": "No local mail found — set MEGATRON_GMAIL_USER and MEGATRON_GMAIL_APP_PASSWORD",
            "from": "",
            "date": "",
        })
    return emails


TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for information. Returns top results with titles, URLs, and snippets. "
                "Use this for factual queries, news, or any web-based lookup."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results (default 5)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_images",
            "description": (
                "Search for images on the web. Returns image URLs and titles. "
                "The images are displayed to the user in the UI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Image search query",
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of images (default 4)",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "Get current weather for a location. Use the user's location if not specified. "
                "Returns temperature, conditions, wind, and humidity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "City or location name (optional, uses default if omitted)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_email",
            "description": (
                "Check for new emails from Gmail (IMAP). "
                "Returns subject lines, senders, and dates of recent messages. "
                "Requires Gmail app password configured in environment."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "max_emails": {
                        "type": "integer",
                        "description": "Maximum number of emails to return (default 5)",
                    },
                },
                "required": [],
            },
        },
    },
]


def execute(tool_name: str, args: dict) -> dict:
    """Execute an info tool."""
    if tool_name == "web_search":
        results = _search_web(args.get("query", ""), args.get("num_results", 5))
        if not results:
            return {"ok": False, "message": "No search results found."}
        return {
            "ok": True,
            "message": f"Found {len(results)} results for '{args.get('query', '')}'",
            "results": results,
        }

    elif tool_name == "search_images":
        results = _search_images(args.get("query", ""), args.get("num_results", 4))
        if not results:
            return {"ok": False, "message": "No images found."}
        return {
            "ok": True,
            "message": f"Found {len(results)} images for '{args.get('query', '')}'",
            "_image_results": results,
        }

    elif tool_name == "get_weather":
        weather = _get_weather(args.get("location", ""))
        return {"ok": True, "message": weather}

    elif tool_name == "check_email":
        emails = _check_email(args.get("max_emails", 5))
        return {
            "ok": True,
            "message": f"Found {len(emails)} emails",
            "emails": emails,
        }

    return {"ok": False, "error": f"Unknown info tool: {tool_name}"}


def route_score(prompt: str) -> float:
    """Score how relevant this module is for the given prompt."""
    prompt_lower = prompt.lower()
    score = 0.0

    if any(kw in prompt_lower for kw in [
        "search", "google it", "look up", "find", "query",
        "news", "wiki", "information",
    ]):
        score += 3.0

    if any(kw in prompt_lower for kw in ["what is", "who is"]):
        # Only count if it's NOT a search-like phrase
        if not re.search(r'(?:search|google|look up)', prompt_lower):
            score += 0.5  # weak match, let system handle simple questions

    if any(kw in prompt_lower for kw in [
        "weather", "temperature", "forecast", "rain", "sunny", "cloudy",
    ]):
        score += 4.0

    if any(kw in prompt_lower for kw in [
        "image", "picture", "photo", "show me a picture", "show me an image",
    ]):
        score += 3.5

    if any(kw in prompt_lower for kw in [
        "email", "mail", "inbox", "message", "new mail",
    ]):
        score += 3.0

    return score
