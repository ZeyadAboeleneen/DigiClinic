# 02 — Database Schema

كل الجداول (ما عدا `Organization` و`User`) بتورث `TenantScopedModel` (`organization`, `created_at`, `updated_at`).
الأوقات `DateTimeField` (aware, UTC). الفلوس `Decimal(10,2)`. الأرقام E.164 عن طريق `apps/core/phones.py`.
`simple-history` على: `Patient`, `Visit`, `Prescription`, `Doctor`, `WorkingPeriod`, `VisitType`, `NotificationTemplate`, الإعدادات.

---

## 2.1 organizations

### OrganizationSettings (تتنضف من حقول العروض)
| الحقل | النوع | ملاحظات |
|---|---|---|
| clinic_name_ar / clinic_name_en | char | |
| logo | image | private |
| primary_color / dark_color / neutral_color | char(7) | |
| phones | JSON list | بتظهر في الرسايل والروشتة |
| address_ar | char | |
| map_link | URL | `{map_link}` في الرسايل |
| working_hours_text | char | سطر بيظهر في فوتر الروشتة ("يوميًا ما عدا الخميس والجمعة 2–9 م") |
| timezone | char | default `Africa/Cairo` |
| whatsapp_sender | char | رقم العيادة |

**يتشال:** كل حقول quote_number / validity / vat / terms / intro / price_note / section_row_color.

---

## 2.2 doctors

### Doctor
| الحقل | النوع | ملاحظات |
|---|---|---|
| user | FK User, null | الحساب اللي بيفتح شاشة الدكتور |
| name_ar / name_en | char | |
| title_ar | char | "د." / "أ.د." |
| specialty_ar | char | |
| qualifications | JSON list[str] | سطور الهيدر في الروشتة |
| booking_mode | choice | `slots` / `queue` |
| slot_minutes | int | الافتراضي لو نوع الزيارة مالوش مدة (slots) |
| booking_horizon_days | int | أقصى مدة حجز قدام (default 60) |
| min_notice_minutes | int | أقل وقت قبل الميعاد يتحجز فيه (default 0) |
| queue_avg_minutes | int | متوسط الكشف عشان الميعاد التقريبي (queue) |
| near_turn_threshold | int | يتبعت "دورك قرّب" لما يفضل قدامه N (queue). 0 = مقفول |
| allow_overbooking | bool | owner/doctor يقدروا يحجزوا "استثنائي" فوق الطاقة |
| is_active | bool | |

النسخة الأولى: دكتور واحد. الواجهة بتخفي اختيار الدكتور لو فيه واحد بس.

### WorkingPeriod (فترات العمل الأسبوعية)
| الحقل | النوع | ملاحظات |
|---|---|---|
| doctor | FK | |
| weekday | int 0–6 | 0 = السبت (ثابت في الكود: `WEEKDAYS` بالترتيب المصري) |
| start_time / end_time | time | بتوقيت العيادة. `end > start` (مفيش فترات بتعدي نص الليل في v1) |
| max_patients | int, null | للـqueue (الطاقة) |
| effective_from / effective_to | date, null | عشان تغيير الجدول ميأثرش على الماضي ولا على حجوزات اتعملت |
| is_active | bool | |

قيد: مفيش تداخل بين فترتين لنفس الدكتور ونفس اليوم في نفس المدة (validation في الـservice + test).

### ScheduleException
| الحقل | النوع | ملاحظات |
|---|---|---|
| doctor | FK | |
| date | date | |
| kind | choice | `closed` (اليوم مقفول) / `custom` (مواعيد مختلفة اليوم ده) / `extra` (يوم إضافي) |
| start_time / end_time / max_patients | null | لـcustom/extra |
| reason | char | "إجازة"، "مؤتمر" |

قفل يوم فيه حجوزات → بيفتح شاشة "الحجوزات المتأثرة" (12-booking-engine §5).

### VisitType
| الحقل | النوع | ملاحظات |
|---|---|---|
| doctor | FK | |
| name_ar | char | كشف / إعادة / استشارة / جلسة |
| duration_minutes | int | slots |
| price | decimal | |
| is_followup | bool | نوع إعادة |
| free_followup_days | int | الإعادة مجانية لو فيه كشف خلال X يوم (0 = مقفول) |
| color | char(7) | في الجداول |
| order / is_active | | |

