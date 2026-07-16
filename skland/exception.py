class SklandError(Exception):
    """Base error exposed to the AstrBot adapter."""


class LoginException(SklandError):
    pass


class RequestException(SklandError):
    pass


class UnauthorizedException(SklandError):
    pass
