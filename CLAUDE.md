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

## Doctors notes (Phase 1)
- `apps/doctors`: `Doctor` (single row per org in v1 — `views._get_doctor(org)` gets-or-creates it on first visit
  to the settings screen), `WorkingPeriod`, `ScheduleException` (`closed`/`custom`/`extra`), `VisitType`.
  `simple-history` on `Doctor`/`WorkingPeriod`/`VisitType`.
- `apps/core/timeutils.py`: `CAIRO` zoneinfo, `weekday_egypt()` (Python Monday=0..Sunday=6 → Egyptian
  Saturday=0..Friday=6: `(python_weekday + 2) % 7`), `local_dt()` builds a DST-correct aware datetime directly
  from a `date` + `time` — never do manual UTC offset math for clinic hours.
- `apps/scheduling/availability.periods_for(doctor, day)` is the only place that resolves a day's actual working
  periods (weekly schedule + exceptions). `scheduling` has no models yet — `Appointment`/`DayLedger` land in Phase 3.
- Settings screens live under `org/settings/doctor/` (profile + weekly grid + exceptions, HTMX partials swapped
  in place) and `org/settings/visit-types/`. Both gated by the `schedule.manage` permission (doctor/admin/owner).
- `seed_org` now also seeds the doctor/working periods/visit types from `clinic.json`'s `doctor`/`working_periods`/
  `visit_types` keys (same create-or-skip-unless-`--force` rule as the org settings).

