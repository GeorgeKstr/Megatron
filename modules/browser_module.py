"""
Browser Module — Brave CDP navigation, DOM inspection, and page control.
Uses a persistent asyncio event loop so Playwright objects stay valid across calls.
"""

import asyncio
import threading
from tools.browser import BrowserTool

browser = BrowserTool()
_browser_started = False
_loop: asyncio.AbstractEventLoop | None = None
_loop_thread: threading.Thread | None = None


def _start_loop():
    """Start a persistent event loop in a background thread."""
    global _loop, _loop_thread
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


def _get_loop() -> asyncio.AbstractEventLoop:
    """Get or create the persistent event loop."""
    global _loop, _loop_thread
    if _loop is None or not _loop.is_running():
        _loop_thread = threading.Thread(target=_start_loop, daemon=True)
        _loop_thread.start()
        # Wait for loop to be ready
        import time
        while _loop is None or not _loop.is_running():
            time.sleep(0.05)
    return _loop


def _run_async(coro):
    """Run a coroutine on the persistent event loop (thread-safe)."""
    loop = _get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=35)  # 35s timeout


def _ensure_browser():
    """Ensure browser is connected on the persistent loop."""
    global _browser_started
    if not _browser_started:
        _run_async(browser.start())
        _browser_started = True
    else:
        try:
            _run_async(browser._ensure_page())
        except Exception:
            _run_async(browser.start())


async def _get_dom_text(page, selector: str | None = None, max_chars: int = 8000) -> str:
    """Extract visible text content from the page or a specific element."""
    if selector:
        el = await page.query_selector(selector)
        if not el:
            return f"No element found for selector: {selector}"
        text = await el.inner_text()
    else:
        text = await page.inner_text("body")
    return text[:max_chars] + ("..." if len(text) > max_chars else "")


async def _get_html(page, selector: str | None = None, max_chars: int = 10000) -> str:
    """Get HTML content from the page or a specific element."""
    if selector:
        el = await page.query_selector(selector)
        if not el:
            return f"No element found for selector: {selector}"
        html = await el.inner_html()
    else:
        html = await page.content()
    return html[:max_chars] + ("..." if len(html) > max_chars else "")


async def _get_links(page, max_links: int = 50) -> list[dict]:
    """Extract all clickable links from the page."""
    links = await page.evaluate("""
        () => {
            const result = [];
            for (const a of document.querySelectorAll('a[href]')) {
                const href = a.href;
                if (href && !href.startsWith('javascript:') && !href.startsWith('#')) {
                    result.push({
                        text: (a.innerText || '').trim().substring(0, 100),
                        href: href,
                    });
                }
            }
            return result;
        }
    """)
    return links[:max_links]


async def _get_form_fields(page) -> list[dict]:
    """Extract all form input fields with their labels and types."""
    fields = await page.evaluate("""
        () => {
            const result = [];
            for (const input of document.querySelectorAll('input, select, textarea')) {
                const label = input.labels?.[0]?.innerText || input.placeholder || input.name || '';
                result.push({
                    type: input.type || input.tagName.toLowerCase(),
                    name: input.name || '',
                    id: input.id || '',
                    label: label.substring(0, 80),
                    value: input.value || '',
                    placeholder: input.placeholder || '',
                });
            }
            return result;
        }
    """)
    return fields


async def _get_interactive_elements(page, max_elements: int = 50) -> list[dict]:
    """Get all interactive elements (buttons, links, inputs) with their text."""
    elements = await page.evaluate("""
        () => {
            const result = [];
            const selectors = 'a, button, [role="button"], input, select, textarea, [onclick]';
            const els = document.querySelectorAll(selectors);
            for (const el of els) {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    result.push({
                        tag: el.tagName.toLowerCase(),
                        text: (el.innerText || el.value || '').trim().substring(0, 80),
                        role: el.getAttribute('role') || '',
                        href: el.href || '',
                        type: el.type || '',
                        visible: true,
                    });
                }
            }
            return result;
        }
    """)
    return elements[:max_elements]


async def _click_by_text(page, text: str) -> dict:
    """Click an element by its visible text content."""
    # Try exact match first
    elements = await page.evaluate(f"""
        () => {{
            const result = [];
            for (const el of document.querySelectorAll('a, button, [role="button"], span, div')) {{
                if (el.innerText && el.innerText.trim() === `{text}`) {{
                    const rect = el.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {{
                        result.push({{tag: el.tagName, text: el.innerText.trim()}});
                    }}
                }}
            }}
            return result;
        }}
    """)
    if elements:
        # Click the first matching element
        await page.click(f"text={text}")
        await page.wait_for_load_state("networkidle", timeout=5000)
        return {"ok": True, "message": f"Clicked element with text: '{text}'"}

    # Try partial match
    try:
        await page.click(f"text={text}")
        await page.wait_for_load_state("networkidle", timeout=5000)
        return {"ok": True, "message": f"Clicked element containing: '{text}'"}
    except Exception as e:
        return {"ok": False, "error": f"Could not find element with text: '{text}' ({e})"}


async def _fill_by_label(page, label: str, value: str) -> dict:
    """Fill a form field by its label, placeholder, or name."""
    # Try to find the input by label text
    input_el = await page.query_selector(f"input[placeholder*='{label}'], input[name='{label}'], textarea[placeholder*='{label}']")
    if input_el:
        await input_el.fill(value)
        return {"ok": True, "message": f"Filled '{label}' with '{value}'"}

    # Try label association
    label_el = await page.query_selector(f"label:has-text('{label}')")
    if label_el:
        for_attr = await label_el.get_attribute("for")
        if for_attr:
            input_el = await page.query_selector(f"#{for_attr}")
            if input_el:
                await input_el.fill(value)
                return {"ok": True, "message": f"Filled '{label}' with '{value}'"}

    # Try any input containing the label text nearby
    try:
        await page.fill(f"input, textarea", value)
        return {"ok": True, "message": f"Filled first input with '{value}'"}
    except Exception as e:
        return {"ok": False, "error": f"Could not find field for '{label}': {e}"}


