# amica/asgi.py
import os

from ws.routing import application

django_settings = os.environ.get("DJANGO_SETTINGS_MODULE", "amica.settings.dev")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", django_settings)