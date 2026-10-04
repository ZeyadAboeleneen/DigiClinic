# CLAUDE.md — Execution Instructions

You are implementing **Marsool Al-Barq (مرسول البرق)**, the Al-Barq Quotation System. The plan in `docs/plan/` is the source of truth.
Read `docs/plan/README.md` first, then the files relevant to the current phase.

## Working rules
- Work **one phase at a time** from `docs/plan/08-roadmap.md`. At the end of each phase: run the quality
  gates, report what was done against its Definition of Done, list how the user can verify it manually,
  commit with message `phase-N: <summary>`, then STOP and wait for approval.
- If a decision is not covered by the plan, **ask** instead of guessing. Do not silently change the
  locked decisions in `docs/plan/README.md`. If you believe a decision is wrong, say so and propose.
- **Development is local on Windows WITHOUT Docker** (decided Sep 2026: no WSL/admin on the dev machine).
  `run.bat` does everything: `uv sync` → `scripts/devdb.py start` (PostgreSQL 16 from the `pgserver` wheel,
  port 54329, data in `%LOCALAPPDATA%/marsool-albarq`) → Tailwind build → migrate → seed_org → runserver.
  Private media also lives in `%LOCALAPPDATA%/marsool-albarq/media` (never inside OneDrive).
  Background jobs run eagerly in dev (no Redis); Celery + Redis are used in production only.
- Production (Phase 10) uses Docker Compose on the Linux server. Never touch host ports 80/443;
  other projects on the server must not be affected.

## Stack (locked)
Python 3.12 (via uv) · Django 5.2 LTS · PostgreSQL 16 · Redis 7 + Celery (prod) · HTMX 2 · Alpine.js 3 ·
Tailwind v4 (standalone CLI via pytailwindcss, pinned in run.bat) · **Chromium/Playwright for PDF** (replaces
WeasyPrint — it needs GTK on Windows) · **WhatsApp: local Node gateway `whatsapp-gateway/` (whatsapp-web.js)**
(replaces the OpenWA container; runs without Docker) · Docker Compose (prod only).
No Node build step for the Django app. HTMX/Alpine/fonts are vendored in `static/`.

## Domain rules
- A quotation is a **price list**: item, unit, unit price. No quantities, no line totals, no grand totals.
- Products have **no reference price**. Price is entered per quotation; suggest the last price sent to the same customer.
- Catalog is two-level: `ProductGroup` → `Category` → `Product`. PDF section rows show the Category name only.
- Owner manages groups/categories/products fully from the UI (add, edit, deactivate, reorder).
- Quote number = `{code}-{year}/{month:02d}/{day:02d}` from the customer code and issue date (RG-2026/09/24);
  2nd+ for the same key gets `-2`, `-3` (QuoteSequence per key). Revisions keep the number: `-R1`, `-R2`.
  Issue date defaults to today, validity to end of month (`OrganizationSettings.validity_mode`).
- Sending requires a **self-review gate**: the sending user must have opened the final PDF preview
  (recorded server-side) and ticked the confirmation checkbox. Enforced in the backend, not only the UI.
- WhatsApp sender number: 01000967017 (configured through settings UI, never hardcoded).
- PDF layout reference: `docs/plan/assets/reference-quotation.pdf`. Colors/fonts: `docs/plan/seed/brand.json`.

## Non-negotiable conventions
- Every operational model inherits `TenantScopedModel`. Views query via `.for_org(request.organization)`.
  Never `Model.objects.all()` in views. Add a cross-tenant access test for every new view.
- Money is `Decimal(12,2)`. No floats.
- Phone numbers stored E.164 via `phonenumbers` (default region EG).
- Issued quotations are locked while issued; «تعديل» (`services.reopen`) turns one back into a draft with the
  same number (old PDF + reviews dropped). Revisions (-R1) and duplicates still exist. Delete = manager+.
- Quote numbers generated inside `transaction.atomic()` with `select_for_update()` on `QuoteSequence`.
- Permission checks in views via `@require_perm(...)`, never only in templates.
- Secrets only from environment (`.env`, never committed; provide `.env.example`).
  SMTP/OpenWA credentials stored in DB are Fernet-encrypted.
- Private files (PDFs, logos) served via permission-checked view (`FileResponse` locally,
  `X-Accel-Redirect` in production).
- UI is Arabic RTL; user-facing strings wrapped for i18n (`gettext`).
- Arabic search normalizes: أ/إ/آ→ا, ة→ه, ى→ي, removes tatweel and diacritics.

