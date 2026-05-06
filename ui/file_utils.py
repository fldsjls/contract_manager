# shutil 用于复制用户选择的合同、开票和收款附件。
import shutil
# Path 用于处理文件和合同归档目录路径。
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = BASE_DIR / "contracts"
COMMON_FILE_FILTER = (
    "常用文件 (*.pdf *.doc *.docx *.xls *.xlsx *.jpg *.jpeg *.png *.bmp);;"
    "PDF 文件 (*.pdf);;"
    "Word 文件 (*.doc *.docx);;"
    "Excel 文件 (*.xls *.xlsx);;"
    "图片文件 (*.jpg *.jpeg *.png *.bmp);;"
    "所有文件 (*.*)"
)


# 清理 Windows 文件夹名称中不能使用的字符。
def safe_folder_name(name: str) -> str:
    invalid_chars = '<>:"/\\|?*'
    cleaned = "".join("_" if char in invalid_chars else char for char in name.strip())
    return cleaned or "未命名合同"


# 返回某份合同的附件目录；如果不存在就自动创建。
def contract_folder(contract_name: str) -> Path:
    folder = CONTRACTS_DIR / safe_folder_name(contract_name)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# 生成不重名的目标文件路径，避免覆盖已有附件。
def unique_target_path(folder: Path, filename: str) -> Path:
    target = folder / filename
    if not target.exists():
        return target

    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = folder / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


# 把用户选择的文件复制到合同目录中，并返回复制后的文件路径。
def archive_file(source_path: str, contract_name: str) -> str:
    if not source_path:
        return ""

    source = Path(source_path)
    if not source.exists() or not source.is_file():
        return source_path

    folder = contract_folder(contract_name)
    try:
        source.resolve().relative_to(folder.resolve())
        return str(source)
    except ValueError:
        pass

    target = unique_target_path(folder, source.name)
    shutil.copy2(source, target)
    return str(target)
