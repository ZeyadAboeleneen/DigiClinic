# 01 — System Architecture

## 1.1 ليه نبني على مرسول البرق

مرسول البرق فيه أصعب الأجزاء اللي محتاجينها، ومتجربة:
multi-tenancy، auth بالدعوات والأدوار، audit، تشفير الإعدادات، PDF عربي بـChromium، بوابة واتساب محلية
بـacks (✓✓)، SMTP بـretries، قوالب رسايل بمتغيرات، بحث عربي متطبّع، تشغيل كامل على Windows من غير Docker.
الجديد في DigiClinic هو **منطق العيادة** (المواعيد، الطابور، الملف الطبي، الروشتة) و**محرك جدولة الرسايل**.

## 1.2 خريطة الـReuse

| App في البرق | في DigiClinic | التغيير |
|---|---|---|
| `core` | يفضل | + `apps/core/timeutils.py` (تحويلات القاهرة ↔ UTC)، + `normalize_ar` يتنقل هنا لو مش موجود |
| `organizations` | يفضل | `OrganizationSettings` تتنضف من حقول العروض وتتحول لبيانات العيادة (02) |
| `accounts` | يفضل | الأدوار الجديدة: `owner`, `admin`, `doctor`, `reception`, `viewer` + مصفوفة صلاحيات جديدة (04) |
| `audit` | يفضل | + أحداث العيادة + تسجيل فتح الملف الطبي |
| `messaging` | يفضل | `Delivery` تتفك من `Quotation` وتتربط بـ`ScheduledMessage`. الـproviders والبوابة زي ما هما |
| `documents` | يفضل | `pdf.py` (Chromium) يفضل، والـtemplates تبقى روشتة بدل عرض سعر |
| `customers` | **يتشال** | المنطق المفيد (الأرقام E.164، البحث المتطبّع، الـforms) يتنقل لـ`patients` |
| `catalog` | **يتشال** | نمط الترتيب بالسحب والـimport يتنقل لكتالوج الأدوية في `prescriptions` |
| `quotations` | **يتشال** | نمط الـbuilder بالـHTMX partials يتنقل لشاشة الروشتة. الـsnapshots والقفل والـrevisions نفس الفكرة |
| `dashboard` | يفضل | محتواه يتغير (إحصائيات العيادة) |

## 1.3 الـApps الجديدة

```
apps/
├── core/           (موجود)
├── organizations/  (موجود) بيانات العيادة والبراندنج
├── accounts/       (موجود) المستخدمين والأدوار
├── audit/          (موجود)
├── messaging/      (موجود) providers + بوابة الواتساب + Delivery
├── documents/      (موجود) Chromium PDF
├── doctors/        Doctor, WorkingPeriod, ScheduleException, VisitType
├── patients/       Patient, Allergy, ChronicCondition, PatientFieldDefinition, sequence رقم الملف
├── scheduling/     Appointment, AppointmentEvent, DayLedger, services: availability/book/reschedule/cancel/no-show
├── notifications/  NotificationSettings, NotificationTemplate, ScheduledMessage, dispatcher, run_scheduler
├── clinical/       Visit (الكشف)، Vitals، Attachment
├── prescriptions/  Drug, DosePhrase, Prescription, PrescriptionItem, PrescriptionTemplate
├── billing/        Payment + التقرير اليومي
├── reception/      views شاشة الاستقبال (من غير models)
├── doctor_desk/    views شاشة الدكتور (من غير models)
└── dashboard/      (موجود) إحصائيات
```

قاعدة: **كل business logic في `services.py` جوه كل app**، والـviews رفيعة (زي البرق). الـservices هي اللي بتتختبر.

## 1.4 شكل النظام (Runtime)

```
Browser (السكرتيرة / الدكتور)
   │  HTMX (polling كل 5 ثواني لشاشات الاستقبال والدكتور)
   ▼
Django (runserver local / gunicorn على السيرفر)
   │                               ▲
   │ ScheduledMessage (outbox)     │ webhook (acks + الرسايل الواردة)
   ▼                               │
run_scheduler  ──send──►  whatsapp-gateway (Node, 127.0.0.1)  ──► WhatsApp
   │          ──send──►  SMTP
   └─ jobs دورية: إرسال المستحق، الغياب التلقائي، تنظيف
PostgreSQL 16
```

- **مفيش Celery ولا Redis** في النسخة الأولى. `manage.py run_scheduler` عملية واحدة بتصحى كل 20 ثانية:
  بتبعت الرسايل المستحقة (`SELECT ... FOR UPDATE SKIP LOCKED`)، وبتشغّل jobs الغياب. نفس الأمر local وعلى السيرفر
  (container لوحده). ده أبسط وأسهل في الصيانة، وكفاية جدًا لحجم عيادات.
- "ابعت دلوقتي" (زي الروشتة أو تأكيد الحجز): بيتعمل `ScheduledMessage` بـ`send_at=now` + بيتعمل kick فوري
  في thread (زي البرق) عشان ما يستناش الـ20 ثانية. لو الـthread فشل، الـscheduler بيلقطها.
- الـrealtime بين الاستقبال والدكتور: **HTMX polling** (كل 5 ثواني، والـresponse بيرجع `204` لو مفيش تغيير
  باستخدام `ETag`/version رقم). مفيش WebSockets ولا Channels.

## 1.5 Multi-Tenancy (زي البرق بالظبط)

- كل model تشغيلي بيورث `TenantScopedModel`. الـviews بتستخدم `.for_org(request.organization)`.
- Test عزل إجباري لكل view جديدة (مستخدم من عيادة A ميشوفش أي حاجة من B بالـID في الـURL).
- البراندنج والأرقام والعنوان ولينك الخريطة وبيانات الروشتة كلها من بيانات الـOrganization، مش hardcoded.

## 1.6 تعايش DigiClinic مع مرسول البرق على نفس الجهاز

المشروعين ممكن يشتغلوا على نفس اللابتوب، فـ**لازم ميتشاركوش أي حاجة**:

| البند | مرسول البرق | DigiClinic |
|---|---|---|
| مجلد البيانات | `%LOCALAPPDATA%\marsool-albarq` | `%LOCALAPPDATA%\digiclinic` |
| بورت PostgreSQL | 54329 | **54339** |
| اسم الداتابيز | `marsool` | `digiclinic` |
| بورت Django | 8000 | **8010** |
| بورت بوابة الواتساب | 3310 | **3320** |
| جلسة الواتساب | `.../marsool-albarq/whatsapp` | `.../digiclinic/whatsapp` |

## 1.7 Packages جديدة

- `rapidfuzz`: مطابقة أسماء الأدوية التقريبية (الإملاء الصوتي والبحث).
- `python-dateutil`: لو احتجناه في حساب الأيام (اختياري).
- الباقي موجود بالفعل في `pyproject.toml`.
- `celery`/`redis`: **مش هيتضافوا** دلوقتي.
