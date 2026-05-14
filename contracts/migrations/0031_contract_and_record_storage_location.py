from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0030_remove_appsetting_delete_source_file"),
    ]

    operations = [
        migrations.AddField(
            model_name="contract",
            name="storage_location_number",
            field=models.CharField(blank=True, default="00", max_length=100, verbose_name="存储位置编号"),
        ),
        migrations.AddField(
            model_name="maintenancerecord",
            name="storage_location_number",
            field=models.CharField(blank=True, default="00", max_length=100, verbose_name="存储位置编号"),
        ),
    ]
