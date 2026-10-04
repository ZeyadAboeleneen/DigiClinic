from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Doctor, ScheduleException, VisitType, WorkingPeriod


class DoctorForm(forms.ModelForm):
    qualifications_text = forms.CharField(
        label=_("المؤهلات"),
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text=_("سطر لكل مؤهل، بيظهروا في هيدر الروشتة."),
    )

    class Meta:
        model = Doctor
        fields = [
            "name_ar",
            "name_en",
            "title_ar",
            "specialty_ar",
            "booking_mode",
            "slot_minutes",
            "booking_horizon_days",
            "min_notice_minutes",
            "queue_avg_minutes",
            "near_turn_threshold",
            "allow_overbooking",
        ]
        widgets = {"name_en": forms.TextInput(attrs={"dir": "ltr"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["qualifications_text"].initial = "\n".join(self.instance.qualifications or [])

    def save(self, commit=True):
        self.instance.qualifications = [
            line.strip() for line in self.cleaned_data["qualifications_text"].splitlines() if line.strip()
        ]
        return super().save(commit)


class WorkingPeriodForm(forms.ModelForm):
    class Meta:
        model = WorkingPeriod
        fields = ["weekday", "start_time", "end_time", "max_patients", "effective_from", "effective_to"]
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
            "effective_from": forms.DateInput(attrs={"type": "date"}),
            "effective_to": forms.DateInput(attrs={"type": "date"}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if start and end and end <= start:
            self.add_error("end_time", _("وقت النهاية لازم يكون بعد وقت البداية."))
        return cleaned


class ScheduleExceptionForm(forms.ModelForm):
    class Meta:
        model = ScheduleException
        fields = ["date", "kind", "start_time", "end_time", "max_patients", "reason"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "start_time": forms.TimeInput(attrs={"type": "time"}),
            "end_time": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if kind in ("custom", "extra"):
            if not start or not end:
                raise forms.ValidationError(_("محتاج وقت البداية والنهاية لليوم ده."))
            if end <= start:
                self.add_error("end_time", _("وقت النهاية لازم يكون بعد وقت البداية."))
        return cleaned


class VisitTypeForm(forms.ModelForm):
    class Meta:
        model = VisitType
        fields = ["name_ar", "duration_minutes", "price", "is_followup", "free_followup_days", "color", "is_active"]
        widgets = {"color": forms.TextInput(attrs={"type": "color"})}
