from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0024_alter_contract_archive_years"),
    ]

    operations = [
        migrations.AddField(
            model_name="contract",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="归档时间"),
        ),
        migrations.AddField(
            model_name="contract",
            name="is_archived",
            field=models.BooleanField(default=False, verbose_name="是否归档"),
        ),
    ]
