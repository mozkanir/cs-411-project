"""Errors raised by the IAM service layer.

The message of every IAMError is safe to show to the end user: it never
reveals secrets or whether a username exists.
"""


class IAMError(Exception):
    pass


class ValidationError(IAMError):
    pass


class UserAlreadyExists(IAMError):
    pass


class NotFound(IAMError):
    pass


class PermissionDenied(IAMError):
    pass


class InvalidCredentials(IAMError):
    def __init__(self, message: str = "Invalid username or password.") -> None:
        super().__init__(message)


class AccountLocked(IAMError):
    def __init__(self, message: str = "Account is temporarily locked. Try again later.") -> None:
        super().__init__(message)
