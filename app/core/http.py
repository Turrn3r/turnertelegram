from __future__ import annotations
import asyncio
import httpx
from dataclasses import dataclass
from typing import Callable, TypeVar

T = TypeVar("T")


@dataclass
class HttpPolicy:
    timeout_sec: float = 25.0
    retries: int = 2
    backoff_base_sec: float = 0.6


async def with_retries(fn: Callable[[], "asyncio.Future[T]"], policy: HttpPolicy) -> T:
    last_err: Exception | None = None
    for i in range(policy.retries + 1):
        try:
            return await fn()
        except Exception as e:
            last_err = e
            if i >= policy.retries:
                break
            await asyncio.sleep(policy.backoff_base_sec * (2 ** i))
    assert last_err is not None
    raise last_err


def client(user_agent: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(headers={"User-Agent": user_agent})
