class SklandError(Exception):
    """Base error exposed to the AstrBot adapter."""


class LoginException(SklandError):
    pass


class RequestException(SklandError):
    pass


class UnauthorizedException(SklandError):
    pass


class RosterExportError(SklandError):
    """Stable, credential-free errors for consumers of the raw roster facade."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
