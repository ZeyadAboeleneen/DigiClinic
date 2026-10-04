"""Shared Chromium/Playwright PDF rendering: fonts are inlined as data URIs, so rendering never
touches the network. The prescription-specific rendering (Phase 7) builds on top of this.
"""

import base64
import logging
import mimetypes
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
