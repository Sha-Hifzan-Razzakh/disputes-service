class DomainError(Exception):
    """A business rule was violated."""


class InvalidTransition(DomainError):
    def __init__(self, current: str, action: str) -> None:
        super().__init__(f"cannot {action} a dispute in status {current}")
        self.current = current
        self.action = action


class NotFound(DomainError):
    def __init__(self, kind: str, key: str) -> None:
        super().__init__(f"{kind} {key} not found")
        self.kind = kind
        self.key = key


class VersionConflict(DomainError):
    def __init__(self, expected: int, actual: int) -> None:
        super().__init__(f"version conflict: expected {expected}, current {actual}")
