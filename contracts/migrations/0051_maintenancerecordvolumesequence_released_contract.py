from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0050_maintenancerecord_is_archived"),
    ]

    operations = [
        migrations.AddField(
            model_name="maintenancerecordvolumesequence",
            name="released_contract",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="released_record_volume_sequences",
                to="contracts.contract",
                verbose_name="释放来源合同",
            ),
        ),
    ]
