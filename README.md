# 合同管理系统

这是一个基于 Django 的局域网合同管理系统，适合在办公室内由一台主机运行，其他电脑通过浏览器访问使用。

## 技术栈

- Python 3.12+
- Django 5+
- SQLite
- HTML / CSS / JavaScript
- PDF.js 用于 PDF 网页预览

## 主要功能

- 管理员登录和游客模式
- 游客可查看数据，管理员可新增、编辑、删除和恢复
- 合同列表展示、搜索和表头排序
- 新增、编辑、删除合同
- 删除合同进入回收站，默认保留 7 天，可由管理员恢复
- 合同类型：维保、评估、检测、改造、新建、其他
- 维保合同必须填写截止日期
- 自动计算合同状态：进行中、即将到期、已到期
- 合同文件支持多文件上传
- 文件按项目名称自动归档到对应文件夹
- 合同文件可拖拽调整顺序
- 合同详情页只显示首个合同文件
- 文件支持预览、下载、删除
- PDF 使用 PDF.js 在网页内预览
- 图片支持网页内预览
- Word、Excel 等文件保留下载入口
- 开票记录和收票记录作为合同子记录
- 开票/收票记录支持批量添加
- 统计总览显示总金额、总开票、总收票、收款率
- 开票/收票趋势图
- 即将到期项目列表
- 最近项目列表
- 设置页支持“上传时是否删除原文件”
- 设置页显示局域网访问地址
- 修改管理员密码

## 项目结构

```text
contract_manager/
├─ manage.py
├─ requirements.txt
├─ README.md
├─ db.sqlite3
├─ media/
│  └─ contracts/
├─ contract_web/
│  ├─ settings.py
│  ├─ urls.py
│  ├─ wsgi.py
│  └─ asgi.py
└─ contracts/
   ├─ models.py
   ├─ views.py
   ├─ forms.py
   ├─ urls.py
   ├─ admin.py
   ├─ migrations/
   └─ templates/
      └─ contracts/
```

## 安装依赖

建议先进入虚拟环境：

```powershell
.\.venv\Scripts\activate
```

安装依赖：

```powershell
pip install -r requirements.txt
```

## 初始化数据库

首次运行或更新代码后执行：

```powershell
python manage.py migrate
```

## 创建管理员账号

```powershell
python manage.py createsuperuser
```

按提示输入用户名、邮箱和密码。管理员账号用于新增、编辑、删除、恢复合同，以及进入设置页。

## 启动系统

本机测试：

```powershell
python manage.py runserver
```

局域网访问：

```powershell
python manage.py runserver 0.0.0.0:8000
```

主机浏览器访问：

```text
http://127.0.0.1:8000
```

局域网其他电脑访问：

```text
http://主机局域网IP:8000
```

例如：

```text
http://192.168.1.100:8000
```

主机 IP 也可以在系统的“设置”页面底部查看。

## 登录说明

系统提供两种模式：

- 管理员模式：需要账号密码，可以新增、编辑、删除、恢复和设置系统。
- 游客模式：点击“游客进入”即可访问，只能查看数据。

游客点击“设置”时会弹出提示，可选择登录或取消。

## 文件管理说明

合同文件、开票附件、收票附件都会按合同项目名称保存到：

```text
media/contracts/项目名称/
```

合同文件支持：

- 多文件上传
- 拖拽调整顺序
- 预览
- 下载
- 删除

合同详情页只显示排序第一的合同文件。文件名过长时会自动省略。

PDF 预览使用 PDF.js。如果局域网电脑无法访问外网 CDN，PDF 预览可能失败，但下载功能仍可使用。

## 回收站说明

删除合同后不会立即永久删除，而是进入回收站。

- 回收站默认保留 7 天
- 管理员可以恢复合同
- 游客只能查看回收站
- 超过 7 天的删除项目会在访问系统时自动清理

## 常用命令

检查项目配置：

```powershell
python manage.py check
```

执行数据库迁移：

```powershell
python manage.py migrate
```

启动局域网服务：

```powershell
python manage.py runserver 0.0.0.0:8000
```

创建管理员：

```powershell
python manage.py createsuperuser
```

## 注意事项

- 当前项目使用 SQLite，适合小团队和局域网轻量使用。
- 请定期备份 `db.sqlite3` 和 `media/` 文件夹。
- 如果局域网其他电脑无法访问，请检查主机防火墙是否允许 8000 端口。
- 开发服务器适合局域网内部使用；正式长期部署建议使用 Waitress、Nginx 或其他生产部署方式。
- PDF.js 当前通过 CDN 加载，如需完全离线使用，可将 PDF.js 下载到本地 static 目录后改为本地引用。
