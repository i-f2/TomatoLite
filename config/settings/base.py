"""§0.6 base 片：**无任何密钥默认值**（开工基线纪律，v0.7 不变）。

所有密钥/端点一律 `env("X")`——缺 .env 即 ImproperlyConfigured，
这是对"第一次提交就有真密钥进 git 历史"那道防线（S0-3 DoD 的负断言）的正断言。
"""
from pathlib import Path

import environ

ROOT_DIR = Path(__file__).resolve().parents[2]  # 仓库根

env = environ.Env()
_env_file = ROOT_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(_env_file)

# ---- 核心 ----
SECRET_KEY = env("SECRET_KEY")  # 无默认值
DEBUG = False
ALLOWED_HOSTS: list[str] = []

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "drf_spectacular",
    "channels",
    # S0-4：八个业务 app 注册（§0.1 划分；channels_a/_b 避开 pip 包 channels 的 label 冲突）
    "apps.core",
    "apps.cmdb",
    "apps.tickets",
    "apps.channels_a",
    "apps.channels_b",
    "apps.verification",
    "apps.feedback",
    "apps.metrics",
]

# S0-5（拍板①）：认证基座 = Django session + 本地密码（ModelBackend），username 标识；
# SSO/ LDAP 属 M3 认证后端替换位——只换 backend，不动 User。
AUTH_USER_MODEL = "core.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": [
            "django.template.context_processors.request",
            "django.contrib.auth.context_processors.auth",
            "django.contrib.messages.context_processors.messages",
        ]},
    },
]
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# §0.6a：本地/生产一律 PostgreSQL（≥15，F-8 起；Django 6.1.1 连接层硬下限），SQLite 是禁区
DATABASES = {"default": env.db_url("DATABASE_URL")}

# ---- Redis / Celery（§0.5 队列拓扑在 prod.py 路由与 compose 命令行落地）----
REDIS_URL = env("REDIS_URL")
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = None  # 状态回传走 DB+done 语义；开 result backend 前须先过 §0.5 评审
CHANNEL_LAYERS = {  # base 兜底 InMemory；prod.py 换 Redis（§0.6 表）
    "default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}
}

# ---- 外部系统抽象（§0.4；§6 第 2 条：三态零分支，差异只在 base_url）----
INTEGRATION_MODE = env("INTEGRATION_MODE")  # mock | integration | prod 口径
GITLAB_BASE_URL = env("GITLAB_BASE_URL")
GITLAB_TOKEN = env("GITLAB_TOKEN")
GITLAB_WEBHOOK_SECRET = env("GITLAB_WEBHOOK_SECRET")
JENKINS_BASE_URL = env("JENKINS_BASE_URL")
JENKINS_USER = env("JENKINS_USER")
JENKINS_TOKEN = env("JENKINS_TOKEN")
JENKINS_CALLBACK_HMAC_KEY = env("JENKINS_CALLBACK_HMAC_KEY")  # §3.3 坏签名 401 且不落库
# OQ-4 IM 双实现凭据位（真实值 S0-9 填充）
WECOM_CORP_SECRET = env("WECOM_CORP_SECRET")
DINGTALK_APP_SECRET = env("DINGTALK_APP_SECRET")
K8S_READER = env("K8S_READER")  # OQ-3: api | kubectl

# ---- DRF / API 契约 ----
REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    # S0-4 基座三件套挂载：统一 envelope / 异常处理 / 分页（page_size=20 拍板默认）
    "EXCEPTION_HANDLER": "apps.core.exceptions.app_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    # S0-5（拍板①）：仅 session 认证；BasicAuth 之类调试口子禁开（多一道匿名面=多一处回归红线）
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
}
SPECTACULAR_SETTINGS = {
    "TITLE": "TomatoLite Release Platform API",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
