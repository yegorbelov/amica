import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "amica.settings.dev")

app = Celery("amica")

app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


app.conf.update(
    task_time_limit=300,
    task_soft_time_limit=270,
)
