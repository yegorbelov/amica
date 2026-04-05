from .base import *

DEBUG = False

CORS_ALLOWED_ORIGINS = [
    "https://amica.tf",
]

CSRF_TRUSTED_ORIGINS = [
    "https://amica.tf",
]

CSP_IMG_SRC = ("'self'", "data:", "blob:", "https://amica.tf")
CSP_CONNECT_SRC = ("'self'", "wss://amica.tf")

# Keep production lean: remove local/dev-only apps.
INSTALLED_APPS = [
    app
    for app in INSTALLED_APPS
    if app not in {"django_extensions", "sslserver", "silk"}
]