---

## 2.3 patients

### Patient
| الحقل | النوع | ملاحظات |
|---|---|---|
| file_number | int | **مسلسل لكل عيادة** (PatientSequence + `select_for_update`). unique(org, file_number) |
| full_name | char | |
| name_normalized | char, indexed | بيتحسب في `save()` بـ`normalize_ar` |
| gender | choice | ذكر / أنثى |
| date_of_birth | date, null | |
| dob_is_estimated | bool | لو السكرتيرة كتبت السن بس → `dob = اليوم - السن` والعلامة دي True |
| phone | char E.164 | **مش unique** |
| whatsapp | char E.164, null | default = phone (checkbox "نفس الرقم") |
| email | email, blank | |
| guardian_name | char, blank | للأطفال |
| address / occupation | char, blank | |
| preferred_channel | choice | whatsapp / email / none |
| messaging_consent | bool + `consent_at` | الموافقة على الرسايل (04 §4.4). من غيرها مفيش رسايل |
| notes | text | ملاحظات إدارية (مش طبية) |
| custom_fields | JSON | قيم الحقول الإضافية (validated ضد `PatientFieldDefinition`) |
| no_show_count | int | denormalized، بيتحدث من الـservice |
| merged_into | FK self, null | دمج المكرر (بيتنقل كل حاجة للأصلي) |
| is_active | bool | مفيش hard delete لمريض عنده زيارات |

Indexes: `(organization, phone)`, `(organization, name_normalized)`, `(organization, file_number)`.

### Allergy / ChronicCondition
`patient` FK · `name` char · `severity` (allergy: خفيفة/متوسطة/شديدة) · `notes` · `recorded_by`.
بيظهروا **بانر أحمر** في شاشة الدكتور، والحساسية بتتقارن بالأدوية في الروشتة.

### PatientFieldDefinition (الحقول الإضافية للعيادة)
`key` slug · `label_ar` · `type` (text/number/date/choice/bool) · `choices` JSON · `scope` (`patient` / `visit`) · `order` · `is_active`.
مثال: عيادة أطفال تضيف "فصيلة الدم" (patient) و"محيط الرأس" (visit).

---

## 2.4 scheduling

### Appointment
| الحقل | النوع | ملاحظات |
|---|---|---|
| patient / doctor / visit_type | FK | |
| date | date | تاريخ العيادة (local) |
| start_at / end_at | datetime | slots: الميعاد الفعلي. queue: الميعاد **التقريبي** المحسوب |
| period | FK WorkingPeriod, null | الفترة اللي الحجز فيها (queue) |
| queue_number | int, null | queue بس |
| status | choice | `booked` · `confirmed` · `arrived` · `in_consultation` · `completed` · `no_show` · `cancelled` · `rescheduled` |
| version | int | **بيزيد مع أي تغيير في الميعاد**. الرسايل المجدولة بتتأكد منه قبل الإرسال |
| source | choice | `phone` · `walk_in` · `auto_rebook` · `whatsapp` (مستقبلًا) · `online` (مستقبلًا) |
| is_overbooked | bool | حجز استثنائي فوق الطاقة |
| price | decimal | snapshot وقت الحجز (0 لو إعادة مجانية) |
| discount | decimal | |
| followup_of | FK self, null | الكشف الأصلي للإعادة |
| rescheduled_to | FK self, null | |
| auto_rebooked_from | FK self, null | لمنع إعادة الحجز التلقائي أكتر من مرة |
| booked_by | FK User | |
| arrived_at / called_at / completed_at / cancelled_at | datetime, null | |
| cancel_reason | char | |
| notes | char | |

القيود:
- `UniqueConstraint(doctor, date, period, queue_number)` بشرط `status NOT IN (cancelled, rescheduled)`.
- منع تداخل الـslots: في الـservice تحت قفل `DayLedger` (12 §4). (اختياري لو `btree_gist` متاح: `ExclusionConstraint`.)
- Index: `(organization, doctor, date)`, `(organization, patient, -start_at)`, `(organization, status, start_at)`.

