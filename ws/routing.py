# ws/routing.py
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.core.asgi import get_asgi_application
from django.urls import re_path

django_asgi_app = get_asgi_application()

from .middleware.token_auth_middleware import TokenAuthMiddleware


def get_websocket_urlpatterns():
    from .consumers.app_consumer import AppConsumer

    return [
        re_path(r"^ws/socket-server/$", AppConsumer.as_asgi()),
    ]


application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AllowedHostsOriginValidator(
            TokenAuthMiddleware(URLRouter(get_websocket_urlpatterns()))
        ),
    }
)
