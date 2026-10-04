# 08 — Roadmap (Phases)

كل مرحلة ليها **Definition of Done**. مفيش انتقال قبل ما الـDoD يتحقق وتراجعيه.
الحجم: S = يوم تقريبًا · M = 2–4 أيام · L = أسبوع.
كل المراحل من 0 لـ9 local. النشر (10) بعد قرار الاستضافة.

---

### Phase 0 — Fork & Cleanup · M
- نقل `docs/plan/CLAUDE.md` لجذر الـrepo (مكان بتاع البرق). مسح ملفات الخطة القديمة الزيادة
  (`05-pdf-generation.md`, `06-sending-email-whatsapp.md`, `seed/products.csv`, `seed/brand.json`, `assets/*` بتاعة البرق).
- مسح `apps/quotations`, `apps/catalog`, `apps/customers` + templates + tests بتاعتهم + `tmp/` القديم.
  `messaging`: فك `Delivery` من `Quotation`، مسح `QuotationReview` وشاشة الإرسال الخاصة بالعروض (الإعدادات والبوابة يفضلوا).
- `OrganizationSettings`: مسح حقول العروض وإضافة حقول العيادة (02 §2.1). الأدوار الجديدة + المصفوفة (04).
- **Migrations من الأول** (مشروع جديد، مفيش بيانات حقيقية): مسح migrations القديمة وعمل initial جديدة.
- البورتات والمجلدات (01 §1.6) في `run.bat`, `scripts/devdb.py`, `.env.example`, `whatsapp-gateway/server.js`, settings.
- الاسم في كل الواجهة: DigiClinic. `seed_org` من `seed/clinic.json`.
- **git**: الـrepo الحالي فيه تاريخ البرق → commit "phase-0: fork from marsool-albarq".

**DoD:** `run.bat` يشغّل DigiClinic على 8010 **والبرق شغال في نفس الوقت على 8000 من غير تعارض** · الدخول/الخروج/الدعوات شغالين ·
مفيش أي ذكر لعروض الأسعار/البرق في الكود أو الواجهة (`grep -ri "quotation\|albarq\|البرق"` نضيف ما عدا docs) · الـtests الباقية خضرا.

---

### Phase 1 — Clinic Setup & Doctor Schedule · M
- `doctors`: Doctor, WorkingPeriod, ScheduleException, VisitType + شاشات الإعدادات (تبويب العيادة، الدكتور والمواعيد، أنواع الزيارات).
- `scheduling/availability.periods_for()` + tests (12 §1).
- `core/timeutils.py`.

**DoD:** إدخال جدول "كل الأيام ما عدا الخميس والجمعة من 2 لـ9 مساءً" من الواجهة في أقل من دقيقتين · إجازة يوم معين بتقفله ·
tests الـ12 §1 كلها خضرا (بما فيها يوم التوقيت الصيفي).

---

### Phase 2 — Patients · M
- `patients`: Patient, Allergy, ChronicCondition, PatientFieldDefinition, رقم الملف المسلسل.
- البحث (الرقم بأي صيغة 010…/+2010…/2010…، الاسم المتطبّع، رقم الملف) + قائمة + صفحة المريض + إضافة سريعة + تحذير التكرار + الدمج.
- `import_patients` + `seed_demo` (30 مريض وهمي، منهم عيلة على نفس الرقم).

**DoD:** البحث بـ"0100 123" و"محمد احمد" (من غير همزة) بيلاقي المريض · رقم واحد عليه 3 مرضى بيعرض التلاتة ·
رقمين ملف في نفس اللحظة مايتكرروش (test) · سكرتيرة مش شايفة أي بيانات طبية (test).

---

### Phase 3 — Booking Engine & Booking Screen · L (أهم مرحلة)
- `scheduling`: Appointment, DayLedger, AppointmentEvent + `free_slots`, `queue_availability`, `book`, `reschedule`, `cancel`,
  `transition` بالـtests كلها في 12.
- شاشة الحجز (03 §3.2) + التقويم (03 §3.3) + شاشة الحجوزات المتأثرة لما يوم يتقفل.
- الإعادة المجانية والأسعار.

**DoD:** حجز مريض قديم في < 20 ثانية والجديد في < 45 (بالكيبورد) · test الحجز المتزامن (threads) بينجح واحد بس ·
تحويل الدكتور من slots لـqueue من الإعدادات والشاشة بتتغير صح · قفل يوم فيه 5 حجوزات بيعرضهم بمواعيد مقترحة.

---

### Phase 4 — Notifications Engine · L
- `notifications`: NotificationSettings, NotificationTemplate, ScheduledMessage + `schedule_for/on_rescheduled/on_cancelled` +
  dispatcher + `run_scheduler` (+ advisory lock + heartbeat) + ساعات الهدوء + الـfreshness guard.
- `messaging`: Delivery مربوط بـScheduledMessage، البوابة: بورت/مجلد من env، `isRegisteredUser`، forward للرسايل الواردة (تخزين بس).
- شاشة القوالب (Preview حي + ابعت تجربة) + شاشة سجل الرسايل + `seed_notifications`.

