# 08 — Roadmap (Phases)

كل مرحلة ليها **Definition of Done**. مفيش انتقال للمرحلة اللي بعدها قبل ما الـDoD يتحقق وتراجعيه.
الحجم: S = يوم تقريبًا · M = 2–4 أيام · L = أسبوع.
**كل المراحل من 1 لـ9 بتتنفذ وتتجرب local.** النشر (10) بعد ما تقرري السيرفر والدومين.

---

### Phase 0 — الأصول (قبل Phase 1)
- لوجو البرق بجودة عالية (SVG أو PNG ≥ 1000px بخلفية شفافة) في `docs/plan/assets/`.
- الـPDF المرجعي في `docs/plan/assets/reference-quotation.pdf`.
- باقي المنتجات (الـ30 الناقصين) في `seed/products.csv` (مطلوبة قبل Phase 3 بس).

---

### Phase 1 — Foundation · M
- Django project + docker compose للـdev (web, db, redis, worker).
- `core`: BaseModel, TenantScopedModel, OrgManager, CurrentOrganizationMiddleware.
- `organizations` + `accounts`: Organization, OrganizationSettings (تتملى من `brand.json`), Membership، login/logout، دعوة المستخدمين، axes، argon2.
- Base layout: RTL، Tailwind، HTMX، Alpine، خطوط Cairo/Poppins محلية، sidebar.
- Permissions matrix + decorator.
- pytest + ruff + pre-commit.

**DoD:** `docker compose up` يشغّل كل حاجة، دخول وخروج شغالين، مستخدم sales ميقدرش يفتح الإعدادات، test عزل الـtenants بيعدّي.

---

### Phase 2 — Customers & Contacts · M
- Customer (مع الشجرة parent)، Contact، ContactChannel بـphonenumbers (افتراضي مصر).
- List + بحث عربي + صفحة العميل + إضافة/تعديل inline للمسؤولين والقنوات.
- simple-history.

**DoD:** إضافة Travco → Jaz Hotels بـ3 مسؤولين (Ahmed: WhatsApp+Email، Mohamed: WhatsApp، Sara: Email) في أقل من دقيقتين، والأرقام متخزنة E.164.

---

### Phase 3 — Catalog · S–M
- Category, Unit, Product + شاشة المنتجات (ترتيب بالسحب، تعديل inline، تعطيل).
- `import_products` command: **upsert** بالاسم والقسم (ينفع يتشغل تاني لما منتجات جديدة تتضاف من غير تكرار).
- بحث عربي مع توحيد الحروف.

**DoD:** كل المنتجات في `products.csv` مستوردة بنفس ترتيب العرض المرجعي، تشغيل الـimport مرتين ميكررش حاجة، البحث بـ"معلقه" يلاقي "معلقة".

---

### Phase 4 — Quotation Builder · L (أهم مرحلة)
- Quotation, QuotationItem, QuoteSequence (بالسنة والشهر).
- شاشة البناء الواحدة: keyboard flow، ذاكرة الأسعار، نسخ آخر عرض للعميل، إضافة قسم كامل، صنف خاص، autosave.
- قائمة العروض + فلاتر.
- الإصدار: ترقيم آمن مع التزامن، snapshots، قفل، Revisions.

**DoD:** عرض بـ15 صنف لعميل موجود في أقل من دقيقتين. إصدار عرضين في نفس اللحظة ميدّيش نفس الرقم (test). أول عرض في أكتوبر رقمه `AB-2026/10-01`. العرض بعد الإصدار مش قابل للتعديل.

---

### Phase 5 — PDF · M
- Template مطابق للعرض المرجعي (4 أعمدة: م · الصنف · الوحدة · السعر) بألوان وخطوط الـGuideline.
- Preview بعلامة "مسودة"، توليد نهائي وتخزين، تحميل بصلاحيات.

**DoD:** مقارنة جنب لجنب مع الـPDF المرجعي وموافقتك. عرض بكل المنتجات بيتقسم على الصفحات صح (عناوين الأعمدة بتتكرر، مفيش عنوان قسم لوحده آخر الصفحة).

---

### Phase 6 — Review Gate + Email Sending · M
- شاشة Preview/Send + بوابة المراجعة الإجبارية (reviewed_by + checkbox + تحقق في الـbackend).
- SMTP provider، الإعدادات المتشفرة، زرار إيميل تجربة.
- Delivery model، Celery task، retries، live status، قوالب الرسائل.

**DoD:** زرار الإرسال مقفول قبل المراجعة (وطلب POST مباشر بيترفض). عرض بيتبعت بالإيميل ويوصل inbox، والـDelivery بيتسجل.

---

### Phase 7 — WhatsApp (OpenWA) · M
- OpenWA container في الـcompose، OpenWAProvider، ربط QR من الإعدادات، webhooks (status + ack)، health check، rate limit، fallback للإيميل.

**DoD:** ربط `01000967017` من الواجهة بـQR، إرسال PDF لرقم تجربة (رقمك أنتِ) مع ظهور ✓✓، فصل الرقم يظهر تنبيه خلال 5 دقائق.

---

### Phase 8 — Dashboard, Audit, Polish · M
- الصفحة الرئيسية، AuditEvent + شاشة سجل النشاط، انتهاء صلاحية العروض تلقائيًا، مراجعة الموبايل.

**DoD:** الأدمن يجاوب "مين بعت العرض ده ولمين وإمتى" من الواجهة.

---

### Phase 9 — Local Pilot · S
- إدخال بيانات حقيقية (عميلين أو تلاتة)، عمل عروض حقيقية كاملة من الأول للآخر، تسجيل الملاحظات وإصلاحها.
- `seed_demo` command لبيانات تجربة، وسكريبت backup/restore يتجرب local.

**DoD:** 5 عروض حقيقية اتعملت واتبعتت من النظام local من غير مشاكل.

---

### Phase 10 — Production Deployment · M (بعد تحديد السيرفر والدومين)
- Nginx block + SSL + compose prod + backups متشفرة off-site + Sentry + اختبار استرجاع.

**DoD:** النظام شغال على الـsubdomain، backup اتعمل واتسترجع، المشاريع التانية على السيرفر ما اتأثرتش.
