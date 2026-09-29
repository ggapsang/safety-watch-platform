"""프로세스 메모리 — 재고, 비우고, 밖에 보여 준다.

**왜 따로 두는가.** 이 서버는 스레드가 수백 개다(카메라마다 수신 워커 + 디코더 스레드).
glibc 는 스레드마다 malloc 아레나를 붙이고, 스레드가 사라져도 아레나는 남는다. 그 안에서
해제된 조각은 곧장 OS 로 돌아가지 않아 RSS 가 실제 사용량보다 높게 머문다. 재연결이나
고화질 전환처럼 디코더를 새로 여는 일이 반복되면 그 높이가 계단처럼 오른다 — 실제로
31시간에 519MB → 1.5GB 가 됐고, 그중 1,234MB 가 아레나였다.

막는 것은 세 겹이다.
  1. MALLOC_ARENA_MAX=2 (compose)   아레나가 늘어날 자리 자체를 없앤다. 근본 처방.
  2. 디코더 스레드 상한 (config)     재연결 한 번에 생기는 스레드 수를 줄인다.
  3. 주기적 malloc_trim (여기)       해제된 조각을 OS 로 돌려준다. 안전판.

그리고 **숫자를 남긴다.** 이 모든 것이 듣는지 모르면 다음에도 '느려졌다' 는 말로 시작하게
된다. `snapshot()` 을 /api/system 이 내보내고, 감시 모듈(ops-watchdog)이 그것을 기록한다.
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
import time

log = logging.getLogger(__name__)

_libc = None
_last_trim: dict = {}


def _load_libc():
    global _libc
    if _libc is None:
        try:
            _libc = ctypes.CDLL("libc.so.6")
        except OSError:
            _libc = False                     # glibc 가 아니다(개발용 Windows·macOS)
    return _libc or None


def rss_mb() -> float:
    """이 프로세스의 실제 상주 메모리(MB). 리눅스가 아니면 0."""
    try:
        with open("/proc/self/status", encoding="ascii") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def decoder_threads() -> int:
    """FFmpeg 디코더 스레드 수. 늘기만 하면 디코더가 새고 있다는 뜻이다."""
    try:
        n = 0
        for tid in os.listdir("/proc/self/task"):
            try:
                with open(f"/proc/self/task/{tid}/comm", encoding="ascii",
                          errors="replace") as f:
                    if f.read().startswith("av:"):
                        n += 1
            except OSError:
                continue
        return n
    except OSError:
        return 0


def trim() -> dict:
    """해제된 메모리를 OS 에 돌려준다. 결과(전·후 RSS, 걸린 시간)를 돌려준다.

    malloc_trim 은 모든 아레나를 훑는다. 그동안 할당하려는 스레드가 잠깐 기다리므로
    이벤트 루프에서 부르지 않는다 — 호출하는 쪽이 스레드로 넘긴다(monitor 참조).
    """
    libc = _load_libc()
    if libc is None:
        return {}
    before = rss_mb()
    t0 = time.perf_counter()
    libc.malloc_trim(0)
    took = (time.perf_counter() - t0) * 1000
    after = rss_mb()
    result = {"before_mb": round(before, 1), "after_mb": round(after, 1),
              "freed_mb": round(before - after, 1), "took_ms": round(took, 1),
              "at": time.time()}
    _last_trim.clear()
    _last_trim.update(result)
    if before - after >= 20:
        log.info("메모리 반환: %.0f → %.0f MB (%.0f MB 회수, %.0f ms)",
                 before, after, before - after, took)
    return result


def snapshot() -> dict:
    """감시 모듈이 기록할 숫자들."""
    return {
        "rss_mb": round(rss_mb(), 1),
        "threads": threading.active_count(),
        "os_threads": _os_threads(),
        "decoder_threads": decoder_threads(),
        "malloc_arena_max": os.environ.get("MALLOC_ARENA_MAX", ""),
        "last_trim": dict(_last_trim),
    }


def _os_threads() -> int:
    try:
        return len(os.listdir("/proc/self/task"))
    except OSError:
        return 0
