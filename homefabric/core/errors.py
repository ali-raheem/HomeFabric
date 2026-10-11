class DomainError(Exception):
    """A business rule rejected the operation. Message is safe to show users."""


class ConcurrencyError(DomainError):
    def __init__(self, message: str = ""):
        super().__init__(
            message
            or "This record was changed by someone else after you opened it. "
            "Reload the page to see the latest version, then reapply your change."
        )


class PermissionDenied(DomainError):
    pass


class ArchivedError(DomainError):
    def __init__(self, label: str = "This item"):
        super().__init__(f"{label} is archived and read-only. Restore it before making changes.")
