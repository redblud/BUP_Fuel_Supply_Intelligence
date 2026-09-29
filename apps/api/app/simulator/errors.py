class SimulatorError(Exception):
    """Normalized simulator failure. Both simulator error shapes ({"detail": {...}} and
    {"error": {...}}, guide §9) and transport failures end up here."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.code in ("FAULT_INJECTED", "UNREACHABLE", "TIMEOUT") or (self.status or 0) >= 500