## Auth notes
- Custom `accounts.User`: login by email (lower-cased), no username. Roles live on `Membership.role`.
- Permission matrix: `apps/accounts/permissions.py`. Templates use `can.<perm_with_underscores>`.
- Create users from the terminal: `manage.py create_user --email … --name … --role owner|admin|manager|sales|viewer`.

## Customers notes
- `Customer` tree via `parent` (group → chain → hotel). `recipient_lines()` builds the PDF "To" block.
- Phones: `apps/core/phones.py` (`to_e164`, `to_local`, `mask`). Search: `Customer.objects.for_org(org).search(q)`.
- The embedded dev Postgres has no `pg_trgm`; search uses normalized `icontains` (fine at this scale).
- `manage.py import_customers <xlsx>` (suppliers skipped), `manage.py seed_demo [--remove]` (fake Travco/Jaz data).
- Rebuild CSS after adding template classes: `.venv/Scripts/tailwindcss -i static/src/app.css -o static/css/app.css --minify`.

## Catalog notes
- `ProductGroup` → `Category` (printed as pink section rows) → `Product` (+ `Unit`). No prices on products.
- Default ordering of `Product` = group order → category order → product order = PDF order.
- `import_products` only creates missing rows (never overwrites UI edits). Don't run it automatically.
- Reorder endpoint `catalog:reorder` (kind=product|category|group, ids in order, same parent only).
- Editing needs `product.edit` (manager+). Sortable.js vendored in `static/vendor/`.

## Quotations notes
- Business rules live in `apps/quotations/services.py` (new_draft, add_product/category/custom, price_hints,
  next_number, finalize, create_revision, duplicate). Views stay thin.
- Items carry snapshots (description, unit, category_name, section_order, item_order) → PDF order/grouping.
- `QuotationItem.save/delete` raise `ImmutableQuotationError` once the quotation isn't a draft.
- Builder = one page of HTMX partials (party, items, meta, summary, picker) wired by the `itemsChanged` event.
- `seed_org` never overwrites an existing org's settings unless `--force` (run.bat calls it on every start).

## Messaging notes
- Self-review gate: loading the final PDF with `?review=1` (the iframe on the send page) records a
  `QuotationReview` for that user; `messaging.services.create_deliveries` requires it + the checkbox.
- SMTP config lives in `SendingChannelConfig.config` (Fernet via `apps/core/crypto.py`); never render the password.
- Dispatch: background thread locally, `MESSAGING_SYNC=True` in tests, Celery in production (Phase 10).
  Temporary SMTP errors retry after `MESSAGING_RETRY_DELAYS`; auth errors don't.
- The final PDF view is `xframe_options_sameorigin` so it can be embedded.

## WhatsApp notes
- `whatsapp-gateway/server.js` (Express + whatsapp-web.js) listens on 127.0.0.1:3310, API key `WA_GATEWAY_KEY`
  from `.env` (it reads the same file). One session per org (`/sessions/<org_id>`), LocalAuth data in
  `%LOCALAPPDATA%/marsool-albarq/whatsapp`. Uses Playwright's `chrome-headless-shell` (full chrome.exe can't
  be spawned from Node on this machine). `run.bat` installs its node_modules once and starts it minimized.
- Django side: `apps/messaging/whatsapp.py` (httpx; tests inject `WA_GATEWAY_TRANSPORT`), webhook
  `/integrations/whatsapp/webhook/` verified by HMAC-SHA256 of the body. Acks: 2 → delivered, 3/4 → read.
- Anti-ban: one message per `WA_RATE_LIMIT_SECONDS` (20) per org; manual sending only; email fallback button.

## Audit notes
- `apps.audit`: `audit.log(action, request=..., target=..., summary=...)` never raises; `AuditEvent` is
  append-only (save on existing / delete raise). Login/logout/failed login via auth signals.
- Screen `/activity/` (audit.view = manager+); quotation page shows its own timeline.
- `expire_overdue()` runs on dashboard/list loads and via `manage.py expire_quotations` (run.bat on start).
- Deleting: customers/contacts only when no quotation uses them; products anytime (items keep snapshots);
  categories/groups only when empty.

## Quality gates per phase
- `.venv/Scripts/ruff check .` + `ruff format --check .` clean, `.venv/Scripts/python -m pytest` green, migrations committed, no TODOs left in phase scope.
- New models registered in Django admin (superuser emergency access only).

## Seed data
- `python manage.py import_products docs/plan/seed/products.csv --org albarq` (idempotent upsert; columns:
  group_order,group,group_description,category_order,category,product_order,name_ar,unit — 72 products)
- `docs/plan/seed/brand.json` → initial Organization + OrganizationSettings for `albarq`.
