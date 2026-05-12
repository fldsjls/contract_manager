from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("contracts", "0025_contract_archived_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="OperationLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("username", models.CharField(blank=True, max_length=150, verbose_name="用户名")),
                ("role", models.CharField(blank=True, max_length=50, verbose_name="角色")),
                ("action", models.CharField(max_length=50, verbose_name="动作")),
                ("object_type", models.CharField(blank=True, max_length=100, verbose_name="对象类型")),
                ("object_name", models.CharField(blank=True, max_length=255, verbose_name="对象名称")),
                ("object_id", models.CharField(blank=True, max_length=50, verbose_name="对象ID")),
                ("detail", models.TextField(blank=True, verbose_name="详情")),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True, verbose_name="IP地址")),
                ("created_at", models.DateTimeField(default=django.utils.timezone.now, verbose_name="操作时间")),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="操作用户",
                    ),
                ),
            ],
            options={
                "verbose_name": "操作日志",
                "verbose_name_plural": "操作日志",
                "ordering": ["-created_at", "-id"],
            },
        ),
    ]
