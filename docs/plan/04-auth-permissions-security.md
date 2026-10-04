# 04 — Authentication, Permissions & Security

## 4.1 Authentication

- Django session auth (cookie-based). مفيش تسجيل عام: المستخدمين بيتضافوا بدعوة من الأدمن.
- الدعوة: رابط لمرة واحدة صالح 48 ساعة، المستخدم يحط الباسورد بنفسه.
- Password hashing: **Argon2**. سياسة: 10 حروف كحد أدنى + Django validators.
- **django-axes:** قفل بعد 5 محاولات فاشلة لمدة 15 دقيقة (per username + IP).
- Session: تنتهي بعد 12 ساعة من غير نشاط، والـcookie: `Secure`, `HttpOnly`, `SameSite=Lax`.
- "نسيت كلمة المرور" عن طريق الإيميل.
- **2FA (TOTP)**: مرحلة لاحقة، إجباري للـowner/admin لما يتفعل.

## 4.2 Roles & Permissions

| الصلاحية | viewer | sales | manager | admin | owner |
|---|:-:|:-:|:-:|:-:|:-:|
| عرض العملاء/المنتجات/العروض | ✅ | ✅ | ✅ | ✅ | ✅ |
| إنشاء وإصدار عرض | | ✅ | ✅ | ✅ | ✅ |
| إرسال عرض | | ✅ | ✅ | ✅ | ✅ |
| تعديل العملاء والمسؤولين | | ✅ | ✅ | ✅ | ✅ |
| تعديل المنتجات والأسعار المرجعية | | | ✅ | ✅ | ✅ |
| إلغاء عرض / تحديد مقبول-مرفوض | | own | ✅ | ✅ | ✅ |
| سجل النشاط | | | ✅ | ✅ | ✅ |
| إدارة المستخدمين | | | | ✅ | ✅ |
| إعدادات الشركة والإرسال | | | | ✅ | ✅ |
| حذف المنظمة / نقل الملكية | | | | | ✅ |

التنفيذ:
- الدور متخزن في `Membership.role`، ومصفوفة الصلاحيات في كود واحد (`apps/accounts/permissions.py`).
- Decorator/mixin: `@require_perm("quotation.send")` على كل view.
- الـtemplates بتخفي الأزرار، **لكن الحماية الحقيقية في الـview**.
- قابل للتوسع: لو احتجنا صلاحيات custom لكل مستخدم بعدين، نضيف `Membership.extra_permissions` (JSON).

## 4.3 Security Checklist

**الشبكة والسيرفر**
- HTTPS إجباري + HSTS. شهادة Let's Encrypt من الـcertbot الموجود.
- كل الـcontainers على 127.0.0.1 أو الشبكة الداخلية. PostgreSQL وRedis وOpenWA **مش مكشوفين أبدًا**.
- UFW: 22 (مع key-only SSH)، 80، 443 بس.
- اختياري: تقييد الدخول للنظام بـIP أو Basic Auth إضافي في Nginx لو الموظفين من أماكن ثابتة.

**التطبيق**
- `DEBUG=False`، `ALLOWED_HOSTS` محدد، `SECURE_*` settings كلها مفعلة.
- CSRF على كل POST (HTMX بياخد الـtoken من header).
- Content-Security-Policy: scripts من نفس الدومين بس (HTMX وAlpine متخزنين locally).
- Tenant isolation test لكل view (راجعي 01-architecture).
- رفع الملفات: صور بس للوجو والمنتجات، حد أقصى 5MB، التحقق بـPillow مش بالامتداد.

**البيانات الحساسة**
- كل الأسرار في `.env` (مش في git): `SECRET_KEY`, `DATABASE_URL`, `FIELD_ENCRYPTION_KEY`, `OPENWA_API_KEY`, ...
- باسورد الـSMTP ومفاتيح OpenWA في الداتابيز **متشفرة بـFernet** بمفتاح من الـenv.
- الـPDFs **مش في مجلد media عام**. بتتقدم عن طريق view بتتأكد من الصلاحية (`FileResponse` في الـlocal، و`X-Accel-Redirect` من Nginx في الـproduction).
- الـbackups متشفرة قبل ما تخرج من السيرفر.
- الـlogs مبتسجلش باسوردات أو محتوى كامل للأرقام في مستوى INFO (masking: `+20106****030`).

**التحديثات**
- `pip-audit` في الـCI، تحديث أمني شهري لـDjango وOpenWA image.
