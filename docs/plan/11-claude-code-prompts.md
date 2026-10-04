# 11 — الرسائل اللي تتبعت لـClaude Code بالترتيب

## قبل أي رسالة: تجهيز الفولدر (مرة واحدة، يدوي)

```bash
mkdir albarq && cd albarq
git init
mkdir -p docs
# انسخي فولدر الخطة كله جوه docs واسميه plan:
cp -r /path/to/albarq-quotation-system-plan docs/plan
# انقلي CLAUDE.md لجذر المشروع (Claude Code بيقراه تلقائيًا من هنا):
mv docs/plan/CLAUDE.md ./CLAUDE.md
mkdir -p docs/plan/assets
# حطي في docs/plan/assets:
#   logo.svg أو logo.png (جودة عالية، خلفية شفافة)
#   reference-quotation.pdf (عرض السعر الحالي)
git add . && git commit -m "plan: initial"
```

المتطلبات على جهازك: Docker (Desktop أو Engine) + Git. مش محتاجة Python على الجهاز لأن كل حاجة جوه Docker.

## قاعدة عامة
- **ابدئي session جديدة (`/clear`) مع كل مرحلة.** الخطة وCLAUDE.md بيتقروا من جديد، والـcontext بيفضل نضيف.
- بعد كل مرحلة: جرّبي بنفسك الحاجات اللي Claude Code هيقولك تتأكدي منها، وبعدين ابعتي الرسالة اللي بعدها.
- لو فيه حاجة مش عاجباكي، قوليها في نفس الـsession قبل ما تنتقلي.

---

### رسالة 1 — مراجعة الخطة (من غير كود)
```
اقرأ CLAUDE.md وكل الملفات في docs/plan بالترتيب.
متكتبش أي كود دلوقتي.
عايزاك:
1) تلخصلي فهمك للنظام في 10 سطور.
2) تقولي أي تناقض أو نقص في الخطة لازم يتحل قبل التنفيذ.
3) تقترح هيكل الـrepo النهائي وملف docker-compose للـdev (شرح بس).
```

### رسالة 2 — Phase 1
```
نفّذ Phase 1 (Foundation) من docs/plan/08-roadmap.md بالكامل.
المستخدمين في البداية: أنا owner، و2 sales (اعمل command لإنشائهم من الـterminal).
في الآخر: شغّل الـtests وruff، اعرضلي الـDoD نقطة نقطة، وقولي أجرب إيه بإيدي، واعمل commit.
```

### رسالة 3 — Phase 2
```
نفّذ Phase 2 (Customers & Contacts).
اعمل seed_demo فيه Travco كـgroup → Jaz Hotels كـchain بالـ3 مسؤولين اللي في الـDoD (أرقام وإيميلات وهمية).
في الآخر: tests + DoD + خطوات التجربة + commit.
```

### ⚠️ قبل رسالة 4: كمّلي `docs/plan/seed/products.csv` بالـ30 منتج الناقصين بنفس الأعمدة
`category_order,category,product_order,name_ar,unit,reference_price`

### رسالة 4 — Phase 3
```
نفّذ Phase 3 (Catalog). استورد المنتجات من docs/plan/seed/products.csv.
الـimport لازم يكون upsert وميكررش لو اتشغل تاني.
في الآخر: قولي عدد المنتجات والأقسام اللي اتستوردت + tests + DoD + commit.
```

### رسالة 5 — Phase 4 (أكبر مرحلة، ممكن تاخد أكتر من session)
```
نفّذ Phase 4 (Quotation Builder). ابدأ بالـmodels والترقيم الشهري والـtests بتاعته،
وبعدين شاشة البناء بالتفصيل الموجود في docs/plan/03-modules-and-ux.md.
افتكر: مفيش كميات ولا إجماليات، سعر الوحدة بس.
قسّم الشغل لخطوات وقولي بعد كل خطوة كبيرة قبل ما تكمل.
```
لو الـsession طولت: `/clear` وبعدين: `كمّل Phase 4 من حيث وقفت. راجع git log وآخر commit الأول.`

### رسالة 6 — Phase 5
```
نفّذ Phase 5 (PDF). المرجع الأساسي للشكل: docs/plan/assets/reference-quotation.pdf،
والألوان والخطوط من docs/plan/seed/brand.json، واللوجو من docs/plan/assets.
ولّد عرض تجربة بـ5 أصناف وعرض بكل المنتجات، وحطهم في tmp/ عشان أقارنهم بالمرجع.
```
**مهم:** افتحي الـPDFs وقارنيها بالمرجع بعينك، وابعتي ملاحظاتك قبل ما تكمّلي.

### رسالة 7 — Phase 6
```
نفّذ Phase 6: شاشة Preview/Send مع بوابة المراجعة الإجبارية (docs/plan/06-sending-email-whatsapp.md)،
وبعدين الإرسال بالإيميل.
اكتب test إن طلب إرسال مباشر (POST) من غير مراجعة بيترفض.
للتجربة هستخدم Gmail App Password، اعمل صفحة الإعدادات وزرار "ابعت إيميل تجربة".
```

### رسالة 8 — Phase 7
```
نفّذ Phase 7 (WhatsApp عن طريق OpenWA). ضيف OpenWA للـdocker-compose بـimage version مثبتة
وengine whatsapp-web.js، ومش مكشوف غير للشبكة الداخلية.
صفحة الإعدادات تعرض الـQR عشان أربط رقم 01000967017.
طبّق rate limit وfallback للإيميل زي الخطة.
```
**وقت التجربة:** ابعتي لأرقامك أنتِ أو لزمايلك بس، مش لعملاء حقيقيين، لحد ما كل حاجة تبقى مستقرة.

### رسالة 9 — Phase 8
```
نفّذ Phase 8 (Dashboard, Audit, Polish). وبعدين راجع كل الشاشات على عرض موبايل (375px) وصلّح أي مشكلة.
```

### رسالة 10 — Phase 9
```
نفّذ Phase 9 (Local Pilot): سكريبت backup/restore للداتابيز والملفات وجرّبه،
وراجع الكود كله مراجعة أمنية ضد docs/plan/04-auth-permissions-security.md
وطلعلي قائمة بأي حاجة ناقصة وصلّحها.
```
بعدها: استخدمي النظام في عروض حقيقية كام يوم وابعتي الملاحظات في sessions منفصلة.

### رسالة 11 — Phase 10 (بعد ما تقرري السيرفر والدومين)
```
نفّذ Phase 10 (Production Deployment) على السيرفر [اسم/IP] والـsubdomain [الدومين].
السيرفر عليه مشاريع شغالة: متلمسش بورت 80/443 ولا أي Nginx config تاني.
اكتبلي الخطوات والأوامر اللي هتتنفذ على السيرفر وراجعها معايا قبل ما تنفذ أي حاجة.
```