async def _get_page_info(page) -> dict:
    """Get comprehensive page information (title, URL, text summary)."""
    title = await page.title()
    url = page.url
    text = await _get_dom_text(page, max_chars=4000)
    links = await _get_links(page, max_links=20)
    return {
        "title": title,
        "url": url,
        "text_summary": text,
        "links": links,
    }


async def _get_page_screenshot_base64(page) -> str:
    """Take a page screenshot and return as base64 (for vision fallback)."""
    from tools.screenshot import ScreenshotTool
    import io
    from PIL import Image

    buf = await page.screenshot(full_page=False)
    img = Image.open(io.BytesIO(buf))
    return ScreenshotTool().to_base64(img, quality=70)


TOOL_DEFS = [
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Navigate to a URL in Brave.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to (include https://)"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_content",
            "description": (
                "Get the text content or HTML of the current page. "
                "Use 'text' mode to read the visible text, 'html' mode to get raw HTML. "
                "Optionally pass a CSS selector to extract from a specific element. "
                "This lets you browse without screenshots — just read the DOM directly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["text", "html"],
                        "description": "'text' for visible text, 'html' for raw HTML",
                    },
                    "selector": {
                        "type": "string",
                        "description": "CSS selector to extract from (optional, gets full page if omitted)",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_links",
            "description": (
                "Get all clickable links on the current page. "
                "Returns text and href for each link. Use this to discover navigation options."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_forms",
            "description": (
                "Get all form fields on the current page with their labels, types, and current values. "
                "Use this to understand what inputs are available before filling them."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click an element on the page by its visible text. Works for buttons, links, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Visible text of the element to click"},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_fill",
            "description": (
                "Fill a form field by its label, placeholder text, or name attribute. "
                "First call browser_get_forms to see available fields."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "description": "Label, placeholder, or name of the field to fill"},
                    "value": {"type": "string", "description": "Text to fill in"},
                },
                "required": ["label", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_page_info",
            "description": (
                "Get comprehensive page info: title, URL, visible text summary, and top links. "
                "Use this as a quick overview of where you are on a page."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_evaluate",
            "description": (
                "Execute JavaScript on the page and return the result. "
                "Use this for advanced DOM queries or data extraction. "
                "The code runs in the page context and must return a serializable value."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "javascript": {
                        "type": "string",
                        "description": "JavaScript code to execute (must return a value)",
                    },
                },
                "required": ["javascript"],
            },
        },
    },
]


def execute(tool_name: str, args: dict) -> dict:
    """Execute a browser tool."""
    try:
        _ensure_browser()
    except Exception as e:
        return {"ok": False, "error": f"Browser connection failed: {e}"}

    if tool_name == "browser_navigate":
        return _run_async(browser.navigate(args.get("url", "")))

    elif tool_name == "browser_get_content":
        mode = args.get("mode", "text")
        selector = args.get("selector")
        if mode == "html":
            html = _run_async(_get_html(browser._page, selector))
            return {"ok": True, "mode": "html", "content": html}
        else:
            text = _run_async(_get_dom_text(browser._page, selector))
            return {"ok": True, "mode": "text", "content": text}

    elif tool_name == "browser_get_links":
        links = _run_async(_get_links(browser._page))
        return {"ok": True, "links": links, "count": len(links)}

    elif tool_name == "browser_get_forms":
        fields = _run_async(_get_form_fields(browser._page))
        return {"ok": True, "fields": fields, "count": len(fields)}

    elif tool_name == "browser_click":
        return _run_async(_click_by_text(browser._page, args.get("text", "")))

    elif tool_name == "browser_fill":
        return _run_async(_fill_by_label(browser._page, args.get("label", ""), args.get("value", "")))

    elif tool_name == "browser_page_info":
        info = _run_async(_get_page_info(browser._page))
        return {"ok": True, **info}

    elif tool_name == "browser_evaluate":
        js = args.get("javascript", "")
        try:
            result = _run_async(browser._page.evaluate(js))
            return {"ok": True, "result": result}
        except Exception as e:
            return {"ok": False, "error": f"JS evaluation failed: {e}"}

    return {"ok": False, "error": f"Unknown browser tool: {tool_name}"}


def route_score(prompt: str) -> float:
    """Score how relevant this module is for the given prompt."""
    from modules.media_index import matches as media_matches
    prompt_lower = prompt.lower()
    score = 0.0

    # Direct browser mentions
    if any(kw in prompt_lower for kw in [
        "brave", "chrome", "firefox", "navigate", "open site",
        "open brave", "open chrome", "open firefox",
        "new tab", "open tab",
    ]):
        score += 3.0

    # URL-like patterns
    import re
    if re.search(r'(?:go to |open |visit )\s*[\w.-]+\.[a-z]{2,}', prompt_lower):
        score += 3.5

    # "Play X" where X is NOT in Downloads → search YouTube
    if prompt_lower.startswith("play ") or "search for" in prompt_lower:
        if not media_matches(prompt_lower):
            score += 3.0  # Route to browser for YouTube search

    if any(kw in prompt_lower for kw in ["browse", "surf", "web page", "youtube", "google"]):
        score += 2.0

    return score
