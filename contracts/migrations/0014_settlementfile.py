from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone

import contracts.models


class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0013_maintenancerecord"),
    ]

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
