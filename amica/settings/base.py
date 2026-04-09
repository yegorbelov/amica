import os
from pathlib import Path

os.environ.setdefault("DJANGO_CELERY_BEAT_TZ_AWARE", "False")
os.environ["TZ"] = "UTC"

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(os.path.join(BASE_DIR, ".env"))

SECRET_KEY = env("SECRET_KEY")
GOOGLE_CLIENT_ID = env("GOOGLE_CLIENT_ID")

WEBAUTHN_RP_ID = env("WEBAUTHN_RP_ID")
WEBAUTHN_ORIGIN = env("WEBAUTHN_ORIGIN")
WEBAUTHN_PORT = env("WEBAUTHN_PORT")

SITE_SCHEME = env("SITE_SCHEME")
SITE_DOMAIN = env("SITE_DOMAIN")

FRONTEND_URL = env.str("FRONTEND_URL", default="http://localhost:5173")

# Email: default console (dev). Set EMAIL_HOST + credentials in .env for real SMTP.
EMAIL_BACKEND = env.str(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
DEFAULT_FROM_EMAIL = env.str("DEFAULT_FROM_EMAIL", default="webmaster@localhost")
EMAIL_HOST = env.str("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env.str("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env.str("EMAIL_HOST_PASSWORD", default="")
# Gmail: 587 + TLS (defaults). For port 465 use EMAIL_USE_SSL=True and EMAIL_USE_TLS=False.
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=True)
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=30)

if (
    EMAIL_HOST
    and EMAIL_HOST_USER
    and EMAIL_HOST_PASSWORD
    and EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend"
):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"


STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"


SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
SESSION_COOKIE_AGE = 1209600
SESSION_EXPIRE_AT_BROWSER_CLOSE = False
# False required for http://localhost dev (Safari/Chrome reject Secure cookies on HTTP).
# Override in .env if needed; amica.settings.dev forces False after importing base.
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=True)
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"


INSTALLED_APPS = [
    "daphne",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "django_extensions",
    "channels",
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "sslserver",
    "apps.Site",
    "apps.accounts",
    # "apps.contacts",
    # "apps.chat",
    "apps.media_files",
    "webauthn",
    "django_celery_beat",
    "django_celery_results",
    "silk",
]

SITE_ID = 1

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# CSP_DEFAULT_SRC = ("'self'",)
# CSP_SCRIPT_SRC = ("'self'",)
# CSP_STYLE_SRC = ("'self'", "'unsafe-inline'")
# CSP_IMG_SRC = ("'self'", "data:", "blob:")
# CSP_CONNECT_SRC = ("'self'", "wss://*", "ws://*")
# CSP_OBJECT_SRC = ("'none'",)
# CSP_FRAME_ANCESTORS = ("'none'",)
# CSP_BASE_URI = ("'self'",)
# CSP_FORM_ACTION = ("'self'",)

# CSP_SEND_DEFAULT_SRC = True


SOCIAL_AUTH_URL_NAMESPACE = "social"

INTERNAL_IPS = [
    "0.0.0.0",
]

ROOT_URLCONF = "amica.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR.joinpath("app")],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
]


AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_TZ = True


LOGIN_REDIRECT_URL = "home"

LOGOUT_REDIRECT_URL = "login"


ASGI_APPLICATION = "amica.asgi.application"
WSGI_APPLICATION = "amica.wsgi.application"


REDIS_HOST = "redis"
REDIS_PORT = 6379

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [(REDIS_HOST, REDIS_PORT)],
        },
    },
}
CELERY_BROKER_URL = "redis://redis:6379/0"
CELERY_RESULT_BACKEND = "redis://redis:6379/0"

CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"

CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

from datetime import timedelta

from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    "flush_expired_sessions_daily": {
        "task": "apps.Site.tasks.flush_expired_tokens.flush_expired_tokens_daily",
        "schedule": crontab(hour=3, minute=0),
    },
    "cleanup_blacklisted_tokens_daily": {
        "task": "apps.Site.tasks.cleanup_blacklist.cleanup_expired_blacklisted_tokens",
        "schedule": crontab(hour=3, minute=30),
    },
    "purge_soft_deleted_messages_minutely": {
        "task": "apps.Site.tasks.purge_deleted_messages.purge_soft_deleted_messages",
        "schedule": timedelta(minutes=15),
    },
}

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Large multipart uploads: keep in-memory buffers small; spill to FILE_UPLOAD_TEMP_DIR.
DATA_UPLOAD_MAX_MEMORY_SIZE = 2_621_440  # 2.5 MiB (Django default)
FILE_UPLOAD_MAX_MEMORY_SIZE = 2_621_440  # 2.5 MiB (Django default)
FILE_UPLOAD_TEMP_DIR = str(BASE_DIR / "tmp")
# Avoid RAM spikes during large multipart uploads: write file parts directly to temp files.
FILE_UPLOAD_HANDLERS = ["django.core.files.uploadhandler.TemporaryFileUploadHandler"]

PROTECTED_MEDIA_ROOT = BASE_DIR / "protected_files"

AUTH_USER_MODEL = "accounts.CustomUser"

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True


DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


ALLOWED_HOSTS = ["*"]

SESSION_CACHE_ALIAS = "default"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "devdb",
        "USER": "devuser",
        "PASSWORD": "devpass",
        "HOST": "postgres",
        "PORT": "5432",
    }
}

DEFAULT_FILE_STORAGE = (
    "django_hashedfilenamestorage.storage.HashedFilenameFileSystemStorage"
)

CSRF_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_HTTPONLY = False
CORS_ALLOW_ALL_ORIGINS = True
CORS_ALLOW_CREDENTIALS = True
CORS_PREFLIGHT_MAX_AGE = 86400

try:
    from corsheaders.defaults import default_headers

    CORS_ALLOW_HEADERS = list(default_headers)
except ImportError:
    CORS_ALLOW_HEADERS = [
        "accept",
        "accept-encoding",
        "authorization",
        "content-type",
        "dnt",
        "origin",
        "user-agent",
        "x-csrftoken",
        "x-requested-with",
    ]

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS")
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS")

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.Site.authentication.authentication.BearerJWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
}

from datetime import datetime, timedelta

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=15),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_COOKIE": "access_token",
    "AUTH_COOKIE_REFRESH": "refresh_token",
    "AUTH_COOKIE_HTTP_ONLY": True,
    "AUTH_COOKIE_SAMESITE": "Lax",
}


DATA_UPLOAD_MAX_NUMBER_FIELDS = 2000

GEOIP_PATH = os.path.join(BASE_DIR, "geoip")