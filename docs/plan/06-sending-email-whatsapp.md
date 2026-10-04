# 06 — Sending: Email, WhatsApp & Workflow

## 6.1 Sending Workflow

```
[إصدار العرض] → Preview (الـPDF النهائي كامل في الصفحة)
      │
☐ راجعت الأصناف والأسعار بنفسي   ← إجباري، والزرار مقفول من غيره
      │
[إرسال] → Modal:
   المسؤول: Ahmed
   ☑ WhatsApp  → +20 106 077 7030  (primary ▾ لو أكتر من رقم)
   ☑ Email     → ahmed@jazhotels.com
   الرسالة: [قالب معبّأ بالمتغيرات، قابل للتعديل]
   [إلغاء]  [تأكيد الإرسال]
      │
   Delivery لكل قناة (status=queued) → Celery task
      │
   النتيجة live في الصفحة (HTMX polling كل 3 ثواني):
   ✅ WhatsApp: اتبعت · ✓✓ اتسلّم · 👁 اتقرا
   ❌ Email: فشل — "SMTP authentication failed" [إعادة المحاولة]
```

### بوابة المراجعة الإجبارية (بدل موافقة المدير)
- مفيش approval من حد تاني. لكن **اللي بيبعت لازم يراجع بنفسه**:
  - فتح صفحة الـPreview للـPDF **النهائي** بيسجّل `reviewed_by/reviewed_at` للمستخدم ده.
  - زرار الإرسال مقفول لحد ما: (1) المستخدم الحالي نفسه فتح الـPreview، و(2) علّم checkbox "راجعت الأصناف والأسعار بنفسي".
  - الـbackend بيتحقق من الشرطين (مش الواجهة بس)، ولو مستخدم تاني هو اللي هيبعت لازم يراجع هو كمان.
  - الـModal بيعرض ملخص أخير: العميل، المسؤول، عدد الأصناف، الرقم/الإيميل المستلم.
  - `Delivery.review_confirmed=True` بيتسجل كدليل.

- القنوات المتاحة بتتحدد من `ContactChannel` بتاعة المسؤول، والافتراضي من `preferred_channel`.
- أول Delivery ناجح → `Quotation.status = sent`.
- ممكن تبعتي لأكتر من مسؤول في نفس العميل (checkbox لكل واحد).
- **لو WhatsApp فشل** يظهر اقتراح: "ابعت بالإيميل بدلًا منه" (لو المسؤول عنده إيميل).
- زرار "إعادة إرسال" من صفحة العرض في أي وقت.

### متغيرات القوالب
`{contact_name}` `{customer_name}` `{quote_number}` `{valid_until}` `{items_count}` `{sender_name}` `{company_name}`

مثال رسالة WhatsApp الافتراضية:
```
أهلًا أ/ {contact_name}،
مرفق عرض أسعار رقم {quote_number} من {company_name}، صالح حتى {valid_until}.
لأي استفسار أنا متاح.
{sender_name}
```

## 6.2 Provider Abstraction

```python
class MessagingProvider(Protocol):
    def send(self, delivery: Delivery, pdf_path: Path) -> ProviderResult: ...
    def health_check(self) -> ChannelStatus: ...

class SmtpEmailProvider: ...
class OpenWAProvider: ...
# مستقبلًا: class MetaCloudAPIProvider: ...  ← نفس الـinterface
```

ده بيخلي الانتقال لـWhatsApp Business API الرسمي تغيير provider واحد، مش إعادة بناء.

## 6.3 Email

- SMTP عادي. الإعدادات من شاشة الإعدادات (متشفرة).
- **حاليًا Gmail** (`elbaark.company@gmail.com`): محتاج App Password (بعد تفعيل 2-Step Verification على الحساب). حد Gmail اليومي كافي جدًا للاستخدام ده.
- **الأفضل على المدى المتوسط:** إيميل على دومين الشركة (Google Workspace أو Zoho) → شكل أكثر احترافية ووصول أفضل لـinbox الفنادق.
- الإيميل: HTML بسيط بألوان البرق + نسخة plain text + الـPDF مرفق.
- `Reply-To` = إيميل المستخدم اللي بعت (لو متسجل) عشان الرد يوصل للشخص الصح.
- Retry: 3 محاولات (بعد 1، 5، 15 دقيقة) للأخطاء المؤقتة بس.

## 6.4 WhatsApp عن طريق OpenWA

### ليه OpenWA
- Gateway جاهز مبني على whatsapp-web.js: REST API + webhooks + إدارة sessions + rate limiting.
- Multi-session → كل organization مستقبلًا تربط رقمها بنفسها.
- مش هنكتب Node service بنفسنا.

### الإعداد
- Container منفصل في نفس الـdocker compose، `ENGINE_TYPE=whatsapp-web.js`.
- مش مكشوف للإنترنت. الـdashboard بتاعه مش هنستخدمه للموظفين؛ Django هو الواجهة.
- API key بصلاحية محدودة لـDjango.
- Webhook لـDjango لأحداث: `session.status` (اتصل/اتفصل) و `message.ack` (اتسلّم/اتقرا).
- استهلاك الذاكرة: تقريبًا 300–500MB لكل session (Chromium headless).
- الـimage version مثبت (pinned) ومش `latest`، وأي تحديث بيتجرب الأول.

### ربط الرقم من الإعدادات (من غير أي خطوات تقنية للأدمن)
1. الأدمن يدخل الإعدادات → WhatsApp → "ربط رقم".
2. Django يطلب من OpenWA session جديدة ويعرض الـQR في الصفحة (بيتحدث تلقائيًا).
3. الأدمن يعمل scan من الموبايل (الأجهزة المرتبطة).
4. الحالة تبقى 🟢 متصل + الرقم يظهر.
5. "تغيير الرقم" = فصل الـsession الحالية + ربط جديد.

### إرسال العرض
- رسالة document (الـPDF) مع الـcaption = نص الرسالة.
- رقم المستلم بيتحول من E.164 لصيغة `2010XXXXXXXX@c.us`.
- قبل الإرسال: التحقق إن الرقم عليه واتساب (لو الـAPI بيدعمها) → لو لأ، Delivery يفشل برسالة واضحة.

### الحد من خطر الحظر (مهم)
- **رقم الإرسال: `01000967017`** (قرار نهائي). ده رقم معروف للعملاء ومطبوع على العروض، فده بيقلل مشكلة "أول رسالة لرقم غريب"، لكن لو اتقيّد هيتأثر رقم شغال. عشان كده باقي الإجراءات تحت مهمة جدًا.
- الرقم بيفضل شغال عادي على الموبايل (Linked Device)، والنظام مجرد جهاز مرتبط.
- الإرسال **يدوي بضغطة زرار** لكل عرض. مفيش bulk ولا حملات.
- Rate limit في Celery: حد أقصى رسالة كل 20 ثانية من نفس الرقم.
- المستلمين عملاء بيتعاملوا مع الشركة أصلًا ومتوقعين الرسالة.
- الإيميل دايمًا fallback متاح.
- ملحوظة: أول رسالة لرقم عمره ما كلّم الرقم ده قبل كده ممكن متوصلش (سياسة واتساب نفسها). الحل: المسؤول يحفظ رقم الشركة أو يبعت رسالة الأول، أو نبعت الإيميل كمان لأول مرة.

### Health monitoring
- Celery beat كل 5 دقائق: `health_check()` → لو الحالة اتغيرت لـdisconnected: banner أحمر للأدمن في الـdashboard + إيميل.
