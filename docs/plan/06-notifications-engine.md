# 06 — Notifications Engine (محرك الإشعارات)

المبدأ: **كل رسالة بتعدي على جدول `ScheduledMessage` (outbox)**. مفيش أي مكان في الكود بيبعت رسالة مباشرة.
ده بيدّينا: إلغاء وتعديل سهل، مفيش تكرار، retries، سجل كامل لكل رسالة، ولحاق الرسايل لو الجهاز اتقفل.

## 6.1 الجدولة (`notifications/services.py`)

```python
def schedule_for(appt):            # بعد الحجز (on_commit)
    # booking_confirmed → send_at = now  (skip لو walk_in)
    # لكل reminder template مفعّل ومناسب لـbooking_mode:
    #     send_at = appt.start_at - offset
    #     لو send_at فات بالفعل أو (start_at - now) < min_lead_minutes → متتعملش أصلًا
    # تطبيق ساعات الهدوء على send_at
    # صف لكل قناة (whatsapp/email) حسب template.channel / patient.preferred_channel
    # dedupe_key يمنع التكرار (get_or_create)

def on_rescheduled(old, new):      # cancel pending(old) + rescheduled message + schedule reminders(new)
def on_cancelled(appt):            # cancel pending + cancelled message
def on_no_show(appt, rebooked=None)
def on_queue_progress(doctor, day) # "دورك قرّب"
def send_prescription(rx)          # لو send_prescription_after_visit
def cancel_pending(appt, reason)
```

**الشروط قبل أي رسالة:** `NotificationSettings.is_enabled` · `template.is_enabled` · `patient.messaging_consent` ·
فيه recipient للقناة. أي شرط ناقص → الصف بيتعمل بحالة `skipped` + السبب (عشان يبان في السجل ليه ما اتبعتتش).

## 6.2 الإرسال (`dispatcher.py` + `manage.py run_scheduler`)

```
كل 20 ثانية:
  rows = ScheduledMessage.pending.filter(send_at<=now, next_attempt_at<=now)
            .select_for_update(skip_locked=True)[:20]
  لكل row:
    1. التحقق من الصلاحية (Freshness guard):
       - appointment.version != row.appointment_version → cancelled ("اتعدل الميعاد")
       - الحجز ملغي/مؤجل/تم → cancelled
       - reminder و (start_at - now) < min_lead_minutes → skipped ("فات وقتها")
       - near_turn والمريض وصل بالفعل → skipped
    2. render النص دلوقتي بالمتغيرات الحالية
    3. send عن طريق provider (WhatsApp: احترام wa_min_gap_seconds + jitter، زي rate limit البرق)
    4. Delivery row لكل محاولة (provider_message_id للـacks)
    5. نجاح → sent | فشل مؤقت → retry بعد [1, 5, 15] دقيقة | فشل نهائي → failed (+ email_fallback لو مفعّل)
  + jobs: mark_no_shows() كل دقيقتين، cleanup يومي
```

- **لحاق الرسايل بعد ما الجهاز يتقفل** (مهم لو الاستضافة طلعت على جهاز العيادة): نفس الـguard فوق بيحل المشكلة.
  الجهاز اشتغل الساعة 10 الصبح → التأكيدات المتأخرة تتبعت، تذكير الـ24 ساعة يتبعت لو لسه فيه أكتر من ساعتين،
  تذكير الساعة اللي فات وقته يتعمل skipped. محدش ياخد "ميعادك بعد ساعة" والميعاد عدى.
- الـscheduler بيكتب heartbeat (`cache`/صف في الداتابيز) كل دورة. الداشبورد والإعدادات بيعرضوا تحذير أحمر لو
  آخر heartbeat أقدم من دقيقتين: "محرك الرسايل واقف".
- قفل عملية واحدة: `pg_advisory_lock` عشان لو اتشغل مرتين بالغلط ميبعتش مرتين.

## 6.3 القوالب والمتغيرات

| المتغير | المثال |
|---|---|
| `{patient_name}` | محمد أحمد |
| `{first_name}` | محمد |
| `{doctor_name}` | د. سارة علي |
| `{clinic_name}` | عيادة … |
| `{date}` | الثلاثاء 14 أكتوبر |
| `{time}` | 4:20 م |
| `{time_label}` | "الساعة 4:20 م" (slots) / "دورك رقم 7 — حوالي الساعة 3:40 م" (queue) |
| `{queue_number}` | 7 |
| `{visit_type}` | كشف |
| `{price}` | 300 ج.م |
| `{clinic_phone}` / `{clinic_address}` / `{map_link}` | |
| `{old_date}` / `{old_time}` | في رسالة التعديل |
| `{patients_ahead}` | في "دورك قرّب" |
| `{rx_number}` / `{next_visit_date}` | في رسالة الروشتة |

- متغير غلط في القالب → validation error وقت الحفظ (مش وقت الإرسال).
- شاشة القوالب فيها **Preview حي** ببيانات حجز تجريبي + زرار "ابعت تجربة لرقمي".

