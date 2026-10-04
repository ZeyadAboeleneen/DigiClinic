# 12 — Booking Engine (محرك الحجز)

كل المنطق في `apps/scheduling/services.py` (+ `availability.py`). الـviews بتنادي الـservices بس.
**كل دالة هنا ليها tests** (الحالات المذكورة تحت كل جزء = test cases إجبارية).

## 1) الجدول الفعلي ليوم معين

```python
def periods_for(doctor, day: date) -> list[Period]:
    # 1. لو فيه ScheduleException(kind=closed) → []
    # 2. لو فيه custom → فترة الاستثناء بدل الأسبوعي
    # 3. لو فيه extra → فترة الاستثناء + الأسبوعي
    # 4. غير كده → WorkingPeriod للـweekday الفعّالة في التاريخ ده (effective_from/to)
```
`Period` = (start_at aware, end_at aware, max_patients, period_id). التحويل لـaware بـ`zoneinfo("Africa/Cairo")`
(التوقيت الصيفي بيتحسب صح تلقائيًا).

Tests: يوم خميس مقفول أسبوعيًا · إجازة يوم ثلاثاء · مواعيد مختلفة يوم معين · يوم إضافي جمعة ·
تغيير الجدول من أول الشهر الجاي ميأثرش على الشهر ده · يوم تغيير التوقيت الصيفي.

## 2) طريقة الـSlots (ميعاد بالدقيقة)

```python
def free_slots(doctor, visit_type, day) -> list[Slot]:
    duration = visit_type.duration_minutes or doctor.slot_minutes
    # لكل period: مواعيد كل `doctor.slot_minutes` من البداية
    # الميعاد صالح لو [start, start+duration) جوه الفترة ومش متداخل مع أي حجز active
    # يتشال: اللي فات + اللي أقل من min_notice_minutes + اللي بعد booking_horizon_days
```
- **Active statuses** = booked, confirmed, arrived, in_consultation, completed.
- الشاشة بتعرض أيام (14 يوم قدام افتراضيًا، والسكرتيرة تقلب) وتحت كل يوم الـchips الفاضية.
- كشف 20 دقيقة وإعادة 10 دقايق في نفس الجدول: الـgrid بالـ`slot_minutes` (مثلًا 10)، والكشف بياخد 2 slots.

Tests: الدكتور من 2 لـ9 بكشف 20 دقيقة → أول ميعاد 2:00 وآخر ميعاد 8:40 · حجز 3:00 بيخفي 3:00 و3:10 لكشف 20 دقيقة ·
النهارده الساعة 4:05 → أول ميعاد متاح 4:10 · الحجوزات الملغية مش بتشغل مكان.

## 3) طريقة الـQueue (رقم دور)

```python
def queue_availability(doctor, day) -> list[PeriodAvailability]:
    # لكل period: booked_count (active) ، remaining = max_patients - booked_count
    # next_number = DayLedger.next_queue_number[period]
    # estimated_time = period.start + (number - 1) * doctor.queue_avg_minutes
```
- الأرقام **مش بتترجع** لو حد لغى (عشان الرقم ميتكررش في نفس اليوم ومحدش يتلخبط). الطاقة بس هي اللي بتفضى.
- الميعاد التقريبي بيظهر للسكرتيرة وفي رسالة التأكيد كـ"حوالي الساعة 3:40".
- `allow_overbooking`: زرار "حجز استثنائي" (للدكتور/owner) بيتخطى الطاقة ويتعلم `is_overbooked`.

Tests: الطاقة 20 والمحجوز 20 → اليوم ممتلئ · لغى رقم 5 → الطاقة 1 فاضية والرقم الجاي 21 · ميعاد رقم 7 بمتوسط 15 دقيقة من 2:00 = 3:30.

## 4) الحجز (Concurrency-safe)

```python
@transaction.atomic
def book(*, patient, doctor, visit_type, start_at=None, day=None, period=None, by, source, overbook=False):
    ledger = DayLedger.objects.select_for_update().get_or_create(doctor=doctor, date=day)  # قفل اليوم
    # slots: إعادة التحقق إن الميعاد لسه فاضي (تحت القفل) → غير كده SlotTaken
    # queue: التحقق من الطاقة → رقم الدور من ledger
    # السعر: visit_type.price، أو 0 لو إعادة مجانية (فيه كشف completed خلال free_followup_days)
    # إنشاء Appointment(version=1) + AppointmentEvent(created)
    # transaction.on_commit → notifications.schedule_for(appointment)
```
- **منع التكرار**: لو المريض عنده حجز active لنفس الدكتور في نفس اليوم → تحذير (مش منع) "عنده حجز الساعة 4:00".
- **مريض عنده غياب كتير** (`no_show_count >= 3`): كارت المريض بيعرض علامة تنبيه.

