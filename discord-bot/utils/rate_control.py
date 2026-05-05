import random
import time


class RateController:
    """
    Advanced rate controller with jitter, burst waves, and bypass modes.

    Bypass strategies:
      - Jitter:      randomise each delay ±50% to defeat pattern-based detectors
      - Burst wave:  fire N requests at 0-delay then hold briefly — mimics organic spikes
      - Chaos mode:  randomise between burst and trickle unpredictably
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
        9: 0.02,
        10: 0.0,
    }

    BURST_SIZE: dict[int, int] = {
        1: 2, 2: 3, 3: 4, 4: 5, 5: 8,
        6: 12, 7: 18, 8: 25, 9: 40, 10: 999,
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
        jitter = random.uniform(-0.5, 0.5) * base
        return max(0.0, base + jitter)

    def get_burst_delay(self) -> float:
        """Returns 0 delay for burst_size calls, then a brief hold."""
        self._call_count += 1
        burst = self.BURST_SIZE.get(self.intensity, 999)
        if self._call_count % (burst + 1) == 0:
            return self.get_delay() * random.uniform(1.5, 3.0)
        return 0.0

    def get_chaos_delay(self) -> float:
        """Randomly alternate between burst and normal to confuse detectors."""
        if random.random() < 0.15:
            return random.uniform(0.3, 1.2)
        return self.get_burst_delay()

    def describe(self) -> str:
        return f"intensity {self.intensity}/10 | delay ~{self.get_delay():.3f}s | burst_size {self.BURST_SIZE.get(self.intensity)}"
