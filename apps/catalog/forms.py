from django import forms
from django.utils.translation import gettext_lazy as _

from .models import Category, Product, ProductGroup, Unit


class ProductForm(forms.ModelForm):
    unit_name = forms.CharField(label=_("الوحدة"), max_length=50)

    class Meta:
        model = Product
        fields = ["name", "category"]

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.instance.organization = organization
        self.fields["category"].queryset = Category.objects.for_org(organization).select_related("group")
        self.fields["category"].label_from_instance = lambda c: f"{c.group.name} ← {c.name}"
        if self.instance.pk:
            self.fields["unit_name"].initial = self.instance.unit.name

    def clean(self):
        cleaned = super().clean()
        name, category = " ".join(cleaned.get("name", "").split()), cleaned.get("category")
        if name and category:
            dup = Product.objects.filter(organization=self.organization, category=category, name=name)
            if dup.exclude(pk=self.instance.pk).exists():
                self.add_error("name", _("الصنف ده موجود بالفعل في نفس القسم."))
        return cleaned

    def save(self, commit=True):
        self.instance.unit = Unit.get_for(self.organization, self.cleaned_data["unit_name"])
        if not self.instance.pk or "category" in self.changed_data:
            last = Product.objects.filter(category=self.cleaned_data["category"]).order_by("-sort_order").first()
            self.instance.sort_order = (last.sort_order + 1) if last else 1
        return super().save(commit)


class CategoryForm(forms.ModelForm):
    class Meta:
        model = Category
        fields = ["name", "group"]

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.instance.organization = organization
        self.fields["group"].queryset = ProductGroup.objects.for_org(organization)

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        dup = Category.objects.filter(organization=self.organization, name=name).exclude(pk=self.instance.pk)
        if dup.exists():
            raise forms.ValidationError(_("فيه قسم بنفس الاسم."))
        return name

    def save(self, commit=True):
        if not self.instance.pk or "group" in self.changed_data:
            last = Category.objects.filter(group=self.cleaned_data["group"]).order_by("-sort_order").first()
            self.instance.sort_order = (last.sort_order + 1) if last else 1
        return super().save(commit)


class GroupForm(forms.ModelForm):
    class Meta:
        model = ProductGroup
        fields = ["name", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, organization, **kwargs):
        super().__init__(*args, **kwargs)
        self.organization = organization
        self.instance.organization = organization

    def clean_name(self):
        name = " ".join(self.cleaned_data["name"].split())
        dup = ProductGroup.objects.filter(organization=self.organization, name=name).exclude(pk=self.instance.pk)
        if dup.exists():
            raise forms.ValidationError(_("فيه مجموعة بنفس الاسم."))
        return name

    def save(self, commit=True):
        if not self.instance.pk:
            last = ProductGroup.objects.for_org(self.organization).order_by("-sort_order").first()
            self.instance.sort_order = (last.sort_order + 1) if last else 1
        return super().save(commit)
