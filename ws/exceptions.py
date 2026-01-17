# ws/exceptions.py


class WSValidationError(Exception):
    pass


class WSPermissionError(Exception):
    pass


class WSNotFoundError(Exception):
    pass