Tests: اتنين بيحجزوا نفس الميعاد في نفس اللحظة (threads) → واحد بس ينجح والتاني ياخد `SlotTaken` ·
آخر رقم في الطاقة لاتنين في نفس اللحظة · إعادة خلال 14 يوم من كشف = سعر 0 · بعد 15 يوم = سعر الإعادة العادي.

## 5) التعديل والإلغاء

```python
def reschedule(appt, *, new_start_at=None, new_day=None, new_period=None, by, reason="") -> Appointment
def cancel(appt, *, by, reason="", notify=True)
```
- **التأجيل** = الحجز القديم `status=rescheduled` + `rescheduled_to` → حجز جديد (نفس المريض/النوع/السعر).
  ده بيحافظ على التاريخ ("اتأجل مرتين") وبيخلي التقارير صح.
  - تغيير **ميعاد** نفس الحجز (من غير تغيير يوم) برضه بيعمل نفس الحاجة للبساطة. كل تغيير بيزود `version`.
- `on_commit`: الرسايل `pending` للحجز القديم بتتلغي (`status_reason="اتعدل الميعاد"`)، ورسالة `rescheduled` بتتبعت،
  والتذكيرات الجديدة بتتجدول.
- **قفل يوم فيه حجوزات** (ScheduleException closed): شاشة "الحجوزات المتأثرة" بتعرض كل حجز مع أقرب ميعاد فاضي مقترح،
  والسكرتيرة تأكد واحد واحد أو "تأجيل الكل للمقترح" (كل واحد بياخد رسالة تعديل). مفيش حجوزات بتتلغي في صمت.
- **تعديل جدول العمل** (WorkingPeriod) بيأثر على الحجوزات الجديدة بس. لو فيه حجوزات قايمة بقت برّه الجدول → نفس الشاشة.

## 6) دورة حالة الحجز

```
booked ──(رد المريض/السكرتيرة)──► confirmed
booked/confirmed ──وصل──► arrived ──استدعاء──► in_consultation ──إنهاء الكشف──► completed
booked/confirmed ──(مهلة السماح / آخر الفترة)──► no_show
أي حالة قبل arrived ──► cancelled / rescheduled
```
- Transitions مسموحة بس من الـservice (`transition(appt, to, by)`) — أي انتقال غلط → `InvalidTransition`.
- كل انتقال → `AppointmentEvent`.
- **رجوع خطوة** (undo) مسموح خلال 10 دقايق للسكرتيرة (ضغطت "لم يحضر" بالغلط → ترجع booked). بعد كده owner بس.

## 7) الغياب (No-show)

`mark_no_shows()` job في الـscheduler (كل دقيقتين):
- **slots**: الحجوزات booked/confirmed اللي `start_at + no_show_grace_minutes < now` → `no_show`.
- **queue**: لو `no_show_queue_mark_at=period_end` → بعد نهاية الفترة. لو `manual` → السكرتيرة بس.
- بعد `no_show`: `patient.no_show_count += 1` ثم حسب `no_show_policy`:
  - `auto_rebook`: `find_rebook_slot(appt)` = أول ميعاد فاضي بعد `auto_rebook_after_days`، **في نفس الوقت تقريبًا من اليوم**
    (أقرب slot لساعة الميعاد الأصلي)، خلال `auto_rebook_window_days`. ينجح → `book(source=auto_rebook, auto_rebooked_from=appt)`
    + رسالة `no_show_rebooked` ("حجزنالك يوم … الساعة …، لو مش مناسب كلمنا على …"). مفيش مكان → رسالة `no_show_missed`.
  - **مفيش إعادة حجز تلقائي لحجز اتعمل أصلًا بإعادة حجز تلقائي** (يعني مرة واحدة بس) → المرة التانية `no_show_missed`.
  - `notify`: رسالة `no_show_missed` بس.
  - `none`: ولا حاجة.

Tests: ميعاد 3:00 ومهلة 30 دقيقة → 3:29 لسه booked و3:31 no_show · إعادة الحجز في نفس الساعة بعد 7 أيام ·
اليوم ده مقفول → اليوم اللي بعده · حجز اتعمل بإعادة تلقائية وغاب تاني → رسالة بس.

## 8) Walk-in (حجز فوري)
زرار في شاشة الاستقبال: بيعمل حجز لنفس اليوم (slots: أقرب ميعاد فاضي أو "استثنائي". queue: الرقم الجاي) وبيعمله `arrived` على طول.
رسالة التأكيد **مش بتتبعت** للـwalk-in (`source=walk_in` → skip).
