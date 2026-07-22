"""LS ELECTRIC XGT 전용 프로토콜(TCP 2004) 클라이언트.

daim_detector 의 `xgt_client.cc` 검증본을 포팅.

프레임 = Company Header(20 byte) + Body
  Company Header
    [0:10]  Company ID  = b"LSIS-XGT\x00\x00"
    [10:12] PLC Info    = 0x0000 (LE)
    [12]    CPU Info    = 0x00 (CPU_ANY, XGB 계열 권장)
    [13]    Source      = 0x33 (client -> server)
    [14:16] Invoke ID   = LE, 매 호출 증가
    [16:18] Body Length = LE
    [18]    Position    = 0x00
    [19]    BCC         = sum(header[0:19]) & 0xFF
  Body (Read/Write)
    [0:2]   Command     = Read 0x5400 / Write 0x5800  (응답 0x5500 / 0x5900)
    [2:4]   Data Type   = Word 0x0200
    [4:6]   Reserved    = 0x0000 (LE)
    [6:8]   Block count = LE
    Write: (이름길이 LE + 이름) 블록들 → (값길이 LE + 값 LE) 블록들
    Read:  (이름길이 LE + 이름) 블록들

⚠ thread-safe 아님. 단일 스레드(추론 루프)에서만 호출하거나 외부에서 lock 을 잡을 것.
"""

from __future__ import annotations

import errno
import logging
import socket
import struct
import time

log = logging.getLogger(__name__)

COMPANY_ID = b"LSIS-XGT\x00\x00"
SOURCE_CLIENT = 0x33
CPU_ANY = 0x00

CMD_READ = 0x5400
CMD_WRITE = 0x5800
CMD_READ_RESP = 0x5500
CMD_WRITE_RESP = 0x5900

DATA_TYPE_WORD = 0x0200

HEADER_LEN = 20
_BODY_LEN_OFFSET = 16


class XgtError(Exception):
    """XGT 통신/프로토콜 오류."""


def build_header(invoke_id: int, body_len: int, cpu_info: int = CPU_ANY) -> bytes:
    header = bytearray()
    header += COMPANY_ID                                  # [0:10]
    header += struct.pack("<H", 0x0000)                   # [10:12] PLC Info
    header += bytes([cpu_info & 0xFF])                    # [12]    CPU Info
    header += bytes([SOURCE_CLIENT])                      # [13]    Source
    header += struct.pack("<H", invoke_id & 0xFFFF)       # [14:16] Invoke ID
    header += struct.pack("<H", body_len & 0xFFFF)        # [16:18] Body Length
    header += bytes([0x00])                               # [18]    Position
    header += bytes([sum(header[:19]) & 0xFF])            # [19]    BCC
    return bytes(header)


def build_write_body(names: list[str], values: list[int]) -> bytes:
    if len(names) != len(values):
        raise ValueError("names 와 values 의 개수가 다릅니다")
    body = bytearray()
    body += struct.pack(">H", CMD_WRITE)
    body += struct.pack(">H", DATA_TYPE_WORD)
    body += struct.pack("<H", 0x0000)                     # Reserved
    body += struct.pack("<H", len(names))                 # Block count
    for name in names:                                    # 변수 이름 블록들
        raw = name.encode("ascii")
        body += struct.pack("<H", len(raw))
        body += raw
    for value in values:                                  # 값 블록들 (Word = 2byte LE)
        body += struct.pack("<H", 2)
        body += struct.pack("<H", value & 0xFFFF)
    return bytes(body)


def build_read_body(names: list[str]) -> bytes:
    body = bytearray()
    body += struct.pack(">H", CMD_READ)
    body += struct.pack(">H", DATA_TYPE_WORD)
    body += struct.pack("<H", 0x0000)                     # Reserved
    body += struct.pack("<H", len(names))                 # Block count
    for name in names:
        raw = name.encode("ascii")
        body += struct.pack("<H", len(raw))
        body += raw
    return bytes(body)


