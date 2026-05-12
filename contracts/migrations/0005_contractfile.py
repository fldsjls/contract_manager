# 添加合同多文件附件表。
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


# 迁移类：声明本次数据库结构变更和依赖关系。
class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0004_alter_appsetting_delete_source_file"),
    ]

    operations = [
        migrations.CreateModel(
            name="ContractFile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to="contract_files/", verbose_name="合同文件")),
                ("original_name", models.CharField(blank=True, max_length=255, verbose_name="原文件名")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="上传时间")),
                (
                    "contract",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="files",
                        to="contracts.contract",
                        verbose_name="所属合同",
                    ),
                ),
            ],
            options={
                "verbose_name": "合同文件",
                "verbose_name_plural": "合同文件",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
