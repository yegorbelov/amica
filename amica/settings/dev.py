from .base import *

DEBUG = True

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

PROTECTED_MEDIA_ROOT = BASE_DIR / "protected_files"

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