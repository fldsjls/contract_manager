from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0022_appsetting_preview_root_path"),
    ]

    operations = [
        migrations.AddField(
            model_name="contract",
            name="archive_years",
            field=models.PositiveSmallIntegerField(default=3, verbose_name="归档时间"),
        ),
    ]
