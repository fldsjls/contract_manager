# json 用于把用户设置保存到本地文件。
import json
# Path 用于定位 data/settings.json。
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS_PATH = BASE_DIR / "data" / "settings.json"
DEFAULT_SETTINGS = {
    "delete_source_after_archive": False,
}


# 读取用户设置；配置文件不存在或损坏时使用默认设置。
def load_settings() -> dict:
    if not SETTINGS_PATH.exists():
        return DEFAULT_SETTINGS.copy()
    try:
        with SETTINGS_PATH.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SETTINGS.copy()

    settings = DEFAULT_SETTINGS.copy()
    settings.update(data)
    return settings


# 保存用户设置到 data/settings.json。
def save_settings(settings: dict) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with SETTINGS_PATH.open("w", encoding="utf-8") as file:
        json.dump(settings, file, ensure_ascii=False, indent=2)
