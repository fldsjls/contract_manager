from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0049_alter_contract_storage_location_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="maintenancerecord",
            name="is_archived",
            field=models.BooleanField(default=False, verbose_name="是否归档"),
        ),
    ]
