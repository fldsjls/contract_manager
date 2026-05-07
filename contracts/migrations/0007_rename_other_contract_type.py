# 将合同类型“其他项目”改为“其他”。
from django.db import migrations, models


def rename_other_project_to_other(apps, schema_editor):
    # 同步已有数据库中的旧合同类型名称。
    Contract = apps.get_model("contracts", "Contract")
    Contract.objects.filter(contract_type="其他项目").update(contract_type="其他")


def rename_other_to_other_project(apps, schema_editor):
    # 回滚迁移时恢复旧合同类型名称。
    Contract = apps.get_model("contracts", "Contract")
    Contract.objects.filter(contract_type="其他").update(contract_type="其他项目")


class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0006_alter_appsetting_delete_source_file"),
    ]

    operations = [
        migrations.RunPython(rename_other_project_to_other, rename_other_to_other_project),
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
                default="其他",
                max_length=20,
                verbose_name="合同类型",
            ),
        ),
    ]