## Patients notes (Phase 2)
- `apps/patients`: `Patient`, `Allergy`, `ChronicCondition`, `PatientFieldDefinition` (model only — wired into
  forms in Phase 6), `PatientSequence` (one row per org; `services.next_file_number()` assigns `file_number`
  under `select_for_update()`, same pattern as Al-Barq's old `QuoteSequence`). `simple-history` on `Patient`.
- `services.create_patient(org, instance, by=...)` is the only way to create a patient (wraps file-number
  assignment in `transaction.atomic()`); build the unsaved instance via `PatientForm.save(commit=False)` first.
- Phone is intentionally **not unique** (families share one number) — `services.possible_duplicates()` only
  warns, never blocks. Search (`PatientQuerySet.search()`) matches partial phone digits (any of
  `010.../+2010.../2010...`), `normalize_ar`-matched name, or file number.
- Permissions: `patient.view_basic`/`edit_basic` (reception+) cover demographics, contact info and **allergies**
  (shown as a safety banner per 04§4.3). `patient.view_medical`/`edit_medical` (doctor/admin/owner) gate
  `ChronicCondition` — reception's patient detail page has no "الأمراض المزمنة" section at all. `patient.merge`
  (doctor/admin/owner) via `services.merge_patients()` (moves allergies/chronic conditions, sets `merged_into`,
  deactivates the duplicate — appointments/visits/attachments move too once those apps exist).
- `import_patients <xlsx> --org <slug>` (columns: الاسم، الموبايل، السن، النوع، ملاحظات — idempotent, matched by
  name+phone) and `seed_demo [--org] [--remove]` (30 fake patients, 3 sharing one phone number).

## Scheduling notes (Phase 3)
- `apps/scheduling`: `Appointment`, `AppointmentEvent`, `DayLedger` (one row per doctor+day; every write for that
  day happens under `select_for_update()` on it — this is the only place conflicts/races are prevented).
  `availability.Period.period_id` (`wp-<pk>`/`exc-<pk>`) is stored on `Appointment.period_key` instead of a
  `WorkingPeriod` FK, since queue periods can come from a `ScheduleException` (custom/extra) that has no
  `WorkingPeriod` row — the schema note in `02-database-schema.md` assumed every period is a `WorkingPeriod`; this
  is the one locked-doc deviation so far, flagged here per CLAUDE.md's own rule.
- `services.py` is the only way to touch an appointment: `free_slots`/`queue_availability` (read-only),
  `book`/`reschedule`/`cancel`/`transition` (all `@transaction.atomic`, lock the `DayLedger` first).
  `ALLOWED_TRANSITIONS` in `models.py` is the full state machine (12-booking-engine §6); anything else raises
  `InvalidTransition`. Queue numbers are **never reused** — `DayLedger.queue_numbers` is `{period_id: last number}`,
  only ever incremented, even when an appointment in that period is cancelled.
- Free follow-up pricing (`_price_for`): a follow-up `VisitType` is priced 0 and gets `followup_of` set when the
  patient has a `completed` appointment with the same doctor within `visit_type.free_followup_days`.
- Closing a day (`doctors:exception_add` with kind `closed`/`custom`) checks `affected_by_closing()` for the date
  being closed; if any active appointment no longer fits, the exception still saves but the response carries
  `HX-Redirect` to `scheduling:affected` instead of swapping the exceptions partial — staff reschedule each
  affected appointment to a freshly-computed suggestion (`reschedule_to_suggestion`) one at a time.
- Booking screen (`/booking/`) is a single doctor-scoped flow: search (reuses `Patient.search`) → quick-add if not
  found → visit type + day picker → `/booking/<patient>/slots/` (HTMX partial, branches on `doctor.booking_mode`)
  → `POST /booking/<patient>/confirm/` → redirects to a cleared `/booking/` with a toast (matches the "search
  screen returns empty, ready for the next patient" UX in `03-modules-and-ux.md` §3.2). The actual "ابعت رسالة
  تأكيد" / scheduled-reminders part of that screen's mockup is Phase 4 (`notifications` doesn't exist yet) —
  `book()` has a commented-out `transaction.on_commit` hook marking where it plugs in.
- Permissions added: `appointment.view` (all roles), `appointment.book`/`reschedule`/`cancel` (reception/doctor/
  admin/owner), `appointment.overbook` (doctor/owner only — reception's "حجز استثنائي" button is hidden, not just
  disabled, when the period is full and they lack this permission).
- Tests needing a frozen clock use `freezegun` (new dev dependency) — Egypt's DST (2026: Apr 25 → Oct 30) matters
  for same-day "is this slot still in the future" checks, not just for `periods_for` from Phase 1.

## Notifications notes (Phase 4)
- `apps/notifications`: `NotificationSettings` (one per org, `for_org()` get-or-creates), `NotificationTemplate`,
  `ScheduledMessage` (the outbox), `SchedulerHeartbeat`, `InboundMessage`. `simple-history` on settings/templates.
  `messaging.Delivery` now has a `scheduled_message` FK (one `Delivery` per send attempt); `messaging.WhatsAppNumber`
  caches the gateway's `isRegisteredUser` result for 30 days.
- `services.py` only **enqueues** (`schedule_for`, `on_rescheduled`, `on_cancelled`, `enqueue`, `cancel_pending`);
  `dispatcher.Dispatcher.run_once()` is the only code that sends. `scheduling.services.book/reschedule/cancel`
  call the hooks via `transaction.on_commit` — tests must use `django_capture_on_commit_callbacks(execute=True)`
  to see rows. `book(notify=False)` is used inside `reschedule()` (the "rescheduled" message replaces a fresh
  confirmation). Phase 5 adds `on_no_show`/`on_queue_progress` on top of `enqueue()`.
- `rescheduled` messages hang off the **new** appointment with `context={"old_date","old_time"}`; `cancelled`
  messages hang off the cancelled one. Freshness guard (`Dispatcher.freshness`) per event: version mismatch →
  `cancelled`, wrong appointment status → `cancelled`, less than `template.min_lead_minutes` left → `skipped`,
  quiet hours re-applied at send time (a scheduler restarting at night defers instead of sending).
- Quiet hours wrap midnight. `NotificationSettings.urgent_until` (default 23:00) is the plan's "rescheduled/cancelled
  for today/tomorrow still go out in quiet hours before 11pm" knob — added as a field since 06 §6.5 says it's a setting.
- Retries: `MESSAGING_RETRY_DELAYS` (60/300/900 s). WhatsApp `not_ready`/5xx are temporary; after the last retry →
  `failed` + an email fallback row (`<dedupe_key>:fallback`) if `email_fallback` and the patient has an email.
  Rows stuck in `sending` after a crash are marked `failed` on scheduler start (`recover_interrupted`) — never
  auto-resent, to avoid duplicates.
- `manage.py run_scheduler [--once]`: `pg_try_advisory_lock` (a second copy refuses to start), heartbeat every
  tick (`SCHEDULER_TICK_SECONDS`=20), `JOBS` list (dispatch every tick, cleanup daily — Phase 5 adds
  `mark_no_shows` there). Screens warn when the heartbeat is > 2 minutes old. `run.bat` starts it minimized.
- WhatsApp anti-ban gap: the dispatcher sleeps `wa_min_gap_seconds` + 0–10 s jitter between sends in-process;
  disabled in tests via `NOTIFY_SLEEP_BETWEEN_WA=False` (gap tests inject `sleep`/`monotonic`/`jitter`).
- Templates screen: `org/settings/messages/` (settings.manage) — variable buttons, live HTMX preview with sample
  data, "ابعت تجربة" (an `event=test` outbox row sent synchronously via `dispatcher.send_now`). Unknown `{vars}`
  are rejected at save time (`templating.validate_body`). Message log: `/messages/` (`messages.view` /
  `messages.retry`: reception/doctor/admin/owner, not viewer), filterable by status/event/day/appointment.
- Gateway: new `GET /sessions/:id/check/:number` and inbound 1:1 messages forwarded as `event: "message"` →
  `InboundMessage` (stored only, matched to a patient by phone).
- `seed_notifications [--org] [--force]` creates the 9 default templates (06 §6.3) + settings from `clinic.json`;
  run by `run.bat` after `seed_org`, idempotent.
- Local setup gotcha: on this machine pip under Python 3.10 fails TLS verification (intercepting proxy/AV);
  uv was installed with `C:\Python313\python.exe -m pip install --user uv` and `run.bat` now sets `UV_NATIVE_TLS=1`.

## Reception notes (Phase 5)
- New apps: `reception` (screen only, no models), `billing` (`Payment`; `services.balance()` computes
  due/paid/status — pass a prefetched payments list to avoid N+1; `record_payment()`; `cashbox(org, day)`), and
  `clinical` with `Visit` + `Vitals` **created early** because vitals are recorded at check-in (Vitals is a
  OneToOne on Visit per the schema). `Visit.for_appointment(appt)` get-or-creates the open visit. Visit's own
  `history` TextField (medical history) clashes with simple-history's default name, so its HistoricalRecords
  manager is `Visit.records`. Phase 6 builds the doctor desk on these models.
- `scheduling.services` additions: `walk_in()` (same-day book + immediately `arrived`, source=walk_in → no
  confirmation), `mark_no_show()` (applies `no_show_policy`; auto-rebook via `find_rebook_slot` = closest time of
  day, `auto_rebook_after_days` later, within `auto_rebook_window_days`; never for an appointment that is itself
  `auto_rebooked_from` something), `undo_no_show()` (cancels the auto-rebooking **silently** — `cancel(notify=False)`),
  `due_no_shows()/mark_no_shows()` (scheduler job every 2 min: slots → `start + grace`; queue → after the period
  ends, or never when `no_show_queue_mark_at=manual`). `transition()` backwards ("رجوع خطوة") clears the
  undone step's timestamp.
- `notifications.on_no_show(appt, rebooked)` and `on_queue_progress(doctor, day)` (fired on_commit by
  `transition()` to in_consultation/completed for queue appointments). "Ahead" counts every earlier number not yet
  completed/no-show/cancelled — the patient currently with the doctor counts. `near_turn` is never skipped for
  "time passed" (queue estimates run late), only if the patient already arrived.
- Reception screen `/reception/` (`reception.operate`: reception/doctor/owner — **admin does not get it**, per the
  matrix). Polls `/reception/rows/?sig=` every 5 s; the server returns 204 when the row signature is unchanged, and
  polling pauses while a modal is open. Actions return the fresh rows partial + an `HX-Trigger: toast` event;
  invalid modal forms come back with `HX-Retarget: #modal`. "خلص" is on the reception screen for now (the doctor
  desk takes over in Phase 6). Cashbox `/cashbox/` (`payment.record`).
- New permissions: `reception.operate`, `vitals.record`, `payment.record` (reception/doctor/owner),
  `report.finance` (doctor/owner, used by Phase 9 reports).
- `simulate_day [--remove]` books 10 seed_demo patients today through the real services (3 done+paid, 1 with the
  doctor, 2 waiting with vitals, 1 no-show, rest booked) and cancels every outbox row it caused — nothing is sent.
