"""Shared Chromium/Playwright PDF rendering: fonts are inlined as data URIs, so rendering never
touches the network. The prescription-specific rendering (Phase 7) builds on top of this.
"""

import base64
import logging
import mimetypes
import queue
import threading
from concurrent.futures import Future
from functools import cache
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

FONT_DIR = Path(settings.BASE_DIR) / "static" / "fonts"
FONT_FACES = [
    # family, weight, file, unicode-range
    ("Poppins", 400, "poppins-latin-400.woff2", "U+0000-00FF, U+2000-206F"),
    ("Poppins", 600, "poppins-latin-600.woff2", "U+0000-00FF, U+2000-206F"),
    ("Poppins", 700, "poppins-latin-700.woff2", "U+0000-00FF, U+2000-206F"),
    ("Cairo", 400, "cairo-arabic-400.woff2", "U+0600-06FF, U+0750-077F, U+FB50-FDFF, U+FE70-FEFF, U+200C-200F"),
    ("Cairo", 600, "cairo-arabic-600.woff2", "U+0600-06FF, U+0750-077F, U+FB50-FDFF, U+FE70-FEFF, U+200C-200F"),
    ("Cairo", 700, "cairo-arabic-700.woff2", "U+0600-06FF, U+0750-077F, U+FB50-FDFF, U+FE70-FEFF, U+200C-200F"),
    ("Cairo", 400, "cairo-latin-400.woff2", "U+0000-00FF"),
]


def _data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


@cache
def font_css() -> str:
    rules = []
    for family, weight, fname, urange in FONT_FACES:
        uri = _data_uri((FONT_DIR / fname).read_bytes(), "font/woff2")
        rules.append(
            f"@font-face{{font-family:'{family}';font-weight:{weight};src:url({uri}) format('woff2');"
            f"unicode-range:{urange};}}"
        )
    return "\n".join(rules)


def logo_uri(org_settings) -> str:
    if not org_settings or not org_settings.logo:
        return ""
    try:
        with org_settings.logo.open("rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return ""
    return _data_uri(data, mimetypes.guess_type(org_settings.logo.name)[0] or "image/png")


class PdfRenderError(RuntimeError):
    pass


def _browser_candidates():
    """Chromium builds installed by `playwright install chromium`, most suitable first."""
    import glob
    import os

    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.join(
        os.environ.get("LOCALAPPDATA", str(Path.home())), "ms-playwright"
    )
    patterns = [
        "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe",
        "chromium-*/chrome-win64/chrome.exe",
        "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell",
        "chromium-*/chrome-linux*/chrome",
    ]
    found = []
    for pat in patterns:
        found += sorted(glob.glob(os.path.join(base, pat)), reverse=True)
    return [f for f in found if os.path.isfile(f)]


def _launch(p):
    """Launch Chromium robustly: Playwright's default first, then every installed build by explicit path."""
    errors = []
    try:
        return p.chromium.launch()
    except Exception as e:
        errors.append(str(e).splitlines()[0])
    for exe in _browser_candidates():
        try:
            return p.chromium.launch(executable_path=exe)
        except Exception as e:
            errors.append(f"{exe}: {str(e).splitlines()[0]}")
    logger.error("Could not launch Chromium for PDFs: %s", " | ".join(errors))
    raise PdfRenderError(
        "مش قادر أشغّل Chromium عشان أعمل الـPDF. شغّل الأمر ده مرة واحدة: "
        r".venv\Scripts\python -m playwright install chromium"
    )


def html_to_pdf(html: str, footer_html: str = "", *, page_format: str = "A4") -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.evaluate("document.fonts.ready")
            return page.pdf(
                format=page_format,
                print_background=True,
                display_header_footer=bool(footer_html),
                header_template="<div></div>",
                footer_template=footer_html or "<div></div>",
                margin={"top": "12mm", "right": "12mm", "bottom": "16mm", "left": "12mm"},
            )
        finally:
            browser.close()


# --- warm renderer (05 §5.1: the doctor is waiting for the printer, target < 2 s) ----------------------------
#
# Playwright's sync API is bound to the thread that started it, and Django serves requests from many threads.
# So one daemon thread owns a single warm Chromium and renders jobs from a queue; callers block on a Future.
# If Chromium dies, the next job relaunches it.

_jobs: "queue.Queue[tuple]" = queue.Queue()
_worker_lock = threading.Lock()
_worker: threading.Thread | None = None


def _worker_loop():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = None
        try:
            browser = _launch(p)  # warm up right away: the first print shouldn't pay Chromium's cold start
        except PdfRenderError:
            browser = None
        while True:
            html, options, fut = _jobs.get()
            if fut.set_running_or_notify_cancel() is False:
                continue
            try:
                if browser is None or not browser.is_connected():
                    browser = _launch(p)
                page = browser.new_page()
                try:
                    page.set_content(html, wait_until="load")
                    page.evaluate("document.fonts.ready")
                    fut.set_result(page.pdf(**options))
                finally:
                    page.close()
            except Exception as e:  # keep the worker alive; relaunch on the next job
                logger.exception("PDF render failed")
                try:
                    if browser is not None:
                        browser.close()
                except Exception:
                    logger.debug("browser close failed", exc_info=True)
                browser = None
                fut.set_exception(e if isinstance(e, PdfRenderError) else PdfRenderError(str(e)))


def warm_up():
    """Start the renderer thread (and Chromium) in the background, e.g. when the prescription tab opens."""
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, name="pdf-renderer", daemon=True)
            _worker.start()


def render_pdf(html: str, *, timeout: float = 60, **options) -> bytes:
    """Render with the shared warm browser. `options` go to Playwright's `page.pdf()`."""
    warm_up()
    fut: Future = Future()
    _jobs.put((html, options, fut))
    return fut.result(timeout=timeout)
