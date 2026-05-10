# 新增合同类型扩展记录表，最初用于维护保养记录，后续也复用为其他项目类型记录。

import contracts.models
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    # 依赖合同文件排序迁移，保证记录附件路径函数已经可用。
    dependencies = [
        ('contracts', '0012_alter_contractfile_options'),
    ]

    # 创建 MaintenanceRecord 表，保存日期、月份、附件和备注。
    operations = [
        migrations.CreateModel(
            name='MaintenanceRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('record_date', models.DateField(verbose_name='日期')),
                ('month', models.CharField(max_length=30, verbose_name='月份')),
                ('file', models.FileField(blank=True, null=True, upload_to=contracts.models.project_file_upload_path, verbose_name='附件')),
                ('remark', models.CharField(blank=True, max_length=255, verbose_name='备注')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('contract', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='contracts.contract', verbose_name='所属合同')),
            ],
            options={
                'verbose_name': '维护保养记录',
                'verbose_name_plural': '维护保养记录',
                'ordering': ['record_date', 'id'],
            },
        ),
    ]
