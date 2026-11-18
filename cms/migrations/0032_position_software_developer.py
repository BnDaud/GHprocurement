from django.db import migrations


def add_position(apps, schema_editor):
    Designation = apps.get_model("cms", "Designation")
    last = Designation.objects.order_by("-position").values_list("position", flat=True).first() or 0
    Designation.objects.get_or_create(name="Software Developer", defaults={"position": last + 1})


class Migration(migrations.Migration):

    dependencies = [
        ("cms", "0031_staff_designations_service_charge"),
    ]

    operations = [
        migrations.RunPython(add_position, migrations.RunPython.noop),
    ]
