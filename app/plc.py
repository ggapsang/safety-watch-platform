"""PLC 출력 계층 — XGT 클라이언트를 IF 맵(Rev. 2026.07.03)으로 감싼다.

  PC -> PLC (Write only)
    D8100  PC Ready        0 = Off / 1 = On
    D8101  판정 결과 코드   0 정상(정회전) / 1 부분오염(정지) / 2 많이오염(역회전) / 9 판정실패(알람)

  PLC -> PC 의 D8000/D8001 은 **읽지 않는다.** (PC 는 전송만 한다)

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
        return self._write(self.cfg.addr_pc_ready, value, "PC Ready",
                           "PC Ready On" if value else "PC Ready Off")

    def write_result(self, code: int) -> bool:
        """판정 결과 코드(0/1/2/9)를 D8101 에 쓴다."""
        return self._write(self.cfg.addr_result, code, "결과코드",
                           CODE_LABELS.get(code, f"미정의 코드 {code}"))

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
        self.set_pc_ready(1)

    def shutdown(self) -> None:
        if not self.enabled or self.client is None:
            return
        try:
            self.set_pc_ready(0)
        finally:
            self.client.close()
