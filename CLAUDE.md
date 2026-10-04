# CLAUDE.md — Execution Instructions

You are implementing **DigiClinic**, a clinic management system by Digitiva, forked from Marsool Al-Barq
(a quotation system). The plan in `docs/plan/` is the source of truth. Read `docs/plan/README.md` first, then
the files relevant to the current phase.

## Working rules
- Work **one phase at a time** from `docs/plan/08-roadmap.md`. At the end of each phase: run the quality gates,
  report against its Definition of Done, list how the user can verify it manually, commit `phase-N: <summary>`,
  then STOP and wait for approval.
- If a decision is not covered by the plan, **ask**. Do not silently change locked decisions in `docs/plan/README.md`.
  If you think one is wrong, say so and propose.
- **Development is local on Windows WITHOUT Docker.** `run.bat` does everything (uv sync → embedded PostgreSQL via
  `pgserver` → Tailwind → migrate/seed → WhatsApp gateway → `run_scheduler` → runserver).
- **DigiClinic must coexist with Marsool Al-Barq on the same machine**: data in `%LOCALAPPDATA%\digiclinic`,
  Postgres port 54339, DB `digiclinic`, Django 8010, WhatsApp gateway 3320. Never reuse the Al-Barq paths/ports.
- Production (Phase 10) depends on a hosting decision that is still open (see `07-operations.md` §7.6).

## Stack (locked)
Python 3.12 (uv) · Django 5.2 LTS · PostgreSQL 16 · HTMX 2 · Alpine.js 3 · Tailwind v4 standalone ·
Chromium/Playwright for PDFs · WhatsApp via local Node gateway `whatsapp-gateway/` (whatsapp-web.js) · SMTP ·
`rapidfuzz` for drug matching. **No Celery/Redis**: one `manage.py run_scheduler` process (local and server).
No Node build step for the Django app; HTMX/Alpine/fonts vendored in `static/`.

## Domain rules
- One clinic = one `Organization`. UI shows one doctor, but **every scheduling/clinical model has a `doctor` FK**.
- Booking mode per doctor: `slots` (exact time) or `queue` (number + estimated time). Both must work.
- Appointment changes go through `apps/scheduling/services.py` only (`book`, `reschedule`, `cancel`, `transition`,
  `mark_no_shows`). Every write for a doctor/day happens under `select_for_update()` on `DayLedger`.
  Reschedule = old appointment `rescheduled` + new one. Every time change bumps `Appointment.version`.
- **Every outgoing message is a `ScheduledMessage` row** (outbox). Nothing sends directly. Before sending, the
  dispatcher re-checks freshness (`appointment_version`, status, `min_lead_minutes`) and renders text at send time.
  `dedupe_key` is unique. Quiet hours apply. No messages without `patient.messaging_consent`.
- No-show policy, reminder offsets, templates, quiet hours, prescription sending: all from settings, never hardcoded.
  Auto-rebook happens at most once per chain (`auto_rebooked_from`).
- Prescriptions: drafts editable; `final` is immutable (edits create revision `-R1`). PDF in two print modes
  (`full` / `preprinted` with margins in mm); the copy sent to patients is always `full`. Allergy warnings must be
  acknowledged. Mixed Arabic/English text uses isolated bidi spans.
- Voice dictation: browser Web Speech API behind a JS provider interface. Dictated lines must be confirmed by the
  doctor before a prescription can be finalized.
- Times stored UTC, computed in `Africa/Cairo` with `zoneinfo` (DST-safe). Money `Decimal`. Phones E.164 (`apps/core/phones.py`).

## Non-negotiable conventions (inherited from Al-Barq)
- Every operational model inherits `TenantScopedModel`; views query via `.for_org(request.organization)`.
  Never `Model.objects.all()` in views. Cross-tenant test for every new view.
- Permissions via `@require_perm(...)` in views (`apps/accounts/permissions.py`), not only in templates.
  Reception must get 403 on clinical URLs (test it).
- Business logic in `services.py`; views stay thin. HTMX partials for interactive screens.
- Secrets only from `.env`; DB-stored credentials Fernet-encrypted (`apps/core/crypto.py`).
- Private files (logos, prescriptions, attachments) served via permission-checked views only.
- Medical records are never hard-deleted; `simple-history` on clinical models; `audit.log` for clinical views.
- No medical text or full phone numbers in logs.
- UI Arabic RTL, strings wrapped for i18n. Arabic search normalizes أ/إ/آ→ا, ة→ه, ى→ي, strips tatweel/diacritics.

## Quality gates per phase
- `.venv/Scripts/ruff check .` + `ruff format --check .` clean, `.venv/Scripts/python -m pytest` green,
  migrations committed, no TODOs in phase scope, new models registered in Django admin.
- Time-dependent logic is tested with a frozen/injected clock (no sleeping tests).

## Update this file
At the end of each phase, add a short "<App> notes" section here (like Al-Barq's CLAUDE.md had) with the commands,
gotchas and conventions introduced in that phase.
