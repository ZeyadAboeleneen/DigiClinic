# Security review — Phase 9 (against docs/plan/04-auth-permissions-security.md)

Date: 2026-10-04 · Scope: everything up to `phase-8`. ✅ done & tested · 🔧 fixed in Phase 9 · ⏳ later (by plan) · ⚠️ needs a decision

## 4.1 Authentication
| Item | Status | Where / test |
|---|---|---|
| Session auth, Argon2, invites (48 h), password reset | ✅ | inherited; `tests/test_auth.py`, `test_invitations.py` |
| django-axes 5 attempts / 15 min | ✅ | `tests/test_auth.py` |
| Session timeout 8 h of inactivity | 🔧 (was 12 h, fixed in Phase 6) | `SESSION_COOKIE_AGE`, `SESSION_SAVE_EVERY_REQUEST` |
| Doctor screen locks after 15 idle min, password to unlock | ✅ server-enforced (session flag, HTTP 423) | `test_idle_lock_is_enforced_server_side` |
| 2FA (TOTP) for owner/admin | ⏳ "مرحلة لاحقة" in the plan | — |

## 4.3 Permission matrix
| Item | Status | Where / test |
|---|---|---|
| Every view has `@require_perm` (templates only hide) | ✅ | each app's view tests |
| Reception gets 403 on every clinical URL | ✅ walks `apps/clinical/urls.py` automatically | `test_reception_gets_403_on_every_clinical_url` |
| Reception sees allergies, not diagnoses/prescriptions | ✅ | `test_reception_cannot_write_and_reprints_only_when_allowed` (numbers only) |
| `prescription.print` ⚙ for reception via setting | ✅ | same |
| Admin: settings/users, no medical file, no finance | ✅ | `test_dashboard_by_role_and_reports_permission` |
| Tenant isolation test for every new view | ✅ | `*_tenant_scoped` tests in every phase |

## 4.4 Medical-data privacy
| Item | Status | Where / test |
|---|---|---|
| Messaging consent + date + **who recorded it** | 🔧 `Patient.consent_recorded_by` added; withdrawing consent clears date/recorder | `test_consent_records_who_and_resets_when_withdrawn` |
| Audit of opening the medical file (1 per user×patient×hour) | ✅ (+ prints and exports audited) | `test_opening_a_file_is_audited_once_per_hour` |
| No hard delete of medical records; simple-history | ✅ Visit/Vitals/Attachment/Prescription/Items; attachments archived, Rx voided | model tests |
| Patient data export (PDF summary, owner/doctor) | 🔧 added: `/desk/patients/<pk>/export.pdf` | `test_patient_export_pdf_permissions` |
| Private files only through permission-checked views | ✅ logos, attachments, prescription PDFs | attachment/PDF permission tests |
| Logs: masked phones, no medical text, no message bodies | ✅ reviewed every logger call (only ids, events, masked numbers) | — |
| Outgoing messages contain no diagnosis | ✅ default templates; prescription PDF only when enabled | `seed_notifications` |
| Voice dictation (Google) can be switched off | ✅ setting; script not even loaded when off | `test_mic_script_only_when_voice_is_enabled` |
| Encrypted backups | 🔧 added (Fernet, `BACKUP_KEY`) | `test_backup_file_is_encrypted_and_pruned` |
| Legal review (Law 151/2020) before commercial sale | ⚠️ owner's action, not code | — |

## 4.5 Checklist
| Item | Status | Where / test |
|---|---|---|
| CSP (local scripts only) | 🔧 added `ContentSecurityPolicyMiddleware`: `default-src 'self'`, `object-src 'none'`, `frame-ancestors 'self'`, `form-action 'self'`. Still needs `'unsafe-inline'`/`'unsafe-eval'` for inline scripts + Alpine — moving inline scripts to files + the Alpine CSP build would remove those (follow-up). | `test_security_headers_and_same_origin_print_frame` |
| X-Frame-Options | 🔧 `SAMEORIGIN` (prod had `DENY`, which would have silently broken the prescription print iframe) | same |
| HTTPS/HSTS/secure cookies | ✅ `config/settings/prod.py` (applies in Phase 10) | — |
| CSRF with HTMX | ✅ `hx-headers` in `base_page.html`; sendBeacon/form posts carry the token | `test_tab_closed_mid_typing_text_survives` |
| Fernet for DB-stored secrets | ✅ `apps/core/crypto.py` | `test_messaging.py` |
| Uploads: PDF/JPG/PNG, 15 MB, content-checked, random names | ✅ | `test_upload_checks_content_not_extension`, `test_upload_size_limit` |
| Webhook HMAC, inbound stored as text only | ✅ | `test_whatsapp.py`, `test_inbound_message_is_stored_and_linked_to_patient` |
| Rate limit patient search 120/min/user | 🔧 added on all 4 search endpoints (`apps/core/ratelimit.py`) | `test_patient_search_is_rate_limited` |
| Scheduler: advisory lock, per-row organization | ✅ | `run_scheduler`, dispatcher uses `row.organization` |
| Services on 127.0.0.1 only; UFW | ✅ local (gateway binds 127.0.0.1); UFW is Phase 10 | — |

## Performance (N+1)
Reception screen and doctor desk (visit page, queue, history, record, prescriptions tab): query counts are identical
for 3 vs 15 appointments and 2 vs 10 past visits — `tests/test_performance.py`.

## Open follow-ups (not blocking the pilot)
1. Remove `'unsafe-inline'`/`'unsafe-eval'` from the CSP (move inline scripts to static files, Alpine CSP build).
2. 2FA for owner/admin (plan: later phase).
3. Owner e-mail alert when WhatsApp is disconnected / the scheduler stops > 15 min (07 §7.4 — needs a server-side
   watcher, i.e. Phase 10 deployment).
