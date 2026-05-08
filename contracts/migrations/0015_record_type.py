from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0014_settlementfile"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoicerecord",
            name="record_type",
            field=models.CharField(blank=True, max_length=20, verbose_name="类型"),
        ),
        migrations.AddField(
            model_name="paymentrecord",
            name="record_type",
            field=models.CharField(blank=True, max_length=20, verbose_name="类型"),
        ),
    ]
