"""Default message templates (06 §6.3). The clinic edits them from the templates screen."""

from .models import AppliesTo, Event

DEFAULT_TEMPLATES = [
    {
        "event": Event.BOOKING_CONFIRMED,
        "name_ar": "تأكيد الحجز",
        "body": (
            "أهلًا {first_name} 👋\n"
            "تم حجز {visit_type} مع {doctor_name} يوم {date}، {time_label}.\n"
            "العنوان: {clinic_address}\n"
            "{map_link}\n"
            "للتعديل أو الإلغاء: {clinic_phone}"
        ),
        "email_subject": "تأكيد حجزك",
    },
    {
        "event": Event.REMINDER,
        "name_ar": "تذكير قبلها بيوم",
        "offset_minutes": 1440,
        "min_lead_minutes": 120,
        "body": (
            "تذكير: ميعادك مع {doctor_name} بكرة {date}، {time_label}.\n"
            "لو مش هتقدر تيجي يا ريت تبلغنا على {clinic_phone} عشان نِدّي الميعاد لحد تاني."
        ),
        "email_subject": "تذكير بميعادك بكرة",
    },
    {
        "event": Event.REMINDER,
        "name_ar": "تذكير قبلها بساعة",
        "offset_minutes": 60,
        "min_lead_minutes": 10,
        "applies_to_mode": AppliesTo.SLOTS,
        "body": "ميعادك مع {doctor_name} النهارده {time_label} — فاضل حوالي ساعة. في انتظارك 🌿",
        "email_subject": "ميعادك بعد ساعة",
    },
    {
        "event": Event.RESCHEDULED,
        "name_ar": "تعديل الميعاد",
        "body": "تم تعديل ميعادك مع {doctor_name} من {old_date} {old_time} إلى {date}، {time_label}.",
        "email_subject": "تعديل ميعادك",
    },
    {
        "event": Event.CANCELLED,
        "name_ar": "إلغاء الحجز",
        "body": "تم إلغاء ميعادك مع {doctor_name} يوم {date}. للحجز من جديد: {clinic_phone}",
        "email_subject": "إلغاء ميعادك",
    },
    {
        "event": Event.NEAR_TURN,
        "name_ar": "دورك قرّب",
        "applies_to_mode": AppliesTo.QUEUE,
        "body": "{first_name}، دورك قرّب 🔔 قدامك {patients_ahead} بس. يا ريت تكون في العيادة.",
        "email_subject": "دورك قرّب",
    },
    {
        "event": Event.NO_SHOW_REBOOKED,
        "name_ar": "غياب + إعادة حجز",
        "body": (
            "افتقدناك النهارده يا {first_name}. حجزنالك ميعاد جديد يوم {date}، {time_label}.\n"
            "لو مش مناسب كلمنا على {clinic_phone}."
        ),
        "email_subject": "ميعادك الجديد",
    },
    {
        "event": Event.NO_SHOW_MISSED,
        "name_ar": "غياب (افتقدناك)",
        "body": "افتقدناك النهارده يا {first_name}. تقدر تحجز ميعاد جديد على {clinic_phone}.",
        "email_subject": "افتقدناك",
    },
    {
        "event": Event.PRESCRIPTION,
        "name_ar": "الروشتة",
        "attach_pdf": True,
        "body": "{first_name}، دي روشتة زيارتك النهارده مع {doctor_name} (رقم {rx_number}). ألف سلامة عليك.",
        "email_subject": "روشتة زيارتك",
    },
]
