"""Call an async skill module synchronously from tests: coroutine functions run
under asyncio.run; everything else (exception classes, constants) passes through."""
from __future__ import annotations

import asyncio
import inspect


class Sync:
    def __init__(self, module):
        self._module = module

    def __getattr__(self, name):
        attr = getattr(self._module, name)
        if inspect.iscoroutinefunction(attr):
            return lambda *a, **kw: asyncio.run(attr(*a, **kw))
        return attr
