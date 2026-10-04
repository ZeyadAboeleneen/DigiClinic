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

## Clinical notes (Phase 6)
- `clinical`: `Visit`, `Vitals` (from Phase 5) + `Attachment` (private storage under
  `org_<id>/patients/<file_number>/<random>.<ext>`, type checked by **content** — `%PDF-` header or Pillow
  JPEG/PNG — max 15 MB, served only via `clinical:attachment_file`; never deleted, `is_archived` instead).
  `Visit.followup_handled` drives reception's "إعادات محتاجة حجز" task (`services.followups_due()`; auto-handled
  once the patient has a booking created after the visit finished, or dismissed by reception).
- `clinical.services`: `start_visit` (call in / reopen; always re-reads the appointment), `call_next`,
  `save_field` (autosave whitelist: text fields, `followup_after_days`, `cf_<key>` visit custom fields;
  `diagnosis` also fills `diagnosis_tags`, split on `، , ; newline`), `finish_visit` (→ appointment `completed`;
  **Phase 7 plugs prescription sending/the draft check in here**), `diagnosis_suggestions`, `add_attachment`,
  `log_clinical_view` (one audit event per user×patient×hour).
- Doctor desk `/desk/` (`clinical.view`; edits `clinical.edit`): queue sidebar (polls every 10 s), visit page with
  tabs (current visit / history with weight & BP sparklines / attachments / data). `/desk/` auto-opens the patient
  currently `in_consultation` (creating the Visit if reception called them in). Any patient's file:
  `/desk/patients/<pk>/` (search box in the sidebar, or "الملف الطبي" on the patient page).
- Autosave: per field, 1 s debounce + on change/blur, `navigator.sendBeacon` flush on `pagehide`/hidden, **plus a
  localStorage draft per field** re-applied on load until the server confirms — closing the tab mid-sentence loses
  nothing (an immediate reload could otherwise race the beacon).
