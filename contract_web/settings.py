from pathlib import Path


# 项目根目录路径，后续数据库、静态文件和媒体文件都基于它定位。
BASE_DIR = Path(__file__).resolve().parent.parent

# 开发环境使用的密钥和调试开关。
SECRET_KEY = "django-insecure-contract-manager-lan-dev-key"
DEBUG = True
ALLOWED_HOSTS = ["*"]
X_FRAME_OPTIONS = "SAMEORIGIN"

# 当前 Django 项目启用的内置应用和合同应用。
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "contracts",
]

# 请求进入视图前后依次执行的中间件。
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# 根路由配置文件。
ROOT_URLCONF = "contract_web.urls"

# 模板加载配置，允许 Django 查找应用内模板。
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "contracts.labels.labels_context",
            ],
        },
    },
]

# WSGI 服务入口。
WSGI_APPLICATION = "contract_web.wsgi.application"

# 使用 SQLite 作为本地数据库。
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# 中文和上海时区配置。
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

# 静态文件和上传文件的访问路径与存储路径。
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# 默认主键类型。
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
