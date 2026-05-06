# 合同管理系统 Django 局域网版

当前分支用于从零开始开发 Django Web 版合同管理系统。

## 安装依赖

```bash
pip install -r requirements.txt
```

## 初始化数据库

```bash
python manage.py makemigrations
python manage.py migrate
```

## 创建管理员账号

```bash
python manage.py createsuperuser
```

## 启动局域网服务

```bash
python manage.py runserver 0.0.0.0:8000
```

同一局域网其他电脑访问：

```text
http://你的电脑局域网IP:8000
```

## 当前已包含

- Django 项目骨架
- contracts 应用
- Contract 合同模型
- Django admin 管理入口
- 合同列表首页
- 媒体文件上传目录配置

## 下一步建议

1. 完善新增合同页面
2. 完善编辑合同页面
3. 增加删除确认
4. 增加文件上传
5. 增加到期提醒和金额统计
