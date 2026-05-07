# 将新增合同的默认合同类型改为“维保”。
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0007_rename_other_contract_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="contract",
            name="contract_type",
            field=models.CharField(
                choices=[
                    ("维保", "维保"),
                    ("评估", "评估"),
                    ("检测", "检测"),
                    ("改造", "改造"),
                    ("新建", "新建"),
                    ("其他", "其他"),
                ],
                default="维保",
                max_length=20,
                verbose_name="合同类型",
            ),
        ),
    ]
