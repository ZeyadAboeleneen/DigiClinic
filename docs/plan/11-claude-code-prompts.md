# 11 — الرسائل اللي تتبعت لـClaude Code بالترتيب

## قبل أي رسالة
- الخطة دي موجودة في `docs/plan/` جوه فولدر DigiClinic. افتحي Claude Code في الفولدر ده.
- **مهم:** `CLAUDE.md` اللي في جذر الفولدر لسه بتاع البرق. رسالة 1 بتخلي Claude Code يستبدله بـ`docs/plan/CLAUDE.md`.
- لو مرسول البرق شغال على نفس الجهاز سيبيه — DigiClinic هيشتغل على بورتات تانية.

## قاعدة عامة
- **`/clear` مع كل مرحلة جديدة.**
- بعد كل مرحلة: جربي اللي Claude Code قالك عليه بإيدك، وبعدين الرسالة اللي بعدها.
- ملاحظاتك قوليها في نفس الـsession قبل ما تنتقلي.
- المراحل الكبيرة (3، 4، 6، 7) ممكن تاخد أكتر من session: `/clear` ثم
  `كمّل Phase N من حيث وقفت. راجع git log وآخر commit الأول.`

---

### رسالة 1 — مراجعة الخطة (من غير كود)
```
الفولدر ده fork من مرسول البرق وهيبقى DigiClinic (نظام عيادات).
اقرأ docs/plan/CLAUDE.md ثم docs/plan/README.md ثم باقي ملفات docs/plan بالترتيب اللي في الـREADME.
تجاهل CLAUDE.md اللي في الجذر (بتاع البرق) — هيتستبدل في Phase 0.
متكتبش أي كود.
عايزاك:
1) تلخصلي فهمك للنظام في 10 سطور.
2) تراجع الكود الحالي وتقولي إيه بالظبط اللي هيتشال وإيه اللي هيتعدل في Phase 0 (قائمة ملفات).
3) أي تناقض أو نقص في الخطة لازم يتحل قبل التنفيذ.
```

### رسالة 2 — Phase 0
```
نفّذ Phase 0 (Fork & Cleanup) من docs/plan/08-roadmap.md.
أول خطوة: انقل docs/plan/CLAUDE.md للجذر مكان القديم.
اعمل migrations من الأول. خلي بالك من البورتات والمجلدات في 01-architecture §1.6.
في الآخر: tests + ruff + الـDoD نقطة نقطة + أجرب إيه بإيدي + commit.
```

### رسالة 3 — Phase 1
```
نفّذ Phase 1 (Clinic Setup & Doctor Schedule). اعتمد على 02 §2.2 و12 §1.
ابدأ بـperiods_for والـtests بتاعتها قبل الشاشات.
البيانات التجريبية من docs/plan/seed/clinic.json.
في الآخر: tests + DoD + خطوات التجربة + commit.
```

### رسالة 4 — Phase 2
```
نفّذ Phase 2 (Patients). انقل المنطق المفيد من customers القديم (phones، البحث المتطبّع) بدل ما تكتبه من الأول
— راجع git history لو محتاج الكود القديم.
اعمل seed_demo بـ30 مريض وهمي، منهم 3 على نفس الرقم.
في الآخر: tests + DoD + commit.
```

### رسالة 5 — Phase 3 (أهم مرحلة)
```
نفّذ Phase 3 (Booking Engine & Booking Screen) حسب docs/plan/12-booking-engine.md و03 §3.2–3.3.
الترتيب: models → services بكل الـtests المذكورة في 12 (بما فيها test التزامن بالـthreads) → شاشة الحجز → التقويم.
قولي بعد ما الـservices والـtests يخلصوا وقبل ما تبدأ الشاشات.
```

### رسالة 6 — Phase 4
```
نفّذ Phase 4 (Notifications Engine) حسب docs/plan/06-notifications-engine.md.
كل رسالة لازم تعدي على ScheduledMessage. ابدأ بالـservices والـdispatcher والـtests
(التعديل، الإلغاء، الـfreshness guard، ساعات الهدوء، dedupe، لحاق الرسايل بعد توقف) قبل الشاشات.
ضيف run_scheduler لـrun.bat. للتجربة هبعت لرقمي أنا بس.
```
**وقت التجربة:** ابعتي لأرقامك أنتِ أو زمايلك بس.

### رسالة 7 — Phase 5
```
نفّذ Phase 5 (Reception Screen, No-show & Payments) حسب 03 §3.4 و12 §7–8 و06 §6.4.
بعد ما تخلص: اعمل سكريبت محاكاة يوم (management command) يحجز 10 مرضى لليوم ويمشّي بعضهم ويغيّب واحد،
عشان أجرب الشاشة بسرعة.
```

### رسالة 8 — Phase 6
```
نفّذ Phase 6 (Doctor Desk & Medical File) حسب 03 §3.5 و02 §2.6 و04 (الصلاحيات والـaudit وقفل الشاشة).
اكتب test إن reception بتاخد 403 على كل URL طبي.
```

### رسالة 9 — Phase 7
```
نفّذ Phase 7 (Prescriptions) حسب docs/plan/05-prescription-pdf.md و03 §3.6.
استورد docs/plan/seed/drugs.csv. ولّد روشتة A5 بالوضعين + صفحة المعايرة وحطهم في tmp/ عشان أشوفهم.
ركّز على النص المختلط عربي/إنجليزي (05 §5.3).
```
**مهم:** اطبعي الروشتة فعلًا على ورقة A5 قبل ما تكمّلي.

### رسالة 10 — Phase 8
```
نفّذ Phase 8 (Voice Dictation) حسب docs/plan/13-voice-dictation.md.
ابدأ بالمطابقة في الـbackend والـtests، وبعدين dictation.js.
```

### رسالة 11 — Phase 9
```
نفّذ Phase 9: الداشبورد والتقارير، مراجعة الموبايل، backup/restore وجربه، مراجعة أمنية ضد 04
ومراجعة N+1 في شاشتي الاستقبال والدكتور. طلعلي قائمة بأي حاجة ناقصة وصلّحها.
```

### رسالة 12 — Phase 10 (بعد قرار الاستضافة)
```
نفّذ Phase 10 على [VPS: اسم/IP + subdomain] أو [جهاز العيادة: Windows].
لو VPS: متلمسش 80/443 ولا configs المشاريع التانية. اكتبلي الأوامر وراجعها معايا قبل التنفيذ.
```
