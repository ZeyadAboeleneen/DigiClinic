# 07 — Operations: Storage, Logging, Errors, Backup, Deployment, Scaling

## 7.1 File Storage

| النوع | المكان | الوصول |
|---|---|---|
| Static (CSS/JS/fonts) | WhiteNoise / Nginx | عام |
| لوجو الشركة، صور المنتجات | `media/private/org_<id>/...` | بعد تسجيل الدخول |
| PDFs العروض | `media/private/org_<id>/quotations/<year>/` | view بصلاحيات + X-Accel-Redirect |
| OpenWA session data | docker volume `openwa-data` | داخلي |

- كل الملفات في volume واحد (`media`) عشان الـbackup يبقى بسيط.
- الـstorage backend عن طريق Django `STORAGES`، فالانتقال لـS3-compatible (Contabo Object Storage) مستقبلًا = تغيير إعدادات بس.
- الحجم المتوقع: PDF حوالي 100–200KB → 1000 عرض ≈ 200MB. مش مشكلة لسنين.

## 7.2 Logging & Audit Trail

**3 طبقات:**
1. **AuditEvent** (في الداتابيز، يظهر في الواجهة): login/logout/failed login، إصدار عرض، إرسال، إلغاء، قبول/رفض، تغيير إعدادات، إضافة/تعطيل مستخدم، ربط/فصل WhatsApp.
2. **simple-history** (في الداتابيز): كل تعديل على عميل/مسؤول/منتج/إعدادات بالقيمة القديمة والجديدة.
3. **Application logs** (ملفات/stdout): JSON structured logs، مع `request_id` و`user_id` و`org_id`. rotation يومي، احتفاظ 30 يوم.

## 7.3 Error Handling

- **للمستخدم:** رسائل عربي واضحة وعملية ("رقم الواتساب مش متصل. اطلبي من الأدمن يربط الرقم من الإعدادات")، مش stack traces.
- صفحات 403/404/500 مخصصة بتصميم النظام.
- HTMX errors: أي response 4xx/5xx بيظهر toast بدل ما الصفحة تفضل واقفة.
- **Background tasks:** retries بـbackoff للأخطاء المؤقتة (network/timeout)، ومفيش retry للأخطاء النهائية (رقم غلط، إيميل مرفوض). كل فشل بيتسجل على الـDelivery.
- **Error tracking:** Sentry (الخطة المجانية) أو GlitchTip self-hosted. تنبيه بالإيميل لأي 500.
- **Transactions:** إصدار العرض (ترقيم + قفل + snapshots) في `transaction.atomic()`. لو توليد الـPDF فشل، العرض يرجع مسودة والرقم **ما يضيعش** (بيتولد بس بعد نجاح الـPDF... أو بيتحجز ويتعلّم، القرار في 09-open-questions).

## 7.4 Backup Strategy

| ماذا | إزاي | كل قد إيه | الاحتفاظ |
|---|---|---|---|
| PostgreSQL | `pg_dump -Fc` | يومي 3 الفجر | 7 يومي، 4 أسبوعي، 6 شهري |
| Media (PDFs + صور) | `tar` incremental / `rclone sync` | يومي | نفس السياسة |
| OpenWA session | volume snapshot | أسبوعي | 2 |
| `.env` وإعدادات النشر | نسخة متشفرة يدوية | عند أي تغيير | في مكان آمن خارج السيرفر |

- التشفير: `age` أو `gpg` قبل الرفع.
- **Off-site:** rclone لـContabo Object Storage أو Google Drive (مكان غير السيرفر نفسه).
- **اختبار استرجاع شهري** على بيئة تجريبية: backup ما اتجربش = مفيش backup.
- Celery beat بيبعت إيميل لو الـbackup فشل.

## 7.5 Deployment

**التوقيت:** النشر آخر مرحلة، بعد ما النظام يتبني ويتجرب كامل local. السيرفر والدومين هيتحددوا وقتها.

**قيود ثابتة:** السيرفرات عليها مشاريع شغالة، فمفيش أي container بياخد 80/443، والتعامل مع Nginx الموجود بإضافة server block جديد بس.

الخطوات:
1. DNS: subdomain جديد (راجعي 09-open-questions) → IP السيرفر.
2. `deploy/docker-compose.yml`: web على `127.0.0.1:8100`، الباقي بدون ports.
3. Nginx server block جديد:
   - `proxy_pass http://127.0.0.1:8100`
   - `location /protected-media/ { internal; alias /srv/albarq/media/private/; }`
   - `client_max_body_size 10M`
4. `certbot --nginx -d <subdomain>` (نفس الطريقة المستخدمة قبل كده).
5. `docker compose up -d` → `migrate` → `collectstatic` → `createsuperuser` → `loaddata seed`.
6. Health endpoint `/healthz/` (DB + Redis + OpenWA status) للمراقبة.

**بيئات:**
- `dev`: على الجهاز، docker compose نفسه + `DEBUG=True`.
- `staging` (اختياري): subdomain تاني على نفس السيرفر بداتابيز منفصلة لتجربة التحديثات.
- `prod`.

**النشر:** GitHub repo خاص → GitHub Actions (tests + ruff + pip-audit) → deploy script بـSSH (`git pull && docker compose build && migrate && up -d`). Zero-downtime مش ضروري لنظام داخلي؛ النشر خارج ساعات العمل.

**الموارد المتوقعة:** ~1.5–2GB RAM (OpenWA لوحده 300–500MB) و~5GB disk في السنة الأولى. محتاجين نتأكد من المساحة المتاحة على السيرفر المختار.

## 7.6 Scalability

| المرحلة | الحجم | التغيير المطلوب |
|---|---|---|
| الآن | شركة واحدة، 1–10 مستخدمين | ولا حاجة |
| 5–20 شركة | عشرات المستخدمين | VPS منفصل للنظام، Gunicorn workers أكتر، OpenWA sessions متعددة |
| 50+ شركة | مئات | DB على سيرفر منفصل، media على Object Storage، أكتر من worker، يمكن Baileys لكثافة أعلى أو Cloud API الرسمي |

الـmulti-tenancy بالـshared schema كويسة لحد مئات الـorganizations. مفيش داعي لـschema-per-tenant.