- Idle lock (04 §4.1, changed from 15 min to **6 h** at the clinic's request; `DESK_IDLE_LOCK_MINUTES`): JS posts `/desk/lock/` after that many idle minutes; the `desk_unlocked` decorator then serves the
  lock page (HTTP 423) for every desk view until `/desk/unlock/` gets the user's password. Session age is now 8 h.
- Permissions: `clinical.view`/`clinical.edit` (doctor/owner), `attachment.upload` (reception/doctor/owner —
  reception uploads from the patient page but can't list/view attachments). `test_reception_gets_403_on_every_
  clinical_url` walks `apps/clinical/urls.py` automatically, so new clinical URLs are covered by default.
- Custom fields (`PatientFieldDefinition`): settings tab "ملف المريض" (`/org/settings/patient-fields/`,
  settings.manage; fields are switched off, never deleted). `apps/patients/custom_fields.py` builds form fields and
  JSON values; `PatientForm(org=...)` adds scope=patient fields (quick-add in booking omits them on purpose).
- Reception's "خلص" button from Phase 5 is still there (useful when the doctor doesn't use the desk).

## Prescriptions notes (Phase 7)
- `apps/prescriptions`: `Drug`, `DosePhrase`, `PrescriptionSettings` (one per org: page size, print mode, pre-printed
  margins, voice on/off, `reception_can_reprint`), `PrescriptionSequence` (org+year, `RX-2026-00042`), `Prescription`
  (+ `PrescriptionItem`, simple-history), `PrescriptionTemplate(+Item)`. "Send after visit" + channel live on
  `NotificationSettings` (already there from Phase 4) but are edited on the Rx settings tab.
- Lifecycle (`services.py`): draft (editable) → `finalize()` (number, patient/doctor **snapshots**, immutable; every
  warning key must be in `acknowledged`; any `needs_review` line blocks — Phase 8 sets it for dictated lines) →
  `revise()` = new draft `-R1` sharing the number (original untouched) → `void()` with reason. Never deleted.
  `finish_visit()` refuses while a draft with lines exists, and (if enabled) calls `notifications.send_prescription`.
- Safety (`safety.py`): allergy text vs trade/generic/Arabic aliases + a small drug-class table (`DRUG_CLASSES`:
  Penicillin/بنسلين → amoxicillin, flucloxacillin…; sulfa, NSAIDs, macrolides, quinolones) + duplicate lines. It's a
  safety net — extend `DRUG_CLASSES` rather than adding ad-hoc checks.
- PDF: `apps/documents/pdf.render_pdf()` keeps **one warm Chromium on a dedicated thread** (Playwright's sync API is
  thread-bound; Django serves from many threads). `warm_up()` is called when the Rx tab opens (skipped when
  `settings.TESTING`). `full` PDF is rendered once at finalize (`ensure_pdf`, on_commit, never raises) into
  `pdf_full`; `preprinted` is rendered on the fly for printing; patients always get `full`. Template:
  `templates/pdf/prescription.html` — every mixed-direction run is an isolated `dir` span (05 §5.3).
  `render_rx_samples` writes `tmp/rx-sample-{full,preprinted}.pdf` + `tmp/rx-calibration.pdf`.
- Builder (desk → "الروشتة" tab): search (Arabic aliases too) — **Enter adds the best catalog match** (server-side,
  so it works even before the dropdown arrives); "+ إضافة" (`free=1`) adds free text; cells autosave; dose phrases
  insert into the last focused cell; finalize returns the builder + `printRx()` (hidden iframe → print dialog).
- Sending: `ScheduledMessage.prescription` FK; the dispatcher attaches the stored PDF (`send_document` on WhatsApp,
  email attachment) when the template has `attach_pdf`. Dedupe `rx:<pk>:<channel>`.
- Permissions: `prescription.write`/`print`, `drug.manage`, `rx_template.manage` (doctor/owner). Reception can
  reprint (numbers/dates only, no drug names) only with `reception_can_reprint`.
- `import_drugs [csv] [--update]` (run by `run.bat`): create-only unless `--update`; seeds dose phrases once.
- Local env gotcha found here: `.env` had `MEDIA_ROOT=C:/Users/zeyad/...` from another machine, so no upload/PDF could
  be stored. `MEDIA_ROOT` now defaults to `%LOCALAPPDATA%\digiclinic\media`; leave it empty in `.env`.

## Dictation notes (Phase 8)
- `static/src/dictation.js` (served as `src/dictation.js`, no build step): any field with `data-dictate` gets a 🎤 +
  ع/EN toggle (language remembered in localStorage). Click / Ctrl+Space toggles; 3 s silence stops; interim text grey
  under the field; finals inserted **at the cursor** and fire `input` (so the field's autosave runs). Commands:
  "سطر جديد" → `\n`, "نقطة" → `.`. Provider interface (`WebSpeechProvider` now; a Whisper provider can be dropped in).
  Unsupported browser (Firefox/Safari, or non-secure origin) → no mic, and `[data-dictation-unsupported]` is shown.
- Loaded only on desk pages and only when `PrescriptionSettings.voice_dictation_enabled` (`voice_enabled` in the
  desk context). HTMX-loaded content is scanned on `htmx:afterSettle`.
- Prescription mode (`data-dictate="rx"` on the drug search box): finals are collected (each pause = a line) and POSTed
  to `prescriptions:dictate` → `matching.match_text()` → top-3 suggestions per line in `#rx-dictation` (outside
  `#rx-builder`, so picking one line keeps the rest). Picking → `dictate_add` adds the line with **`needs_review=True`**
  (finalize stays blocked until "✓ تأكيد") and `learn_alias()` stores the spoken form on the drug.
- `apps/prescriptions/matching.py`: normalize (Arabic + digits) → best of the first 1–3 words vs name/generic/aliases
  with rapidfuzz `WRatio` (threshold 80), ties → longer match, then `usage_count`. The rest of the line becomes the
  instructions with spoken numbers → digits; a spoken strength ("واحد جرام", or a bare number equal to the drug's own
  strength) is dropped from the instructions.
- JS is tested in real headless Chromium from pytest (`tests/test_dictation.py`) with a fake `SpeechRecognition`. Note:
  newer Chromium has an unprefixed `SpeechRecognition` too, and Playwright must run in a worker thread (its event loop
  trips Django's async-safety check otherwise).

## Polish notes (Phase 9)
- Dashboard (`apps/dashboard/services.py`): `day_counts`, `report(org, start, end)` (visits, new patients, no-show
  rate, revenue by method net of refunds, top diagnosis tags, reminder delivered/read rates from `Delivery` acks,
  per-day table) — aggregate queries only. Home is role-based; `/reports/` needs `report.finance` (doctor/owner).
- Backups: `manage.py backup` / `restore <file> --database NEW --media-root EMPTY [--create-db] [--check]`
  (`apps/core/backup.py`): pg_dump -Fc + private media → tar.gz → Fernet (`BACKUP_KEY`, default
  `FIELD_ENCRYPTION_KEY` — losing that key loses every backup). Restore **only into an empty DB and an empty media
  folder**. `run_scheduler` makes one backup per day (hourly check, keeps 14; `AUTO_BACKUP=False` turns it off).
  Postgres client binaries: `PG_BIN` → pgserver's bundled ones → PATH.
- Security fixes from the review (`docs/security-review.md`): CSP + Referrer/Permissions-Policy middleware,
  `X_FRAME_OPTIONS = "SAMEORIGIN"` (DENY breaks the print iframe), per-user rate limit on the 4 patient-search
  endpoints (`apps/core/ratelimit.py`, 120/min; decorated views carry `ratelimit_key`), `Patient.consent_recorded_by`,
  patient export PDF (`clinical:export`, audited).
- Patient page now shows bookings, payments (payment.record) and sent + inbound messages (messages.view).
- N+1 guard: `tests/test_performance.py` compares warm query counts at small vs large data — keep it green when
  touching the reception/desk screens.
- Week calendar (03 §3.3, added after Phase 9): `/appointments/week/?date=` (Saturday→Friday, `views.week_start`),
  day/week toggle on both views. Each day shows its working periods (`periods_for`) or "مفيش شغل", exceptions with
  reason, bookings colored by `VisitType.color` + status icon. Drag a booked/confirmed card onto a future day (native
  HTML5 DnD) or press "تأجيل" (phones) → `reschedule_options` dialog with that day's free slots / queue periods →
  `reschedule_confirm` → `services.reschedule()` (so the patient gets the "rescheduled" message and old reminders are
  cancelled). "قفل" per day (`close_day`, schedule.manage) → closed exception → affected-bookings screen if needed.

## Deployment notes (Phase 10 — VPS, prepared; not yet run on the server)
- `deploy/`: `Dockerfile` (web + scheduler image: Python 3.12, uv, Postgres 16 client for backups, Playwright
  Chromium for PDFs, non-root, gunicorn gthread 2×4), `whatsapp.Dockerfile` (Node 20 + Debian chromium),
  `compose.yml` (db/web/scheduler/whatsapp; only web published, on 127.0.0.1:${WEB_PORT}), `env.production.example`,
  `nginx/digiclinic.conf` (new site file only, certbot `certonly --webroot`), `cron` (health alerts every 5 min +
  off-site copy), `offsite-backup.sh` (rclone; files are already encrypted), `README.md` (the runbook — review first).
- App changes for it: `/healthz/` (db + scheduler, no data), `manage.py check_health [--alert]` (owner e-mail, once per
  problem per 6 h via `ops.alert` audit events; run from host cron so it works when the scheduler is down),
  `SECURE_REDIRECT_EXEMPT` for the internal webhook + healthz, `EMAIL_URL`, `SITE_URL`, gateway `WA_GATEWAY_HOST`,
  gunicorn dependency. Upload images are shrunk to 2000 px / JPEG 85 (07 §7.2) in `clinical.services.shrink_image`.
- Docker isn't available on the dev machine: the images have not been built here. First build happens on the server.
