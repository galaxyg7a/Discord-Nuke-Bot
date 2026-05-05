import random


class RateController:
    """
    Per-operation rate controller used alongside the BypassEngine.
    Controls the base timing profile — the BypassEngine applies jitter on top.
    """

    INTENSITY_DELAYS: dict[int, float] = {
        1: 2.0,
        2: 1.2,
        3: 0.8,
        4: 0.5,
        5: 0.3,
        6: 0.18,
        7: 0.1,
        8: 0.05,
        9: 0.01,
        10: 0.0,
    }

    BURST_SIZE: dict[int, int] = {
        1: 2, 2: 3, 3: 4, 4: 5, 5: 8,
        6: 12, 7: 18, 8: 25, 9: 50, 10: 9999,
    }

    def __init__(self) -> None:
        self.intensity: int = 10
        self._call_count: int = 0

    def set_intensity(self, level: int) -> None:
        if 1 <= level <= 10:
            self.intensity = level

    def get_delay(self) -> float:
        base = self.INTENSITY_DELAYS.get(self.intensity, 0.0)
        if base == 0.0:
            return 0.0
        return max(0.0, base + random.uniform(-0.5, 0.5) * base)

    def get_burst_delay(self) -> float:
        """Zero delay for burst_size calls, then a brief hold."""
        self._call_count += 1
        burst = self.BURST_SIZE.get(self.intensity, 9999)
        if self._call_count % (burst + 1) == 0:
            return self.get_delay() * random.uniform(1.5, 3.0)
        return 0.0

    def describe(self) -> str:
        return (
            f"intensity {self.intensity}/10 | "
            f"delay ~{self.get_delay():.3f}s | "
            f"burst_size {self.BURST_SIZE.get(self.intensity)}"
        )
