# Add an optional负责人 field to contracts for list display and follow-up ownership.

from django.db import migrations, models


# 迁移类：声明本次数据库结构变更和依赖关系。
class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0016_actual_amount"),
    ]

    operations = [
        migrations.AddField(
            model_name="contract",
            name="responsible_person",
            field=models.CharField(blank=True, max_length=100, verbose_name="负责人"),
        ),
    ]