### DayLedger
`doctor` · `date` · `next_queue_number` (per period → JSON `{period_id: n}`) · unique(doctor, date).
صف بيتعمل `select_for_update()` عليه في كل حجز/تعديل/إلغاء لليوم ده → يمنع السباق بين اتنين بيحجزوا نفس الميعاد.

### AppointmentEvent (append-only)
`appointment` · `kind` (created/status_changed/rescheduled/cancelled/auto_rebooked/note) · `from_status`/`to_status` · `data` JSON (الميعاد القديم والجديد) · `by_user` null (النظام) · `at`.

---

## 2.5 notifications

### NotificationSettings (one per org)
| الحقل | ملاحظات |
|---|---|
| is_enabled | مفتاح عام |
| quiet_start / quiet_end | time (default 22:00 → 09:00) |
| quiet_policy | `defer` (يتأجل لنهاية ساعات الهدوء) / `skip` |
| default_channel | `preferred` (حسب المريض) / whatsapp / email / both |
| email_fallback | bool: لو الواتساب فشل نهائيًا ابعت إيميل |
| send_prescription_after_visit | bool (**اختياري، مقفول افتراضيًا**) |
| prescription_channel | whatsapp / email / both |
| no_show_policy | `auto_rebook` / `notify` / `none` |
| no_show_grace_minutes | slots: بعد الميعاد بكام دقيقة يتحسب غياب (default 30) |
| no_show_queue_mark_at | queue: `period_end` (آخر الفترة) / `manual` |
| auto_rebook_after_days | default 7 |
| auto_rebook_window_days | يدوّر على ميعاد فاضي خلال كام يوم بعدها (default 14) |
| wa_min_gap_seconds | anti-ban (default 15) + jitter عشوائي 0–10 ثانية |

### NotificationTemplate
| الحقل | ملاحظات |
|---|---|
| event | `booking_confirmed` · `reminder` · `rescheduled` · `cancelled` · `no_show_rebooked` · `no_show_missed` · `near_turn` · `prescription` · `followup_due` |
| name_ar | "تذكير قبلها بيوم" |
| offset_minutes | للـreminder بس: قبل الميعاد بكام دقيقة (1440, 60). ينفع أكتر من reminder |
| min_lead_minutes | لو الوقت الباقي على الميعاد أقل من كده وقت الإرسال → skip (default: 24h → 120، 1h → 10) |
| channel | `default` / whatsapp / email / both |
| body | النص بالمتغيرات (06 §6.3) |
| email_subject | |
| attach_pdf | للروشتة |
| applies_to_mode | all / slots / queue (مثلًا: تذكير الساعة للـslots بس، و"دورك قرّب" للـqueue) |
| is_enabled / order | |

`seed_notifications` بيعمل القوالب الافتراضية لأي عيادة جديدة.

### ScheduledMessage (الـOutbox — قلب المحرك)
| الحقل | ملاحظات |
|---|---|
| patient | FK |
| appointment | FK null |
| prescription | FK null |
| template | FK |
| event | snapshot |
| channel | whatsapp / email (صف لكل قناة) |
| recipient | snapshot E.164 / email |
| send_at | datetime (بعد تطبيق ساعات الهدوء) |
| appointment_version | int: لازم يساوي `appointment.version` وقت الإرسال، غير كده → `cancelled (stale)` |
| status | `pending` · `sending` · `sent` · `failed` · `cancelled` · `skipped` |
| status_reason | "اتعدل الميعاد"، "فات وقتها"، "ساعات الهدوء"، "مفيش موافقة" |
| rendered_text / rendered_subject | **بيتملوا وقت الإرسال** (عشان أي تعديل في القالب أو البيانات يطلع صح) |
| attempts / next_attempt_at | retries |
| dedupe_key | unique: `{appointment}:{event}:{template}:{version}:{channel}` → مستحيل نفس الرسالة تتبعت مرتين |
| created_by | FK User, null |

### messaging.Delivery (موجود — يتعدل)
يتشال `quotation`. يتضاف `scheduled_message` FK. الباقي زي ما هو (provider_message_id، acks، delivered_at، read_at).
`QuotationReview` يتشال.

