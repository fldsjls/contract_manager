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
- 开票记录和收票记录子表
- 管理员登录和游客浏览模式
- Django admin 管理入口
- 合同列表首页
- 合同搜索：合同名称、合同编号、甲方名称
- 新增合同页面
- 编辑合同页面
- 删除合同确认页面
- 合同详情页，可查看开票/收票记录
- 开票/收票记录新增页面，支持附件上传
- 合同文件上传
- 合同总金额统计
- 30 天内到期提醒
- 合同状态显示：进行中、即将到期、已到期
- 维保合同必须填写截止日期
- 左侧侧栏导航
- 统计总览页面
- 总金额、总开票、总收票、收款率统计
- 开票 / 收票按日期趋势图
- 最近项目列表
- 设置页：控制删除或替换合同时是否删除已上传文件
- 媒体文件上传目录配置

## 下一步建议

1. 根据桌面版旧数据格式增加 SQLite 数据迁移脚本
2. 增加记录编辑和删除页面
3. 增加更细的用户权限和操作日志
