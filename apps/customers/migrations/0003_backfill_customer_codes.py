from django.db import migrations


def backfill(apps, schema_editor):
    from apps.customers.codes import suggest_code

    Customer = apps.get_model("customers", "Customer")
    taken_by_org = {}
    for c in Customer.objects.order_by("id"):
        taken = taken_by_org.setdefault(c.organization_id, set())
        if c.code:
            taken.add(c.code)
            continue
        c.code = suggest_code(c.name, taken)
        taken.add(c.code)
        c.save(update_fields=["code"])


class Migration(migrations.Migration):
    dependencies = [("customers", "0002_customer_code")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
