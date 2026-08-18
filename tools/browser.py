"""
Browser tool — connects to the user's real Brave/Chromium browser via CDP.
Navigations, media control, etc. happen in the actual browser with all
sessions, cookies, and extensions intact.

Requires Brave (or Chromium) to be running with --remote-debugging-port=9222.
Megatron will launch Brave with that flag if it's not already available.
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CDP_URL = "http://127.0.0.1:9222"


def _is_cdp_alive() -> bool:
    """Check if a browser is already listening on the CDP port."""
    import urllib.request
    try:
        req = urllib.request.Request(f"{CDP_URL}/json/version", method="GET")
        urllib.request.urlopen(req, timeout=3)
        return True
    except Exception:
        return False


class BrowserTool:
    """
    Connects to Brave/Chromium via Chrome DevTools Protocol.

    - First tries to connect to an already-running browser on port 9222.
    - If none found, launches Brave with --remote-debugging-port=9222.
    - Uses your real browser profile — all logins/sessions preserved.
    """

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self):
        """
        Connect to Brave browser via CDP — and ONLY Brave CDP.
        1. If CDP is already available, connect immediately.
        2. If not, kill Flatpak Brave and relaunch it with --remote-debugging-port=9222.
        3. Wait for CDP to come up and connect.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise RuntimeError(
                "Playwright is not installed. Run:\n"
                "  pip install playwright && playwright install chromium"
            )

        self._playwright = await async_playwright().start()

        # Step 1: Try connecting to existing CDP
        if _is_cdp_alive():
            self._browser = await self._playwright.chromium.connect_over_cdp(CDP_URL)
            logger.info("Connected to Brave via CDP")
            await self._pick_page()
            return

        # Step 2: Not running — restart Brave with CDP
        logger.info("Brave CDP not available — restarting …")
        try:
            subprocess.run(["flatpak", "kill", "com.brave.Browser"],
                           capture_output=True, timeout=10)
        except Exception:
            pass
        time.sleep(2)

        subprocess.Popen(
            ["flatpak", "run", "com.brave.Browser", "--remote-debugging-port=9222"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

        for _ in range(30):
            time.sleep(1)
            if _is_cdp_alive():
                break
        else:
            raise RuntimeError("Brave launched but CDP not available within 30s")

        self._browser = await self._playwright.chromium.connect_over_cdp(CDP_URL)
        logger.info("Connected to restarted Brave")
        await self._pick_page()

    async def _pick_page(self):
        """Use the first existing Brave tab — never create new ones."""
        # If we already have a live page, keep it
        if self._page is not None:
            try:
                await self._page.title()
                return
            except Exception:
                pass

        # Grab the first available page from Brave
        contexts = self._browser.contexts
        if contexts and contexts[0].pages:
            self._page = contexts[0].pages[0]
            logger.info("Using existing Brave tab: %s", await self._page.title())
            return

        # Last resort: create one page (should rarely happen)
        if contexts:
            self._page = await contexts[0].new_page()
        else:
            ctx = await self._browser.new_context()
            self._page = await ctx.new_page()
        logger.info("Created new tab")

    async def stop(self):
        if self._page:
            try: await self._page.close()
            except Exception: pass
        if self._browser:
            try: await self._browser.close()
            except Exception: pass
        if self._playwright:
            await self._playwright.stop()

    async def _ensure_page(self):
        """Return a live page — reuses existing, reconnects if browser died."""
        try:
            if self._page is not None:
                await self._page.title()
                return self._page
        except Exception:
            pass

        # Need a page — try to use or create one
        try:
            await self._pick_page()
            return self._page
        except Exception:
            pass

        # Browser may have been restarted — reconnect
        if not _is_cdp_alive():
            await self.start()
        else:
            self._browser = await self._playwright.chromium.connect_over_cdp(CDP_URL)
            await self._pick_page()
        return self._page

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------
    async def navigate(self, url: str) -> dict:
        """Open a URL in the active tab."""
        page = await self._ensure_page()
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        title = await page.title()
        return {"url": page.url, "title": title}

    async def google_search(self, query: str) -> dict:
        """Run a Google search and return the first few results."""
        page = await self._ensure_page()
        encoded = query.replace(" ", "+")
        await page.goto(
            f"https://www.google.com/search?q={encoded}",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        title = await page.title()
        results = await page.evaluate("""() => {
            const items = document.querySelectorAll('h3');
            return Array.from(items).slice(0, 5).map(h => ({
                title: h.innerText,
                link: h.closest('a')?.href || ''
            }));
        }""")
        return {"url": page.url, "title": title, "results": results}

    # ------------------------------------------------------------------
    # Media control
    # ------------------------------------------------------------------
    _MEDIA_JS = """
    (() => {
        const media = document.querySelector('video, audio');
        if (!media) return JSON.stringify({error: 'No media element found on this page'});
        return JSON.stringify({
            tag: media.tagName,
            src: media.src || '',
            currentTime: media.currentTime,
            duration: media.duration || 0,
            paused: media.paused,
            volume: media.volume,
            muted: media.muted,
            readyState: media.readyState,
        });
    })()
    """

    async def media_status(self) -> dict:
        page = await self._ensure_page()
        raw = await page.evaluate(self._MEDIA_JS)
        return json.loads(raw)

    async def media_play(self) -> dict:
        page = await self._ensure_page()
        await page.evaluate("document.querySelector('video,audio')?.play()")
        return await self.media_status()

    async def media_pause(self) -> dict:
        page = await self._ensure_page()
        await page.evaluate("document.querySelector('video,audio')?.pause()")
        return await self.media_status()

    async def media_set_volume(self, volume: float) -> dict:
        v = max(0.0, min(1.0, volume))
        page = await self._ensure_page()
        await page.evaluate(f"const m=document.querySelector('video,audio');if(m){{m.volume={v}}}")
        return await self.media_status()

    async def media_seek(self, seconds: float) -> dict:
        page = await self._ensure_page()
        await page.evaluate(f"const m=document.querySelector('video,audio');if(m){{m.currentTime={seconds}}}")
        return await self.media_status()

    async def media_skip(self, delta_seconds: float = 10) -> dict:
        page = await self._ensure_page()
        await page.evaluate(f"const m=document.querySelector('video,audio');if(m){{m.currentTime+=({delta_seconds})}}")
        return await self.media_status()

    async def youtube_search_and_play(self, query: str) -> dict:
        """Search YouTube and play the first result."""
        page = await self._ensure_page()
        encoded = query.replace(" ", "+")
        await page.goto(
            f"https://www.youtube.com/results?search_query={encoded}",
            wait_until="domcontentloaded",
            timeout=30000,
        )
        try:
            await page.click("ytd-video-renderer a#video-title", timeout=5000)
            await page.wait_for_selector("video", timeout=10000)
            title = await page.title()
            status = await self.media_status()
            return {"action": "playing", "title": title, **status}
        except Exception as e:
            return {"error": f"Could not click first result: {str(e)}"}

    async def get_current_page_info(self) -> dict:
        page = await self._ensure_page()
        return {"url": page.url, "title": await page.title()}
