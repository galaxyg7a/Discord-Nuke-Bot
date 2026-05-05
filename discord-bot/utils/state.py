import asyncio
from typing import Optional
from utils.rate_control import RateController
from utils.bypass import BypassEngine


class BotState:
    """Global mutable state shared across all cogs."""

    def __init__(self) -> None:
        self.running_tasks: set[asyncio.Task] = set()
        self.stop_event: asyncio.Event = asyncio.Event()
        self.rate_controller: RateController = RateController()
        self.bypass: BypassEngine = BypassEngine()
        self.active_simulation: Optional[str] = None

    def is_running(self) -> bool:
        return bool(self.running_tasks)

    def stop_all(self) -> None:
        self.stop_event.set()
        for task in list(self.running_tasks):
            task.cancel()
        self.running_tasks.clear()
        self.active_simulation = None

    def reset(self) -> None:
        self.stop_event.clear()
        self.bypass.reset()

    def add_task(self, task: asyncio.Task) -> None:
        self.running_tasks.add(task)
        task.add_done_callback(self.running_tasks.discard)


# Module-level singleton — imported by all cogs
bot_state = BotState()