class XgtClient:
    def __init__(self, host: str, port: int = 2004, cpu_info: int = CPU_ANY,
                 timeout_sec: float = 2.0) -> None:
        self.host = host
        self.port = port
        self.cpu_info = cpu_info
        self.timeout_sec = timeout_sec
        self._sock: socket.socket | None = None
        self._invoke_id = 0
        self._refused_backoff_ms = 20            # ECONNREFUSED backoff 20 -> 200ms

    # ------------------------------------------------------------------ 연결

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self) -> None:
        if self._sock is not None:
            return
        try:
            sock = socket.create_connection((self.host, self.port), timeout=self.timeout_sec)
        except OSError as exc:
            if getattr(exc, "errno", None) == errno.ECONNREFUSED:
                time.sleep(self._refused_backoff_ms / 1000.0)
                self._refused_backoff_ms = min(200, self._refused_backoff_ms * 2)
            raise XgtError(f"PLC 연결 실패 {self.host}:{self.port} — {exc}") from exc
        sock.settimeout(self.timeout_sec)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = sock
        self._refused_backoff_ms = 20
        log.info("XGT 연결됨 %s:%d", self.host, self.port)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    # ---------------------------------------------------------------- 저수준 IO

    def _next_invoke_id(self) -> int:
        self._invoke_id = (self._invoke_id + 1) & 0xFFFF
        return self._invoke_id

    def _recv_exact(self, n: int) -> bytes:
        assert self._sock is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise XgtError("PLC 연결이 상대편에서 종료됨")
            buf += chunk
        return bytes(buf)

    def _transact(self, body: bytes, expect_cmd: int) -> bytes:
        """요청 1건 송신 후 응답 body 반환. 실패 시 XgtError."""
        self.connect()
        assert self._sock is not None
        frame = build_header(self._next_invoke_id(), len(body), self.cpu_info) + body
        try:
            self._sock.sendall(frame)
            resp_header = self._recv_exact(HEADER_LEN)
            (resp_body_len,) = struct.unpack_from("<H", resp_header, _BODY_LEN_OFFSET)
            if resp_body_len == 0 or resp_body_len > 4096:
                raise XgtError(f"응답 body length 이상: {resp_body_len}")
            resp_body = self._recv_exact(resp_body_len)
        except (OSError, XgtError) as exc:
            raise XgtError(f"XGT IO 실패: {exc}") from exc

        if len(resp_body) < 8:
            raise XgtError(f"응답 body 가 너무 짧음: {len(resp_body)} byte")
        (cmd,) = struct.unpack_from(">H", resp_body, 0)
        (err_state,) = struct.unpack_from("<H", resp_body, 6)
        if cmd != expect_cmd:
            raise XgtError(f"예상치 못한 응답 command: 0x{cmd:04X} (기대 0x{expect_cmd:04X})")
        if err_state != 0:
            raise XgtError(f"PLC 오류 응답 err_state=0x{err_state:04X}")
        return resp_body

    def _with_reconnect(self, fn):
        """IO 실패 시 close 후 1회 재시도 (daim_detector WithReconnect 동일)."""
        try:
            return fn()
        except XgtError as first:
            log.warning("XGT 실패, 재연결 후 1회 재시도: %s", first)
            self.close()
            try:
                return fn()
            except XgtError as second:
                self.close()
                raise second

    # ------------------------------------------------------------------- 공개 API

    def write_word(self, name: str, value: int) -> None:
        self.write_words([name], [value])

    def write_words(self, names: list[str], values: list[int]) -> None:
        body = build_write_body(names, values)
        self._with_reconnect(lambda: self._transact(body, CMD_WRITE_RESP))

    def read_word(self, name: str) -> int:
        return self.read_words([name])[0]

    def read_words(self, names: list[str]) -> list[int]:
        body = build_read_body(names)
        resp = self._with_reconnect(lambda: self._transact(body, CMD_READ_RESP))
        (block_count,) = struct.unpack_from("<H", resp, 8)
        if block_count != len(names):
            raise XgtError(f"응답 block count 불일치: {block_count} != {len(names)}")
        out: list[int] = []
        offset = 10
        for _ in range(block_count):
            (size,) = struct.unpack_from("<H", resp, offset)
            offset += 2
            if size < 2 or offset + size > len(resp):
                raise XgtError(f"응답 데이터 크기 이상: size={size}")
            (value,) = struct.unpack_from("<H", resp, offset)
            offset += size
            out.append(value)
        return out