### القوالب الافتراضية (`seed_notifications`)
```
booking_confirmed:
أهلًا {first_name} 👋
تم حجز {visit_type} مع {doctor_name} يوم {date}، {time_label}.
العنوان: {clinic_address}
{map_link}
للتعديل أو الإلغاء: {clinic_phone}

reminder (1440):
تذكير: ميعادك مع {doctor_name} بكرة {date}، {time_label}.
لو مش هتقدر تيجي يا ريت تبلغنا على {clinic_phone} عشان نِدّي الميعاد لحد تاني.

reminder (60, slots):
ميعادك مع {doctor_name} النهارده {time_label} — فاضل حوالي ساعة. في انتظارك 🌿

rescheduled:
تم تعديل ميعادك مع {doctor_name} من {old_date} {old_time} إلى {date}، {time_label}.

cancelled:
تم إلغاء ميعادك مع {doctor_name} يوم {date}. للحجز من جديد: {clinic_phone}

near_turn (queue):
{first_name}، دورك قرّب 🔔 قدامك {patients_ahead} بس. يا ريت تكون في العيادة.

no_show_rebooked:
افتقدناك النهارده يا {first_name}. حجزنالك ميعاد جديد يوم {date}، {time_label}.
لو مش مناسب كلمنا على {clinic_phone}.

no_show_missed:
افتقدناك النهارده يا {first_name}. تقدر تحجز ميعاد جديد على {clinic_phone}.

prescription:
{first_name}، دي روشتة زيارتك النهارده مع {doctor_name} (رقم {rx_number}). ألف سلامة عليك.
```
(النصوص دي مبدئية والعيادة بتعدلها من الشاشة. الإيموجي اختياري.)

## 6.4 "دورك قرّب" (queue)
- بيتنادي `on_queue_progress` مع كل انتقال لـ`in_consultation` أو `completed` في شاشة الاستقبال/الدكتور.
- لكل مريض booked/confirmed **لسه ما وصلش** في نفس الفترة: `ahead` = عدد اللي قبله بالدور ولسه مخلصوش (مش no_show/cancelled).
- لو `ahead <= near_turn_threshold` ومتبعتلوش قبل كده (dedupe) → رسالة فورية.

## 6.5 ساعات الهدوء
- `send_at` جوه ساعات الهدوء → `defer`: يتأجل لـ`quiet_end` | `skip`: يتلغي.
- **استثناء**: رسايل التعديل/الإلغاء لميعاد النهارده أو بكرة بدري بتتبعت حتى في ساعات الهدوء لو قبل 11 م (متغير في الإعدادات).
- مثال: ميعاد 10:00 الصبح → تذكير الـ24 ساعة 10:00 الصبح امبارح (عادي). ميعاد 9:30 الصبح وتذكير الساعة 8:30 → جوه الهدوء
  لو `quiet_end=09:00` → الـ`min_lead_minutes` بيقرر (لو متبقي أقل من 10 دقايق → skip).

## 6.6 الواتساب: المخاطر والحماية
- البوابة غير رسمية (whatsapp-web.js). الرسايل التلقائية **أكتر بكتير** من عروض الأسعار اليدوية، فاحتمال الحظر أعلى:
  - فاصل 15 ثانية + jitter بين الرسايل، ومفيش إرسال لرقم مش متسجل على واتساب (`isRegisteredUser` قبل الإرسال، والنتيجة تتخزن).
  - رسايل قصيرة شخصية، ومتنوعة شوية (القوالب فيها الاسم والميعاد).
  - يفضل رقم العيادة الحقيقي اللي المرضى بيكلموه أصلًا (المرضى بيحفظوه → أقل بلاغات).
- **الـprovider قابل للتبديل**: `WhatsAppCloudProvider` (Meta الرسمي، بقوالب معتمدة) يتضاف بعدين من غير تغيير المحرك.
- الواتساب واقع → كل رسايل الواتساب بتفضل `pending` بـretries، و(لو مفعّل) الإيميل fallback، وتحذير في الداشبورد.

## 6.7 الرسايل الواردة (مجهزة، التنفيذ في المستقبل)
البوابة بتعمل forward لـ`message` events على نفس الـwebhook. في v1 بيتسجلوا بس (`InboundMessage`) ويظهروا في كارت المريض.
المرحلة الجاية: الرد بـ"1" تأكيد / "2" إلغاء على التذكير يغيّر الحالة تلقائيًا (10-future-features).

## 6.8 شاشة سجل الرسايل
فلترة بالحالة/النوع/اليوم. لكل رسالة: المريض، النوع، القناة، الميعاد المجدول، الحالة + السبب، ✓✓/👁، زرار "إعادة المحاولة" للفاشل
و"إلغاء" للـpending. وفي كارت الحجز: timeline الرسايل بتاعته.
