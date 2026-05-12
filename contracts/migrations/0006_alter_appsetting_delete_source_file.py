# 调整上传时删除原文件开关的显示名称。
from django.db import migrations, models


# 迁移类：声明本次数据库结构变更和依赖关系。
class Migration(migrations.Migration):

    dependencies = [
        ("contracts", "0005_contractfile"),
    ]

    operations = [
        migrations.AlterField(
            model_name="appsetting",
            name="delete_source_file",
            field=models.BooleanField(default=False, verbose_name="上传时是否删除原文件"),
        ),
    ]
