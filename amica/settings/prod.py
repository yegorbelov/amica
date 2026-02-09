from .base import *

DEBUG = False

CORS_ALLOWED_ORIGINS = [
    "https://amica.nekotyan2d.ru",
]

CSRF_TRUSTED_ORIGINS = [
    "https://amica.nekotyan2d.ru",
]

CSP_IMG_SRC = ("'self'", "data:", "blob:", "https://amica.nekotyan2d.ru")
CSP_CONNECT_SRC = ("'self'", "wss://amica.nekotyan2d.ru")