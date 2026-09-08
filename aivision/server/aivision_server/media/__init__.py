"""미디어 평면.

코어는 `backend` 하나만 본다. 어떤 구현이 붙어 있는지 몰라도 동작해야 한다.
"""

from __future__ import annotations

import logging

from ..config import get_settings
from .backend import BackendHealth, DirectBackend, MediaBackend, Segment, StreamInfo

log = logging.getLogger(__name__)

_backend: MediaBackend | None = None


def backend() -> MediaBackend:
    global _backend
    if _backend is None:
        kind = get_settings().media_backend.lower()
        if kind == "mediamtx":
            from .mediamtx import MediaMTXBackend

            _backend = MediaMTXBackend()
        else:
            _backend = DirectBackend()
        log.info("미디어 백엔드: %s", _backend.name)
    return _backend


async def dispose() -> None:
    global _backend
    if _backend is not None:
        await _backend.close()
    _backend = None


__all__ = ["BackendHealth", "MediaBackend", "Segment", "StreamInfo", "backend", "dispose"]
