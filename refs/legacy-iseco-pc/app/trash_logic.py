"""판정 로직 — daim_detector trash 정책 이식.

(2026-06-30 설계 + 2026-07-01 개정)

  프레임 검출 결과
    -> count = 검출 수
    -> area  = Σbbox면적 / (ROI 또는 프레임 면적)      [단위: percent 또는 ratio]
    -> 2초 sliding window 누적 -> max_count, max_area
    -> count/area 각각 L/M/H -> 9매트릭스 -> severity = max(count_level, area_level)
    -> cooldown 판단 -> 발송 시 D8101 write

분류 규칙 (경계 처리 주의 — 07-01 개정본):
  count : < low -> L / >= high -> H / 그 외 M      (상한 경계 포함)
  area  : < low -> L / >  high -> H / 그 외 M      (상한 경계 미포함)

⚠ 윈도우는 프레임 수 고정이 아니라 '최근 N초 시간 기반'이다.
   카메라(5fps, 10프레임=2초)와 PC 추론 fps 가 다르므로 '2초' 의미를 fps 와 무관하게 보존한다.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from enum import IntEnum

LEVEL_NAMES = ("L", "M", "H")


class Level(IntEnum):
    L = 0
    M = 1
    H = 2


class Severity(IntEnum):
    NORMAL = 0        # 정상       -> 정회전
    PARTIAL = 1       # 부분오염   -> 알람 + 라인 정지
    HEAVY = 2         # 많이오염   -> 알람 + 2번 컨베이어 역회전


SEVERITY_LABELS = {
    Severity.NORMAL: "정상",
    Severity.PARTIAL: "부분오염",
    Severity.HEAVY: "많이오염",
}

# IF 맵(Rev. 2026.07.03): 9 = 판정실패(카메라 오류) -> 알람 + 안전 정지/수동 확인
FAULT_CODE = 9
FAULT_LABEL = "판정실패"


def classify_count(count: int, low: int, high: int) -> Level:
    """< low -> L / >= high -> H / 그 외 M."""
    if count < low:
        return Level.L
    if count >= high:
        return Level.H
    return Level.M


def classify_area(area: float, low: float, high: float) -> Level:
    """< low -> L / > high -> H / 그 외 M. (area 는 상한 경계 미포함)"""
    if area < low:
        return Level.L
    if area > high:
        return Level.H
    return Level.M


@dataclass
class Judgement:
    max_count: int
    max_area: float
    count_level: Level
    area_level: Level
    category: str                 # "L_L" .. "H_H"
    severity: Severity
    samples: int                  # 윈도우 내 프레임 수


class SlidingWindow:
    """최근 window_sec 초의 (count, area) 를 유지하고 max 를 산출."""

    def __init__(self, window_sec: float = 2.0) -> None:
        self.window_sec = window_sec
        self._buf: deque[tuple[float, int, float]] = deque()

    def add(self, count: int, area: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        self._buf.append((now, count, area))
        self._evict(now)

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_sec
        while self._buf and self._buf[0][0] < cutoff:
            self._buf.popleft()

    def maxima(self, now: float | None = None) -> tuple[int, float, int]:
        now = time.monotonic() if now is None else now
        self._evict(now)
        if not self._buf:
            return 0, 0.0, 0
        return (
            max(c for _, c, _ in self._buf),
            max(a for _, _, a in self._buf),
            len(self._buf),
        )

    def clear(self) -> None:
        self._buf.clear()


class TrashJudge:
    """윈도우 집계 -> 9매트릭스 분류 -> severity 축약."""

    def __init__(self, window_sec: float = 2.0, count_low: int = 1, count_high: int = 2,
                 area_low: float = 0.3, area_high: float = 3.0) -> None:
        self.window = SlidingWindow(window_sec)
        self.count_low = count_low
        self.count_high = count_high
        self.area_low = area_low
        self.area_high = area_high

    def update(self, count: int, area: float, now: float | None = None) -> Judgement:
        now = time.monotonic() if now is None else now
        self.window.add(count, area, now)
        max_count, max_area, samples = self.window.maxima(now)

        count_level = classify_count(max_count, self.count_low, self.count_high)
        area_level = classify_area(max_area, self.area_low, self.area_high)
        category = f"{LEVEL_NAMES[count_level]}_{LEVEL_NAMES[area_level]}"
        severity = Severity(max(int(count_level), int(area_level)))

        return Judgement(
            max_count=max_count,
            max_area=max_area,
            count_level=count_level,
            area_level=area_level,
            category=category,
            severity=severity,
            samples=samples,
        )


@dataclass
class Decision:
    send: bool
    code: int                     # 실제 D8101 에 쓸 값
    reason: str


class CooldownDecider:
    """발송 여부 판단(엄격 쿨다운 + 부분오염 격상 창).

      - L_L(정상) -> 항상 skip + 쿨다운/대기 초기화
        (write_zero_on_normal=True 면 '정상 복귀 시 1회만' 0 write — 라인 되살리는 0 은 즉시)
      - 많이오염(2): 쿨다운만 통과하면 즉시 발송한다.
      - 부분오염(1): 바로 보내지 않고 escalate_sec(기본 0.5초) 동안 '확인 창'을 연다.
        창이 열려 있는 동안 관측한 최대 severity 를 창 종료 시점에 발송한다 —
        즉 창 안에서 한 번이라도 많이오염(2)이 나오면 2 로 격상해 보낸다.
        (움직이는 물체가 프레임에 덜 들어온 채 1 로 조기 확정되는 것을 방지)
      - 발송 후에는 cooldown 이 지나야 다음을 보낸다. cooldown 중에는 더 높은
        severity 가 와도 흡수한다(엄격 쿨다운).
    """

    def __init__(self, cooldown_sec: float = 8.0, write_zero_on_normal: bool = False,
                 escalate_sec: float = 0.5) -> None:
        self.cooldown_sec = max(1.0, cooldown_sec)
        self.write_zero_on_normal = write_zero_on_normal
        self.escalate_sec = max(0.0, escalate_sec)      # 0 이면 격상 창 없이 즉시 발송
        self._last_sent_at: float | None = None
        self._zero_written = True     # 기동 직후 정상 상태에서 불필요한 0 write 방지
        self._pending_since: float | None = None        # 부분오염 확인 창 시작 시각
        self._pending_max = 0                           # 창 동안 관측한 최대 severity

    def decide(self, j: Judgement, now: float | None = None) -> Decision:
        now = time.monotonic() if now is None else now
        sev = int(j.severity)

        if j.severity == Severity.NORMAL:
            self._last_sent_at = None
            self._pending_since = None
            self._pending_max = 0
            if self.write_zero_on_normal and not self._zero_written:
                self._zero_written = True
                return Decision(True, 0, "정상 복귀 — 0 write")
            return Decision(False, 0, "L_L 정상 — skip")

        self._zero_written = False

        # 부분오염 확인 창 진행 중 — 최대 severity 를 추적하다가 창이 끝나면 확정 발송
        if self._pending_since is not None:
            self._pending_max = max(self._pending_max, sev)
            if now - self._pending_since >= self.escalate_sec:
                code = self._pending_max
                self._pending_since = None
                self._pending_max = 0
                self._last_sent_at = now
                label = SEVERITY_LABELS.get(Severity(code), str(code))
                return Decision(True, code, f"확인 창 종료 — {label}({code}) 발송")
            remain = self.escalate_sec - (now - self._pending_since)
            return Decision(False, sev, f"부분오염 확인 대기 {remain:.1f}s (많이오염이면 격상)")

        cooled = self._last_sent_at is None or (now - self._last_sent_at) >= self.cooldown_sec
        if not cooled:
            remain = self.cooldown_sec - (now - self._last_sent_at)
            return Decision(False, sev, f"cooldown {remain:.1f}s 남음 — skip")

        # 쿨다운 통과 — 많이오염은 즉시, 부분오염은 확인 창을 연다
        if sev >= int(Severity.HEAVY) or self.escalate_sec <= 0:
            self._last_sent_at = now
            return Decision(True, sev, "발송")
        self._pending_since = now
        self._pending_max = sev
        return Decision(False, sev, f"부분오염 감지 — {self.escalate_sec:.1f}s 확인 대기 시작")

    def reset(self, force_next_send: bool = False) -> None:
        """상태 초기화. force_next_send=True 면 다음 판정을 무조건 1회 발송한다
        (판정실패 복구 시 라인을 되살릴 코드를 다시 써야 하므로)."""
        self._last_sent_at = None
        self._zero_written = not force_next_send
        self._pending_since = None
        self._pending_max = 0


class FaultTracker:
    """판정실패(코드 9) 판단.

    영상이 fault_after_sec 이상 끊기거나 추론이 실패하면 **1회** 9 를 전송한다.
    (알람 코드이므로 반복 전송하지 않는다. 복구되면 폴트 해제.)
    """

    def __init__(self, fault_after_sec: float = 5.0, fault_code: int = FAULT_CODE) -> None:
        self.fault_after_sec = fault_after_sec
        self.fault_code = fault_code
        self.in_fault = False
        self._unhealthy_since: float | None = None

    def update(self, healthy: bool, now: float | None = None) -> Decision:
        """반환: send=True 면 fault_code 를 write 해야 한다.
        복구 순간에는 send=False 이고 reason 에 '복구' 가 담긴다(호출자가 decider 를 리셋)."""
        now = time.monotonic() if now is None else now

        if healthy:
            self._unhealthy_since = None
            if self.in_fault:
                self.in_fault = False
                return Decision(False, self.fault_code, "판정실패 복구")
            return Decision(False, 0, "정상 동작")

        if self._unhealthy_since is None:
            self._unhealthy_since = now
        elapsed = now - self._unhealthy_since
        if elapsed >= self.fault_after_sec and not self.in_fault:
            self.in_fault = True
            return Decision(True, self.fault_code, f"영상/추론 {elapsed:.1f}s 이상 실패 — 코드 9 전송")
        if self.in_fault:
            return Decision(False, self.fault_code, "판정실패 지속 — 재전송 안 함")
        return Decision(False, 0, f"영상/추론 실패 {elapsed:.1f}s (임계 {self.fault_after_sec:.0f}s)")

    def reset(self) -> None:
        self.in_fault = False
        self._unhealthy_since = None
