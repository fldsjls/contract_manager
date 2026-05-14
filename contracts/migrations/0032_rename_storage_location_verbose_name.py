from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0031_contract_and_record_storage_location"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contract",
            name="storage_location_number",
            field=models.CharField(blank=True, default="00", max_length=100, verbose_name="存储编号"),
        ),
        migrations.AlterField(
            model_name="maintenancerecord",
            name="storage_location_number",
            field=models.CharField(blank=True, default="00", max_length=100, verbose_name="存储编号"),
        ),
    ]
