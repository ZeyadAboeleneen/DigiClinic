# HANDOFF — DigiClinic

آخر تحديث: 2026-10-04 · آخر كوميت: `c1f9d28` (phase-3)

## ابدأ من هنا
1. اقرأ `CLAUDE.md` في جذر الريبو — ده تعليمات التنفيذ الحية، وفيه قسم "notes" في الآخر لكل phase خلصت (gotchas وconventions حقيقية من التنفيذ، مش مجرد خطة).
2. اقرأ `docs/plan/README.md` ثم باقي ملفات `docs/plan/` بالترتيب المكتوب فيه. ده الخطة الكاملة (source of truth) ومفيهاش تغيير عن النسخة الأصلية.
3. `docs/plan/08-roadmap.md` فيه كل الـphases والـDefinition of Done بتاعة كل واحدة.

## إيه اللي خلص (Phases 0–3)
| Phase | الموضوع | الكوميت |
|---|---|---|
| 0 | Fork & Cleanup من مرسول البرق | `53f82a9` |
| 1 | Clinic Setup & Doctor Schedule | `7cb0efe` |
| 2 | Patients | `768072c` |
| 3 | Booking Engine & Booking Screen | `c1f9d28` |

كل phase ليها قسم "notes" في `CLAUDE.md` (مثلاً "Doctors notes (Phase 1)"، "Patients notes (Phase 2)"، "Scheduling notes (Phase 3)") فيه تفاصيل تنفيذ مهمة مش موجودة في الخطة الأصلية — اقرأها قبل ما تلمس أي حاجة في الـapps دي.

**الـapps الموجودة دلوقتي:** `core`, `accounts`, `organizations`, `audit`, `messaging` (بنية تحتية بس — القوالب الفعلية جاية في Phase 4), `documents` (Chromium PDF infra بس), `dashboard`, `doctors`, `scheduling`, `patients`.

**مش موجود لسه:** `notifications`, `clinical`, `prescriptions`, `billing`, `reception`, `doctor_desk` — دول Phases 4–8.

## إيه اللي لسه (Phases 4–10)
راجع `docs/plan/08-roadmap.md` بالتفصيل. ملخص سريع:
- ~~Phase 4 — Notifications Engine~~ ✅ (راجع "Notifications notes (Phase 4)" في CLAUDE.md)
- Phase 5 — Reception Screen, No-show & Payments
- Phase 6 — Doctor Desk & Medical File
- Phase 7 — Prescriptions
- Phase 8 — Voice Dictation
- Phase 9 — Dashboard, Reports, Polish & Local Pilot
- Phase 10 — Deployment (بعد قرار الاستضافة — لسه مفتوح، راجع `09-open-questions.md`)

## قرار محسوم بعيد عن الخطة الأصلية (موثّق في CLAUDE.md)
`Appointment.period_key` (CharField) بدل FK لـ`WorkingPeriod` زي ما كانت `02-database-schema.md` مفترضة — لأن فترات الـqueue ممكن تيجي من `ScheduleException` (custom/extra) مالهاش صف `WorkingPeriod` أصلاً. التفاصيل في قسم "Scheduling notes (Phase 3)" في `CLAUDE.md`.

## تشغيل المشروع محليًا
```
run.bat
```
بيعمل: `uv sync` → Postgres embedded (بورت 54339) → Tailwind build → migrate → `seed_org` → يفتح http://127.0.0.1:8010

**مهم:** مرسول البرق (لو موجود على نفس الجهاز) بيشتغل على بورتات مختلفة تمامًا (8000/54329/3310) — الاتنين يقدروا يشتغلوا سوا من غير تعارض.

### مستخدمين تجربة
```
.venv\Scripts\python manage.py create_user --email you@example.com --name "الاسم" --role owner
```
الأدوار: `owner` · `admin` · `doctor` · `reception` · `viewer`.

### بيانات تجربة
```
.venv\Scripts\python manage.py seed_demo          # 30 مريض وهمي (منهم 3 على نفس الرقم)
.venv\Scripts\python manage.py seed_demo --remove # مسحهم تاني
```
لو هتمسح مرضى الـseed وعندهم حجوزات مرتبطة، امسح الحجوزات (`Appointment`) الأول — `Patient.appointments` علاقته `PROTECT`.

## الفحوصات
```
.venv\Scripts\python -m pytest     # 126 test، كلهم شغالين
.venv\Scripts\ruff check .
.venv\Scripts\ruff format --check .
```
الاختبارات اللي محتاجة وقت مجمّد (DST، "النهارده الساعة كذا") بتستخدم `freezegun` (dev dependency). اختبارات الـconcurrency (مثلاً حجز الميعاد نفسه من اتنين في نفس اللحظة) لازم تتعلّم بـ `@pytest.mark.django_db(transaction=True)` — من غيرها الـthreads التانية مش هتشوف الداتا لسه متعملهاش commit.

## قواعد العمل (من CLAUDE.md)
- مرحلة واحدة في المرة. في الآخر: quality gates (ruff + pytest) + تقرير مقابل الـDoD بتاعة المرحلة + خطوات تجربة يدوية + كوميت بصيغة `phase-N: <summary>` + **push** + وقف واستنى تأكيد.
- أي قرار مش موجود في الخطة: اتسأل، متتغيرش القرارات المحسومة في `docs/plan/README.md` بصمت.
- كل موديل تشغيلي بيورث `TenantScopedModel`، والـviews بتستخدم `.for_org(request.organization)` — مفيش `Model.objects.all()` في أي view. كل view جديدة لازم ليها cross-tenant test.
- الصلاحيات عن طريق `@require_perm(...)` في الـviews (`apps/accounts/permissions.py`)، مش في الـtemplates بس.
- الـbusiness logic في `services.py` لكل app، والـviews رفيعة.

## أسرار / إعدادات
`.env` (مش متعمله commit) فيه `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, `DATABASE_URL`, `WA_GATEWAY_KEY`. فيه `.env.example` كـmarker للمتغيرات المطلوبة.