---

## 2.6 clinical

### Visit (الكشف)
| الحقل | ملاحظات |
|---|---|
| appointment | OneToOne (الـwalk-in بيتعمله appointment برضه) |
| patient / doctor | FK |
| started_at / finished_at | |
| chief_complaint | text (الشكوى) |
| history | text (التاريخ المرضي للزيارة) |
| examination | text |
| diagnosis | text + `diagnosis_tags` JSON list (عشان الإكمال التلقائي والتقارير) |
| plan / notes | text |
| followup_after_days | int null → اقتراح حجز إعادة |
| custom_fields | JSON (حقول `scope=visit`) |
| status | `open` / `finished` |

بعد `finished` التعديل مسموح للدكتور بس، و`simple-history` بيسجل القديم (السجل الطبي ميتمسحش).

### Vitals
`visit` OneToOne · `weight_kg` · `height_cm` · `bmi` (محسوب) · `bp_systolic` · `bp_diastolic` · `pulse` · `temperature_c` · `spo2` · `blood_glucose` · `recorded_by` · `recorded_at`.
كلها null. السكرتيرة/الممرضة تسجلها عند الوصول، والدكتور يعدلها.

### Attachment
`patient` · `visit` null · `file` (private: `org_<id>/patients/<file_number>/...`) · `kind` (تحليل/أشعة/تقرير/أخرى) · `title` · `taken_on` date · `uploaded_by`.
PDF/JPG/PNG/HEIC→JPG بس، حد أقصى 15MB، التحقق بالمحتوى مش الامتداد.

---

## 2.7 prescriptions

### Drug (كتالوج أدوية العيادة)
| الحقل | ملاحظات |
|---|---|
| name | الاسم التجاري (لاتيني) "Augmentin 1g" |
| generic_name | "Amoxicillin + Clavulanic acid" |
| form | قرص/كبسولة/شراب/حقن/نقط/كريم/بخاخ/لبوس/فوار/أكياس |
| strength | "1 g" |
| aliases_ar | JSON list: "اوجمنتين"، "اوجمنتن" (للإملاء الصوتي والبحث) |
| default_instructions | "قرص كل 12 ساعة بعد الأكل" |
| default_duration | "لمدة 7 أيام" |
| usage_count | ترتيب الإكمال التلقائي حسب استخدام الدكتور |
| is_active | |

`import_drugs <csv>`: upsert بالاسم، ميمسحش تعديلات الواجهة (زي `import_products`).

### DosePhrase
`text` · `kind` (dose/frequency/timing/duration) · `order` · `usage_count`. جمل جاهزة بزرار واحد.

### Prescription
| الحقل | ملاحظات |
|---|---|
| visit | FK (null للروشتة من غير كشف — مستقبلًا) |
| patient / doctor | FK |
| number | `RX-{year}-{seq:05d}` لكل عيادة (Sequence + `select_for_update`) |
| status | `draft` / `final` |
| revision | 0, 1, 2… (تعديل روشتة نهائية = نسخة جديدة `-R1`، زي العروض) |
| advice | text ("راحة، سوائل كتير") |
| next_visit_date | date null |
| patient_snapshot | JSON (الاسم، السن، رقم الملف وقت الإصدار) |
| doctor_snapshot | JSON (الاسم، اللقب، المؤهلات) |
| pdf_full | file (التصميم الكامل) |
| issued_at / issued_by | |

`PrescriptionItem`: `prescription` · `drug` FK null · `drug_name` snapshot · `form` · `instructions` · `duration` · `notes` · `order`.
بعد `final`: الـitems immutable (زي `QuotationItem`).

### PrescriptionTemplate / PrescriptionTemplateItem
`doctor` · `name` ("نزلة برد — كبار") · `advice` · items بنفس أعمدة `PrescriptionItem` · `usage_count`.

---

## 2.8 billing

### Payment
`appointment` FK · `amount` · `method` (كاش/فيزا/InstaPay/محفظة) · `received_by` · `received_at` · `note` · `is_refund` bool.
المستحق = `appointment.price - discount`. الحالة (مدفوع/جزئي/لأ) محسوبة.
