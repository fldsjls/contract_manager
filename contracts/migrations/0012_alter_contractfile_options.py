from django.db import migrations


# 同步合同文件模型的默认排序规则。
# 迁移类：声明本次数据库结构变更和依赖关系。
class Migration(migrations.Migration):
    dependencies = [
        ("contracts", "0011_contractfile_sort_order"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="contractfile",
            options={
                "ordering": ["sort_order", "id"],
                "verbose_name": "合同文件",
                "verbose_name_plural": "合同文件",
            },
        ),
    ]
