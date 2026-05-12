import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0023_contract_archive_years"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contract",
            name="archive_years",
            field=models.PositiveSmallIntegerField(
                default=3,
                validators=[django.core.validators.MinValueValidator(1)],
                verbose_name="归档时间",
            ),
        ),
    ]
