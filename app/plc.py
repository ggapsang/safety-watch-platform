"""PLC 입출력 계층 — XGT 클라이언트를 IF 맵(Rev. 2026.07.03)으로 감싼다.

  PC -> PLC (Write)
    D8100  PC Ready        (현장 요청으로 사용하지 않는다 — 아무것도 쓰지 않음)
    D8101  판정 결과 코드   0 정상(정회전) / 1 부분오염(정지) / 2 많이오염(역회전) / 9 판정실패(알람)

  PLC -> PC (Read)
    D8001  Conveyor RUN    0 = Off / 1 = On
      → RUN(1) 일 때만 판정·PLC 전송을 수행하고, 정지면 판정 결과를 폐기한다.
    (D8000 PLC Ready 는 아직 사용하지 않는다.)

⚠ 추론 루프(단일 스레드)에서만 호출한다. 대시보드는 SharedState 만 읽는다.
"""

from __future__ import annotations

import logging
import time

from .xgt_client import XgtClient, XgtError

log = logging.getLogger(__name__)

CODE_LABELS = {
    0: "정상 → 정회전",
    1: "부분오염 → 알람+라인정지",
    2: "많이오염 → 알람+역회전",
    9: "판정실패 → 알람",
}

CONVEYOR_RUN_ON = 1        # IF 맵: D8001 = 0000 Off / 0001 On

# 이 코드들은 '이벤트 펄스'로 보낸다 — 전송 후 result_pulse_sec 뒤 D8101 을 0 으로 되돌린다.
# 0(정회전/idle)은 애초에 0 이라 대상 아님. 9(알람)는 PLC 가 래치하도록 유지한다.
PULSE_CODES = frozenset({1, 2})


class PlcController:
    def __init__(self, cfg) -> None:
        self.cfg = cfg.plc
        self.enabled = self.cfg.enabled
        self.client = XgtClient(self.cfg.host, self.cfg.port, self.cfg.cpu_info,
                                self.cfg.timeout_sec) if self.enabled else None
        self.last_error = ""
        self.last_write = "-"
        self.write_ok = 0
        self.write_fail = 0
        # 읽기(D8001) 상태 — write 상태(last_error/last_write)와 분리해서 관리한다.
        self.last_read_error = ""
        self.last_conveyor_raw: int | None = None
        # 결과 코드 펄스(1/2 전송 후 0 복귀) — 예약된 리셋 시각(monotonic)
        self.pulse_sec = float(getattr(self.cfg, "result_pulse_sec", 0.5))
        self._reset_due: float | None = None

    @property
    def connected(self) -> bool:
        return bool(self.client and self.client.connected)

    # ------------------------------------------------------------------ write

    def _write(self, addr: str, value: int, tag: str, meaning: str) -> bool:
        if not self.enabled or self.client is None:
            self.last_write = f"{tag}={value} (PLC 비활성)"
            return True
        try:
            self.client.write_word(addr, value)
        except XgtError as exc:
            self.write_fail += 1
            self.last_error = str(exc)
            log.error("PLC write 실패 %s <- %d: %s", addr, value, exc)
            return False
        self.write_ok += 1
        self.last_error = ""
        self.last_write = f"{tag}({addr})={value} @{time.strftime('%H:%M:%S')}"
        log.info("PLC write %s <- %d  [%s]", addr, value, meaning)
        return True

    def set_pc_ready(self, value: int) -> bool:
        """D8100(PC Ready)은 현장 요청으로 사용하지 않는다 — 아무것도 쓰지 않는다."""
        return True

    def write_result(self, code: int) -> bool:
        """판정 결과 코드(0/1/2/9)를 D8101 에 쓴다."""
        return self._write(self.cfg.addr_result, code, "결과코드",
                           CODE_LABELS.get(code, f"미정의 코드 {code}"))

    # ---------------------------------------------------------- 결과 코드 펄스

    def arm_result_reset(self, now: float | None = None) -> None:
        """펄스 대상 코드(1/2) 전송 후, pulse_sec 뒤 D8101 을 0 으로 되돌리도록 예약한다."""
        if self.pulse_sec > 0:
            self._reset_due = (time.monotonic() if now is None else now) + self.pulse_sec

    def cancel_result_reset(self) -> None:
        """예약된 리셋을 취소한다(0·9 를 직접 쓸 때 — 이미 0 이거나 래치 유지)."""
        self._reset_due = None

    def service_result_reset(self, now: float | None = None) -> bool:
        """예약 시각이 지났으면 D8101 <- 0 을 써서 펄스를 종료한다. 추론 루프가 매 반복 호출."""
        if self._reset_due is None:
            return False
        now = time.monotonic() if now is None else now
        if now >= self._reset_due:
            self._reset_due = None
            self._write(self.cfg.addr_result, 0, "결과코드", "펄스 종료 → 0(정회전/idle)")
            return True
        return False

    # ------------------------------------------------------------------- read

    def read_conveyor_run(self) -> int | None:
        """D8001(Conveyor RUN) 워드를 읽어 원본 값을 반환한다.

        반환: 워드 값(정상) / None(PLC 비활성·읽기 실패). RUN 여부는 `값 == CONVEYOR_RUN_ON`.
        읽기도 XgtClient._transact 가 필요 시 재접속하므로, 끊긴 연결의 자연 복구 지점이 된다.
        """
        if not self.enabled or self.client is None:
            self.last_read_error = "PLC 비활성"
            return None
        try:
            val = int(self.client.read_word(self.cfg.addr_conveyor_run))
        except XgtError as exc:
            self.last_read_error = str(exc)
            log.warning("PLC read 실패 %s: %s", self.cfg.addr_conveyor_run, exc)
            return None
        self.last_read_error = ""
        self.last_conveyor_raw = val
        return val

    # ------------------------------------------------------------------ 수명주기

    def startup(self) -> None:
        if not self.enabled:
            log.warning("PLC 비활성(PLC_ENABLED=false) — 결과 코드는 로그로만 출력됩니다")
            return
        try:
            self.client.connect()  # type: ignore[union-attr]
        except XgtError as exc:
            self.last_error = str(exc)
            log.error("PLC 초기 연결 실패(추론은 계속, write 시 재시도): %s", exc)
        # D8100(PC Ready)은 사용하지 않는다 — 시작 시 아무것도 쓰지 않는다.

    def reconfigure(self, cfg) -> None:
        """대시보드에서 PLC IP/포트가 바뀌었을 때 연결을 새로 맺는다."""
        if self.client is not None:
            self.client.close()
        self.cfg = cfg.plc
        self.enabled = self.cfg.enabled
        self.client = XgtClient(self.cfg.host, self.cfg.port, self.cfg.cpu_info,
                                self.cfg.timeout_sec) if self.enabled else None
        self.last_error = ""
        self.pulse_sec = float(getattr(self.cfg, "result_pulse_sec", 0.5))
        self._reset_due = None
        log.info("PLC 재설정: %s:%d (enabled=%s)", self.cfg.host, self.cfg.port, self.enabled)
        if self.enabled:
            try:
                self.client.connect()  # type: ignore[union-attr]
            except XgtError as exc:
                self.last_error = str(exc)
                log.error("PLC 재연결 실패(write 시 재시도): %s", exc)
        # D8100(PC Ready)은 사용하지 않는다 — 재설정 시에도 쓰지 않는다.

    def shutdown(self) -> None:
        if not self.enabled or self.client is None:
            return
        # D8100(PC Ready)은 사용하지 않는다 — 종료 시 0 도 쓰지 않고 소켓만 닫는다.
        self.client.close()
