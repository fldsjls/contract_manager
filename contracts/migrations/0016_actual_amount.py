from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0015_record_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoicerecord",
            name="actual_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True, verbose_name="实际金额"),
        ),
        migrations.AddField(
            model_name="paymentrecord",
            name="actual_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True, verbose_name="实际金额"),
        ),
    ]