**DoD:** حجز لبكرة الساعة 4 → تأكيد يوصل على واتسابك فورًا + الصفوف المجدولة ظاهرة بأوقاتها (24 ساعة، ساعة) ·
تأجيل الحجز → التذكيرات القديمة `cancelled` ورسالة التعديل وصلت والجديدة اتجدولت · test: قفل الـscheduler 3 ساعات وتشغيله
(بـfreezegun/time travel) → مفيش "فاضل ساعة" لميعاد عدى · مفيش رسالة اتبعتت مرتين في أي test.

---

### Phase 5 — Reception Screen, No-show & Payments · M
- شاشة الاستقبال (03 §3.4) + polling + walk-in + العلامات الحيوية عند الوصول + undo.
- `mark_no_shows` + سياسات الغياب (12 §7) + رسايلها.
- "دورك قرّب" (06 §6.4).
- `billing`: Payment + تسجيل الدفع + تقرير الخزنة اليومي.

**DoD:** يوم كامل تجريبي (10 مرضى) من الشاشة بالماوس والكيبورد · غياب بيتعمل تلقائي بعد المهلة وإعادة الحجز في نفس الساعة بعد 7 أيام ·
في queue: لما رقم 4 يدخل، رقم 7 (لسه ماوصلش) ياخد "دورك قرّب" (threshold=3) · تقرير الخزنة مطابق للمدفوعات.

---

### Phase 6 — Doctor Desk & Medical File · L
- `clinical`: Visit, Vitals, Attachment.
- شاشة الدكتور (03 §3.5): الطابور، الاستدعاء، الملف، التبويبات، autosave، إنهاء الكشف، طلب حجز إعادة للاستقبال.
- الحقول الإضافية (PatientFieldDefinition) في الـforms.
- audit لفتح الملف + قفل الشاشة بعد الخمول.

**DoD:** الدكتور يكشف 5 مرضى ورا بعض بالشاشة من غير ما يلمس شاشة الاستقبال · بانر الحساسية ظاهر · رفع صورة أشعة من الموبايل ·
قفل التاب في نص الكتابة وفتحه → الكلام موجود · السكرتيرة بتاخد 403 على أي URL طبي (test).

---

### Phase 7 — Prescriptions · L
- `prescriptions`: Drug, DosePhrase, Prescription, PrescriptionItem, PrescriptionTemplate + `import_drugs seed/drugs.csv`.
- شاشة الروشتة (03 §3.6) + تحذير الحساسية والتكرار + القوالب + كرر آخر روشتة + revisions.
- الـPDF بالوضعين + صفحة المعايرة + الطباعة + الإرسال الاختياري بعد الكشف.

**DoD:** روشتة 4 أدوية من قالب + تعديل في < 30 ثانية · الـPDF A5 العربي والإنجليزي سليم (5.3) — حطيه في `tmp/` وقارنيه بعينك ·
الطباعة على ورق مطبوع بعد المعايرة مظبوطة · تفعيل الإرسال → الروشتة توصل واتساب PDF بعد "إنهاء الكشف" · دوا فيه Penicillin لمريض عنده الحساسية → تحذير لازم يتأكد.

---

### Phase 8 — Voice Dictation · M
- `dictation.js` (provider interface + WebSpeech) + الأزرار في كل الحقول + المطابقة مع الكتالوج + التعلم من الاختيار (13).

**DoD:** الدكتور يملي روشتة 3 أدوية بالعربي والأسماء بتتطابق صح (أو تظهر في أول 3 اقتراحات) · الاعتماد مقفول لحد مراجعة كل سطر ·
Firefox: الزرار مش ظاهر.

---

### Phase 9 — Dashboard, Reports, Polish & Local Pilot · M
- الرئيسية لكل دور + التقارير (03 §3.9) + تحذيرات الـscheduler/الواتساب.
- مراجعة موبايل 375px لشاشة الدكتور والمواعيد.
- `backup`/`restore` + تجربة استرجاع.
- مراجعة أمنية كاملة ضد 04 + مراجعة الأداء (django-debug-toolbar: مفيش N+1 في الاستقبال والدكتور).
- Pilot: أسبوع تجربة بعيادة حقيقية أو محاكاة كاملة (ممرضة + دكتور على جهازين).

**DoD:** يوم عيادة كامل (حجز → تذكيرات → وصول → كشف → روشتة → دفع → غياب) من غير أي تدخل تقني · backup اتعمل واتسترجع على داتابيز فاضية.

---

### Phase 10 — Deployment · M (بعد قرار الاستضافة — 07 §7.6)
- **VPS**: compose (web, scheduler, whatsapp, db) + Nginx block + SSL + backups off-site متشفرة + مراقبة.
- **جهاز العيادة**: تشغيل مع Windows + HTTPS محلي + backup سحابي + خطوات UPS.

**DoD:** النظام شغال في المكان النهائي، رسالة تذكير حقيقية وصلت من هناك، backup اتعمل واتسترجع، والمشاريع التانية ما اتأثرتش.
