"""Quotation PDF rendering: Django template → HTML → headless Chromium (Playwright) → PDF.

Fonts (Cairo/Poppins) and the logo are inlined as data URIs, so rendering never touches the network.
"""

import base64
import logging
import mimetypes
from functools import cache
from itertools import groupby
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import escape

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


def _logo_uri(org_settings) -> str:
    if not org_settings or not org_settings.logo:
        return ""
    try:
        with org_settings.logo.open("rb") as fh:
            data = fh.read()
    except FileNotFoundError:
        return ""
    return _data_uri(data, mimetypes.guess_type(org_settings.logo.name)[0] or "image/png")


def _recipient_lines(q):
    if q.customer_snapshot.get("recipient_lines"):
        return [tuple(x) for x in q.customer_snapshot["recipient_lines"]]
    return q.customer.recipient_lines() if q.customer_id else []


def _attention(q):
    c = q.contact_snapshot or {}
    if not c and q.contact_id:
        ct = q.contact
        c = {"salutation": ct.salutation, "name": ct.name, "job_title": ct.job_title, "department": ct.department}
    if not c:
        return ""
    name = " ".join(x for x in (c.get("salutation"), c.get("name")) if x)
    extra = c.get("job_title") or c.get("department")
    return f"{name} — {extra}" if extra else name


def build_context(q, *, draft: bool):
    org = q.organization
    s = getattr(org, "settings", None)
    items = list(q.items.all())
    for n, it in enumerate(items, start=1):
        it.no = n
    sections = [(name, list(rows)) for name, rows in groupby(items, key=lambda i: i.category_name)]
    return {
        "q": q,
        "org": org,
        "s": s,
        "font_css": font_css(),
        "logo": _logo_uri(s),
        "draft": draft,
        "number": q.display_number if (q.number and not draft) else "",
        "recipient_lines": _recipient_lines(q),
        "attention": _attention(q),
        "sections": sections,
        "primary": (s.primary_color if s else "#AE171C"),
        "section_bg": (s.section_row_color if s else "#FBECEC"),
    }


def render_html(q, *, draft: bool) -> str:
    return render_to_string("pdf/quotation.html", build_context(q, draft=draft))


def _footer_html(q) -> str:
    org = q.organization
    s = getattr(org, "settings", None)
    phones = " / ".join(s.phones) if s and s.phones else ""
    left = escape(" • ".join(x for x in (org.name_ar, phones, org.name_en.upper()) if x))
    return (
        '<div style="width:100%;font-size:7.5px;color:#888;padding:0 12mm;direction:rtl;'
        "font-family:'Segoe UI',Tahoma,Arial,sans-serif;display:flex;justify-content:space-between;"
        'border-top:0.5px solid #ddd;padding-top:4px;">'
        f"<span>{left}</span>"
        '<span>صفحة <span class="pageNumber"></span> من <span class="totalPages"></span></span>'
        "</div>"
    )


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


def html_to_pdf(html: str, footer_html: str = "") -> bytes:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = _launch(p)
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="load")
            page.evaluate("document.fonts.ready")
            return page.pdf(
                format="A4",
                print_background=True,
                display_header_footer=bool(footer_html),
                header_template="<div></div>",
                footer_template=footer_html or "<div></div>",
                margin={"top": "12mm", "right": "12mm", "bottom": "16mm", "left": "12mm"},
            )
        finally:
            browser.close()


def render_pdf(q, *, draft: bool) -> bytes:
    return html_to_pdf(render_html(q, draft=draft), _footer_html(q))


def download_name(q) -> str:
    customer = q.customer_snapshot.get("name") or (q.customer.name if q.customer_id else "")
    number = (q.display_number or f"draft-{q.pk}").replace("/", "-")
    return f"عرض أسعار - {customer} - {number}.pdf"


def generate_final_pdf(q) -> None:
    """Render and store the immutable final PDF of an issued quotation."""
    data = render_pdf(q, draft=False)
    name = f"{q.display_number.replace('/', '-')}.pdf"
    if q.pdf_file:
        q.pdf_file.delete(save=False)
    q.pdf_file.save(name, ContentFile(data), save=False)
    q.pdf_generated_at = timezone.now()
    q.save(update_fields=["pdf_file", "pdf_generated_at", "updated_at"])


def generate_final_pdf_safely(quotation_id: int) -> None:
    from apps.quotations.models import Quotation

    q = Quotation.objects.select_related("organization__settings", "customer", "contact").get(pk=quotation_id)
    try:
        generate_final_pdf(q)
    except Exception:
        # The quotation is already issued; the PDF is regenerated on first download.
        logger.exception("PDF generation failed for quotation %s", quotation_id)
