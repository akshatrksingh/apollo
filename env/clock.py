from datetime import datetime, timedelta


class FrozenClock:
    def __init__(self, base: str = "2026-06-09T10:00:00", tick_seconds: int = 60) -> None:
        self._base = base
        self._tick_seconds = tick_seconds
        self.tick: int = 0

    def next(self) -> int:
        self.tick += 1
        return self.tick

    def current(self) -> int:
        return self.tick

    def to_datetime(self, tick: int) -> str:
        dt = datetime.fromisoformat(self._base) + timedelta(seconds=tick * self._tick_seconds)
        return dt.isoformat()
