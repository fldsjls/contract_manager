# 合同管理系统

一个基于 Python、PySide6 和 SQLite 的本地桌面合同管理系统。程序首次运行会自动创建本地数据库文件 `data/contracts.db`。

## 功能

- 新增、编辑、删除合同信息
- 表格展示合同列表
- 按合同名称、合同编号、对方名称搜索
- 保存合同扫描件或 PDF 文件路径
- 双击合同行打开对应文件
- 按合同截止日期提醒 30 天内即将到期合同
- 统计合同总金额
- 显示合同状态：进行中、即将到期、已到期

## 项目结构

```text
contract_manager/
├─ main.py
├─ database.py
├─ models.py
├─ requirements.txt
├─ README.md
├─ data/
├─ contracts/
└─ ui/
   ├─ __init__.py
   ├─ main_window.py
   ├─ contract_form.py
   └─ stats_window.py
```

## 环境要求

- Python 3.12 或更高版本
- Windows、macOS 或 Linux 桌面环境

## 安装

进入项目目录：

```powershell
cd C:\Users\YF\Desktop\contract_manager
```

创建虚拟环境：

```powershell
python -m venv .venv
```

Windows 激活虚拟环境：

```powershell
.venv\Scripts\activate
```

macOS / Linux 激活虚拟环境：

```bash
source .venv/bin/activate
```

安装依赖：

```powershell
pip install -r requirements.txt
```

如果虚拟环境不可用，可以删除 `.venv` 后重新创建：

```powershell
Remove-Item -Recurse -Force .venv
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 运行

```powershell
python main.py
```

首次运行后会自动创建：

```text
data/contracts.db
```

## 使用说明

- 点击“新增”录入合同。
- 选中一行后点击“编辑”修改合同。
- 选中一行后点击“删除”删除合同。
- 在关键词输入框中输入合同名称、合同编号或对方名称，然后点击“搜索”。
- 在新增或编辑表单中选择合同扫描件/PDF 文件路径。
- 在合同列表中双击某一行，可使用系统默认程序打开对应文件。
- 点击“统计”查看合同总金额、状态数量和 30 天内即将到期合同。

## 状态规则

- 已到期：截止日期早于今天
- 即将到期：截止日期为今天起 30 天内
- 进行中：截止日期超过 30 天，或未能识别截止日期

## 打包

安装依赖后执行：

```powershell
pyinstaller --noconfirm --windowed --name ContractTracker main.py
```

打包完成后，可执行文件位于：

```text
dist/ContractTracker/
```

如果希望把数据库和合同文件保留在程序目录旁边，建议将 `data/` 和 `contracts/` 文件夹与打包后的可执行文件放在同一业务目录下使用。
