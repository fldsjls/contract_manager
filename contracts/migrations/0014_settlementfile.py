from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

import contracts.models


# 新增结算文件表，把结算附件从普通合同文件中单独分目录保存。
# 迁移类：声明本次数据库结构变更和依赖关系。
class Migration(migrations.Migration):

    # 结算文件依赖合同主表和统一上传路径函数。
    dependencies = [
        ("contracts", "0013_maintenancerecord"),
    ]

    # 每个结算文件都关联一个合同，并保存原文件名用于页面展示。
    operations = [
        migrations.CreateModel(
            name="SettlementFile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(upload_to=contracts.models.project_file_upload_path, verbose_name="结算文件")),
                ("original_name", models.CharField(blank=True, max_length=255, verbose_name="原文件名")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="上传时间")),
                (
                    "contract",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="settlement_files",
                        to="contracts.contract",
                        verbose_name="所属合同",
                    ),
                ),
            ],
            options={
                "verbose_name": "结算文件",
                "verbose_name_plural": "结算文件",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
