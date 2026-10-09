from django.db import migrations


def backfill(apps, schema_editor):
    """Give quote requests made before tracking existed a reference and a first update."""
    RFQ = apps.get_model("cms", "RFQ")
    RFQUpdate = apps.get_model("cms", "RFQUpdate")
    counters = {}
    for rfq in RFQ.objects.filter(reference__isnull=True).order_by("created_at", "pk"):
        year = rfq.created_at.year
        counters[year] = counters.get(year, 0) + 1
        rfq.reference_year = year
        rfq.reference_number = counters[year]
        rfq.reference = f"RFQ-{year}-{counters[year]:04d}"
        rfq.save(update_fields=["reference_year", "reference_number", "reference"])
        RFQUpdate.objects.create(rfq=rfq, stage="received", headline="We have your request", created_at=rfq.created_at)


class Migration(migrations.Migration):
    dependencies = [("cms", "0022_tracking")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
