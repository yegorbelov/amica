import os

from django.core.wsgi import get_wsgi_application

django_settings = os.environ.get("DJANGO_SETTINGS_MODULE", "amica.settings.dev")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", django_settings)

application = get_wsgi_application()
