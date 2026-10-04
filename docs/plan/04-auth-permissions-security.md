# 04 — Authentication, Permissions & Medical-Data Security

## 4.1 Authentication (زي البرق)
Session auth · دعوات بلينك 48 ساعة · Argon2 · django-axes (5 محاولات / 15 دقيقة) · نسيت كلمة المرور بالإيميل.
- Session timeout: **8 ساعات** من غير نشاط (يوم عمل)، و**قفل الشاشة** بعد 15 دقيقة خمول على شاشة الدكتور
  (overlay يطلب الباسورد — الجهاز ممكن يفضل مفتوح والمريض قاعد قدامه).
- 2FA (TOTP) للـowner/admin: مرحلة لاحقة.

## 4.2 الأدوار
| الدور | مين |
|---|---|
| `owner` | صاحب العيادة (غالبًا الدكتور) — كل حاجة |
| `admin` | مدير النظام — الإعدادات والمستخدمين، **من غير** الملف الطبي إلا لو doctor برضه |
| `doctor` | الدكتور — الملف الطبي والروشتات وجدوله |
| `reception` | السكرتيرة/الممرضة — الحجز والاستقبال والدفع والعلامات الحيوية |
| `viewer` | مشاهدة المواعيد بس |

## 4.3 مصفوفة الصلاحيات (`apps/accounts/permissions.py`)

| الصلاحية | viewer | reception | doctor | admin | owner |
|---|:-:|:-:|:-:|:-:|:-:|
| `appointment.view` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `appointment.book` / `reschedule` / `cancel` | | ✅ | ✅ | ✅ | ✅ |
| `appointment.overbook` (استثنائي) | | | ✅ | | ✅ |
| `reception.operate` (وصل/دخّل/لم يحضر) | | ✅ | ✅ | | ✅ |
| `patient.view_basic` / `patient.edit_basic` | | ✅ | ✅ | ✅ | ✅ |
| `patient.merge` | | | ✅ | ✅ | ✅ |
| `vitals.record` | | ✅ | ✅ | | ✅ |
| `clinical.view` (الزيارات، التشخيص، المرفقات) | | | ✅ | | ✅ |
| `clinical.edit` | | | ✅ | | ✅ |
| `attachment.upload` | | ✅ | ✅ | | ✅ |
| `prescription.write` | | | ✅ | | ✅ |
| `prescription.print` (إعادة طباعة) | | ⚙ | ✅ | | ✅ |
| `payment.record` | | ✅ | ✅ | | ✅ |
| `report.finance` | | | ✅ | | ✅ |
| `messages.view` / `retry` | | ✅ | ✅ | ✅ | ✅ |
| `drug.manage` / `rx_template.manage` | | | ✅ | | ✅ |
| `schedule.manage` (المواعيد والاستثناءات) | | | ✅ | ✅ | ✅ |
| `settings.manage` / `user.manage` | | | | ✅ | ✅ |
| `audit.view` | | | | ✅ | ✅ |

⚙ = بإعداد في "ملف المريض" (`reception_can_reprint_prescriptions`).
السكرتيرة بتشوف **الحساسية** في كارت المريض (أمان)، لكن مش التشخيصات ولا الروشتات.
التنفيذ زي البرق: `@require_perm(...)` على كل view، والـtemplates بتخفي بس.

## 4.4 خصوصية البيانات الطبية
> ده مش استشارة قانونية. **قبل البيع التجاري لعيادات**: مراجعة متطلبات قانون حماية البيانات الشخصية المصري
> (151 لسنة 2020) ولائحته مع محامي — البيانات الصحية بيتعامل معاها كبيانات حساسة ليها اشتراطات أشد.

اللي بيتعمل في الكود من الأول:
- **الموافقة على الرسايل** (`messaging_consent` + `consent_at` + مين سجلها). من غيرها مفيش رسايل. نص الموافقة ظاهر في form المريض.
- **Audit لفتح الملف الطبي**: `audit.log("patient.clinical_view", ...)` عند فتح التاريخ/الزيارات/المرفقات (event واحد لكل مستخدم×مريض×ساعة).
- **مفيش hard delete** للسجل الطبي (Visits/Prescriptions/Attachments). المريض بيتعمله archive. `simple-history` على البيانات الطبية.
- **تصدير بيانات مريض** (PDF ملخص) لو طلبها — owner/doctor.
- الملفات private (view بصلاحيات)، والـbackups متشفرة.
- الـlogs: masking للأرقام (`+20100****567`)، ومفيش نصوص طبية ولا محتوى رسايل في الـlogs.
- الرسايل الطالعة **ميبقاش فيها تشخيص** (القوالب الافتراضية مفيهاش). الروشتة PDF بس لو الإعداد مفعّل.
- الإملاء الصوتي (Google) مذكور كمعالج خارجي، وفيه إعداد يقفله.

## 4.5 Security Checklist (زي البرق +)
- كل اللي في البرق: HTTPS/HSTS، CSRF مع HTMX، CSP (scripts محلية)، Fernet للأسرار في الداتابيز، UFW، الخدمات على 127.0.0.1،
  tenant isolation test لكل view.
- **الرفع**: PDF/JPG/PNG بس، 15MB، التحقق بالمحتوى (Pillow / `%PDF` header)، أسماء ملفات عشوائية.
- **الـwebhook**: HMAC زي البرق، والرسايل الواردة بتتخزن كنص بس (مفيش تنفيذ لأي محتوى).
- **Rate limit** على البحث عن المرضى (منع scraping بالأرقام): 120 طلب/دقيقة/مستخدم.
- **الـscheduler**: advisory lock (مفيش نسختين). كل dispatch بيتحقق من `organization` للصف.
