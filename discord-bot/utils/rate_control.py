class RateController:
    """Controls the intensity/rate of raid simulation actions."""

    INTENSITY_DELAYS: dict[int, float] = {
        1: 2.0,
        2: 1.5,
        3: 1.0,
        4: 0.75,
        5: 0.5,
        6: 0.35,
        7: 0.25,
        8: 0.15,
        9: 0.1,
        10: 0.05,
    }

    def __init__(self) -> None:
        self.intensity: int = 5

    def set_intensity(self, level: int) -> None:
        if 1 <= level <= 10:
            self.intensity = level

    def get_delay(self) -> float:
        return self.INTENSITY_DELAYS.get(self.intensity, 0.5)

    def describe(self) -> str:
        return f"intensity {self.intensity}/10 ({self.get_delay()}s delay)"
