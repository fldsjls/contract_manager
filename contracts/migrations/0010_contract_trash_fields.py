from django.db import migrations, models


# 为合同增加回收站状态字段和删除时间字段。
# 迁移类：声明本次数据库结构变更和依赖关系。
class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0009_project_file_upload_paths"),
    ]

    operations = [
        migrations.AddField(
            model_name="contract",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="删除时间"),
        ),
        migrations.AddField(
            model_name="contract",
            name="is_deleted",
            field=models.BooleanField(default=False, verbose_name="是否删除"),
        ),
    ]
