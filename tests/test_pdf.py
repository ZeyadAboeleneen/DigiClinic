import io
from decimal import Decimal
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.urls import reverse
from pypdf import PdfReader

from apps.catalog.models import Product
from apps.customers.models import Contact, Customer, CustomerKind
from apps.documents import pdf
from apps.quotations import services

SEED = Path(settings.BASE_DIR) / "docs" / "plan" / "seed" / "products.csv"


@pytest.fixture
def world(org_a, make_member, settings, tmp_path):
    settings.STORAGES = {
        **settings.STORAGES,
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage", "OPTIONS": {"location": tmp_path}},
    }
    call_command("import_products", str(SEED), org="albarq")
    sales = make_member(org_a, "sales")
    travco = Customer.objects.create(organization=org_a, name="Travco", kind=CustomerKind.GROUP, code="TR")
    jaz = Customer.objects.create(
        organization=org_a, name="Jaz Hotels", kind=CustomerKind.CHAIN, parent=travco, code="JZ"
    )
    contact = Contact.objects.create(organization=org_a, customer=jaz, name="Ahmed", job_title="مدير المشتريات")
    return org_a, sales, jaz, contact


def _quote(org, user, customer, contact, n=5, prices=("65", "5.20")):
    q = services.new_draft(org, user, customer, contact)
    for i, p in enumerate(Product.objects.for_org(org)[:n]):
        item, _ = services.add_product(q, p)
        item.unit_price = Decimal(prices[i % len(prices)])
        item.save()
    return q


# --- HTML (fast, no browser) ---------------------------------------------------------------


def test_html_matches_reference_layout(world):
    org, sales, jaz, contact = world
    q = services.finalize(_quote(org, sales, jaz, contact), sales)
    html = pdf.render_html(q, draft=False)
    assert q.display_number in html
    assert "السادة / شركة" in html and "Travco" in html and "سلسلة الفنادق" in html and "Jaz Hotels" in html
    assert "Ahmed — مدير المشتريات" in html
    assert 'م</th><th class="desc">الصنف</th><th>الوحدة</th><th>السعر (ج.م)' in html  # 4 columns only
    assert ">65<" in html and ">5.20<" in html and "65.00" not in html
    assert "أدوات المائدة الخشبية" in html
    assert "watermark" not in html.split("<body>")[1]
    assert "@font-face" in html and "data:font/woff2;base64," in html  # fonts embedded, no network


def test_draft_html_has_watermark_and_no_number(world):
    org, sales, jaz, contact = world
    html = pdf.render_html(_quote(org, sales, jaz, contact), draft=True)
    assert '<div class="watermark">مسودة</div>' in html
    assert 'رقم العرض:</b> <span class="ltr">مسودة' in html


def test_download_name(world):
    org, sales, jaz, contact = world
    q = services.finalize(_quote(org, sales, jaz, contact), sales)
    assert pdf.download_name(q) == f"عرض أسعار - Jaz Hotels - {q.display_number.replace('/', '-')}.pdf"


# --- real PDFs (Chromium) ------------------------------------------------------------------


def test_final_pdf_generated_on_finalize(world, django_capture_on_commit_callbacks):
    org, sales, jaz, contact = world
    with django_capture_on_commit_callbacks(execute=True):
        q = services.finalize(_quote(org, sales, jaz, contact), sales)
    q.refresh_from_db()
    assert q.pdf_file and q.pdf_generated_at
    assert q.pdf_file.name.startswith(f"org_{org.pk}/quotations/")
    reader = PdfReader(q.pdf_file.open("rb"))
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "JZ-" in text and "65" in text


def test_all_products_paginate(world):
    org, sales, jaz, contact = world
    q = services.finalize(_quote(org, sales, jaz, contact, n=72), sales)
    reader = PdfReader(io.BytesIO(pdf.render_pdf(q, draft=False)))
    assert 3 <= len(reader.pages) <= 5
    # the footer page counter is on every page
    assert all("4" in p.extract_text() or "3" in p.extract_text() or "5" in p.extract_text() for p in reader.pages)


def test_preview_and_download_views(client, world, make_member):
    org, sales, jaz, contact = world
    q = _quote(org, sales, jaz, contact)
    client.force_login(sales)
    r = client.get(reverse("quotations:preview", args=[q.pk]))
    assert r.status_code == 200 and r["Content-Type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    # Draft has no final PDF yet → redirected to the preview
    assert client.get(reverse("quotations:pdf", args=[q.pk])).status_code == 302

    q = services.finalize(q, sales)
    assert not q.pdf_file  # on_commit didn't run in this test → rendered on first download
    r = client.get(reverse("quotations:pdf", args=[q.pk]), {"download": "1"})
    assert r.status_code == 200
    assert "attachment" in r["Content-Disposition"]
    assert b"".join(r.streaming_content).startswith(b"%PDF")
    q.refresh_from_db()
    assert q.pdf_file
    # issued quotation's preview URL points to the final file
    assert client.get(reverse("quotations:preview", args=[q.pk])).status_code == 302

    viewer = make_member(org, "viewer")
    client.force_login(viewer)
    assert client.get(reverse("quotations:pdf", args=[q.pk])).status_code == 200


def test_viewer_cannot_preview_drafts(client, world, make_member):
    org, sales, jaz, contact = world
    q = _quote(org, sales, jaz, contact)
    client.force_login(make_member(org, "viewer"))
    assert client.get(reverse("quotations:preview", args=[q.pk])).status_code == 403


def test_pdf_cross_tenant_404(client, world, org_b, make_member):
    org, sales, jaz, contact = world
    q = services.finalize(_quote(org, sales, jaz, contact), sales)
    client.force_login(make_member(org_b, "owner"))
    assert client.get(reverse("quotations:pdf", args=[q.pk])).status_code == 404
    assert client.get(reverse("quotations:preview", args=[q.pk])).status_code == 404


def test_launch_falls_back_to_explicit_executable(monkeypatch):
    calls = []

    class FakeChromium:
        def launch(self, executable_path=None):
            calls.append(executable_path)
            if executable_path is None:
                raise RuntimeError("Executable doesn't exist at X")
            return "browser"

    class FakeP:
        chromium = FakeChromium()

    monkeypatch.setattr(pdf, "_browser_candidates", lambda: ["C:/a/chrome-headless-shell.exe"])
    assert pdf._launch(FakeP()) == "browser"
    assert calls == [None, "C:/a/chrome-headless-shell.exe"]

    monkeypatch.setattr(pdf, "_browser_candidates", list)
    with pytest.raises(pdf.PdfRenderError):
        pdf._launch(FakeP())


def test_pdf_view_shows_friendly_error(client, world, monkeypatch):
    org, sales, jaz, contact = world
    q = services.finalize(_quote(org, sales, jaz, contact), sales)

    def boom(*a, **k):
        raise pdf.PdfRenderError("مش قادر أشغّل Chromium")

    monkeypatch.setattr(pdf, "render_pdf", boom)
    client.force_login(sales)
    r = client.get(reverse("quotations:pdf", args=[q.pk]))
    assert r.status_code == 503 and "مش قادر أشغّل Chromium" in r.content.decode()
