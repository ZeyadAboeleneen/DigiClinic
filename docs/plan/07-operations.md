# 07 — Operations

## 7.1 التشغيل local (Windows من غير Docker — زي البرق)
`run.bat` بيتعدل:
```
[1/7] uv sync
[2/7] scripts\devdb.py start          → PostgreSQL على 54339، البيانات في %LOCALAPPDATA%\digiclinic\pg
[3/7] tailwind build
[4/7] migrate + seed_org + seed_notifications
[5/7] whatsapp-gateway (بورت 3320، الجلسة في %LOCALAPPDATA%\digiclinic\whatsapp)
[6/7] run_scheduler  (نافذة minimized: "DigiClinic Scheduler")
[7/7] runserver 127.0.0.1:8010
```
- `.env.example`: `DATABASE_URL=postgres://postgres@127.0.0.1:54339/digiclinic`، `WA_GATEWAY_URL=http://127.0.0.1:3320`،
  `MEDIA_ROOT=%LOCALAPPDATA%\digiclinic\media`، `TIME_ZONE=Africa/Cairo`، `SCHEDULER_INTERVAL_SECONDS=20`.
- `whatsapp-gateway/server.js`: البورت ومجلد الجلسة من الـenv (مش hardcoded) عشان ميتلخبطش مع البرق.
- `scripts\stop.bat`: يوقف الـscheduler والبوابة والداتابيز.

## 7.2 الملفات
| النوع | المكان |
|---|---|
| لوجو العيادة | `media/private/org_<id>/branding/` |
| الروشتات PDF | `media/private/org_<id>/prescriptions/<year>/` |
| مرفقات المرضى | `media/private/org_<id>/patients/<file_number>/` |
| جلسة الواتساب | `%LOCALAPPDATA%\digiclinic\whatsapp` / volume على السيرفر |

الحجم: المرفقات (صور أشعة/تحاليل) هي الأكبر — حوالي 1–3MB للصورة → ضغط تلقائي للصور (أقصى بُعد 2000px، JPEG 85%).

## 7.3 Logging & Audit
نفس 3 طبقات البرق (AuditEvent + simple-history + JSON logs). أحداث جديدة:
`appointment.booked/rescheduled/cancelled/no_show/auto_rebooked` · `visit.finished` · `prescription.issued/voided/printed/sent` ·
`patient.clinical_view/merged/exported` · `schedule.changed/day_closed` · `settings.notifications_changed` · `whatsapp.connected/disconnected`.

## 7.4 المراقبة (مهمة جدًا لأن الرسايل تلقائية)
- **Heartbeat للـscheduler** + تحذير في الداشبورد لو واقف > دقيقتين.
- **حالة الواتساب** (زي البرق) + تحذير لو مفصول.
- **رسايل فشلت النهارده** عدّاد في الداشبورد.
- إيميل تنبيه للـowner لو الواتساب اتفصل أو الـscheduler وقف > 15 دقيقة (على السيرفر).

## 7.5 Backup
- `manage.py backup` (pg_dump + media) → ملف متشفر بمفتاح من `.env`. local: يوميًا عند أول تشغيل في اليوم، آخر 14 نسخة.
- `manage.py restore <file>` + تجربة استرجاع إجبارية في Phase 9.
- النسخة off-site: حسب الاستضافة (تحت).

## 7.6 الاستضافة — **لسه متحددتش** (القرار مطلوب قبل Phase 10)

| | VPS (زي Contabo) | جهاز في العيادة |
|---|---|---|
| التذكيرات | شغالة 24 ساعة | بتقف لما الجهاز يتقفل (المحرك بيلحق اللي ينفع لما يشتغل — 06 §6.2) |
| النت في العيادة واقع | السكرتيرة مش هتقدر تشتغل | شغال على الشبكة المحلية، والرسايل تتأخر بس |
| الدخول من برّه (الدكتور من البيت/الموبايل) | ✅ | محتاج tunnel (Cloudflare Tunnel) |
| الإملاء الصوتي (HTTPS) | ✅ بشهادة | محتاج HTTPS محلي أو الدخول من نفس الجهاز على 127.0.0.1 |
| البيع SaaS لعيادات تانية | ✅ مناسب | كل عيادة installation لوحدها |
| Backup | سيرفر + off-site | لازم نسخة سحابية تلقائية + UPS للجهاز |
| التكلفة | اشتراك شهري | الجهاز موجود |

**التوصية**: VPS. لو العيادة النت فيها ضعيف: VPS + خط نت احتياطي (راوتر 4G).
**لو اتقرر جهاز العيادة**: Phase 10 يتحول لـ: تشغيل تلقائي مع Windows (Task Scheduler) + HTTPS محلي (mkcert) + backup لـGoogle Drive/OneDrive (ملف متشفر) + UPS.

### على السيرفر (لو VPS) — زي البرق
Docker Compose: `web` (gunicorn، 127.0.0.1:81xx) · `scheduler` (نفس الـimage، `run_scheduler`) · `whatsapp` (البوابة) · `db`.
Nginx الموجود + subdomain + SSL. **ممنوع لمس 80/443 أو configs المشاريع التانية.**

## 7.7 الأداء المتوقع
عيادة واحدة: ~40 حجز/يوم → ~150 رسالة/يوم. الـscheduler بـ15 ثانية بين رسايل الواتساب = 240/ساعة → كفاية بزيادة.
Polling شاشتين كل 5 ثواني = تافه. الـqueries الأساسية عليها indexes (02).
