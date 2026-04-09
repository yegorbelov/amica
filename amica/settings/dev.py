from .base import *

DEBUG = True

# Binding + session cookies must not use Secure on http://localhost (Vite proxy, docker).
SESSION_COOKIE_SECURE = False

MIDDLEWARE.insert(0, "silk.middleware.SilkyMiddleware")

import re


class SilkIgnoreProtectedFiles:
    def __contains__(self, path: str) -> bool:
        if re.match(r"^/api/protected-file/\d+/[a-zA-Z0-9_-]+/?", path):
            return True
        if re.match(r"^/api/files/\d+(/|/[a-zA-Z0-9_-]+/?)$", path):
            return True
        if path.startswith("/media/"):
            return True
        return False


SILKY_IGNORE_PATHS = SilkIgnoreProtectedFiles()
