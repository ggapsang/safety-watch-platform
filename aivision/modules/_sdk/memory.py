"""모듈 프로세스의 메모리 — 재고, 비운다.

코어의 services/memory.py 와 같은 일을 모듈 쪽에서 한다(모듈은 코어를 import 하지 않는다 —
계약만 공유한다). 이유도 같다: 디코더 스레드가 수백 개인 프로세스에서 glibc 아레나가
최고점을 붙든 채 남아 RSS 가 실제 사용량보다 높게 머문다. 객체감지가 4.9GB 까지 올랐고
그중 3,632MB 가 아레나였다.

근본 처방은 compose 의 MALLOC_ARENA_MAX=2 다. 여기 있는 것은 안전판이다.
"""

from __future__ import annotations

import ctypes
import logging
import os
import time

log = logging.getLogger(__name__)

_libc = None


def _load():
    global _libc
    if _libc is None:
        try:
            _libc = ctypes.CDLL("libc.so.6")
        except OSError:
            _libc = False
    return _libc or None


def rss_mb() -> float:
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def trim() -> float:
    """해제된 메모리를 OS 에 돌려주고, 돌려받은 양(MB)을 돌려준다."""
    libc = _load()
    if libc is None:
        return 0.0
    before = rss_mb()
    t0 = time.perf_counter()
    libc.malloc_trim(0)
    freed = before - rss_mb()
    if freed >= 20:
        log.info("메모리 반환: %.0f MB 회수 (%.0f ms)", freed,
                 (time.perf_counter() - t0) * 1000)
    return freed


def snapshot() -> dict:
    try:
        threads = len(os.listdir("/proc/self/task"))
    except OSError:
        threads = 0
    return {"rss_mb": round(rss_mb(), 1), "os_threads": threads,
            "malloc_arena_max": os.environ.get("MALLOC_ARENA_MAX", "")}
