class KtbError(Exception):
    """A user-facing error handled by the CLI."""

    exit_code = 1


class UserAbort(KtbError):
    """An intentional cancellation by the user."""

    exit_code = 0
