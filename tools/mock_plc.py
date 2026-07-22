"""모의 XGT PLC 서버 — 실제 PLC 없이 앱의 PLC 경로를 검증한다 (M2 사전 리허설용).

    python tools/mock_plc.py                 # 127.0.0.1:2004 에서 대기
    python tools/mock_plc.py --port 20004 --plc-ready 1 --conveyor-run 1

    # 다른 터미널에서
    set PLC_HOST=127.0.0.1 & set PLC_PORT=20004
    python main.py --source sample.mp4

프레임 헤더(Company ID / source / BCC / body length)와 body 레이아웃을 실제로 검증한 뒤
규격대로 응답하므로, 앱이 보내는 바이트가 틀리면 여기서 바로 드러난다.
"""

from __future__ import annotations

import argparse
import socket
import struct
import threading

MEM: dict[str, int] = {"%DW8000": 1, "%DW8001": 1, "%DW8100": 0, "%DW8101": 0}

CMD_READ, CMD_WRITE = 0x5400, 0x5800
CMD_READ_RESP, CMD_WRITE_RESP = 0x5500, 0x5900


def build_resp_header(invoke_id: int, body_len: int) -> bytes:
    h = bytearray()
    h += b"LSIS-XGT\x00\x00"
    h += struct.pack("<H", 0)
    h += bytes([0x00])          # CPU info
    h += bytes([0x11])          # server -> client
    h += struct.pack("<H", invoke_id)
    h += struct.pack("<H", body_len)
    h += bytes([0x00])
    h += bytes([sum(h[:19]) & 0xFF])
    return bytes(h)


def _recv_exact(conn: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = conn.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def handle(conn: socket.socket, addr) -> None:
    print(f"[mock-plc] 접속 {addr}")
    try:
        while True:
            header = _recv_exact(conn, 20)
            if header[0:10] != b"LSIS-XGT\x00\x00":
                raise AssertionError("Company ID 불일치")
            if header[13] != 0x33:
                raise AssertionError(f"source 바이트 불일치 0x{header[13]:02X}")
            if header[19] != sum(header[:19]) & 0xFF:
                raise AssertionError("BCC 불일치")
            invoke_id = struct.unpack_from("<H", header, 14)[0]
            body = _recv_exact(conn, struct.unpack_from("<H", header, 16)[0])

            cmd = struct.unpack_from(">H", body, 0)[0]
            if struct.unpack_from(">H", body, 2)[0] != 0x0200:
                raise AssertionError("Word 데이터 타입이 아님")
            blocks = struct.unpack_from("<H", body, 6)[0]

            off, names = 8, []
            for _ in range(blocks):
                ln = struct.unpack_from("<H", body, off)[0]
                off += 2
                names.append(body[off:off + ln].decode("ascii"))
                off += ln

            if cmd == CMD_WRITE:
                for name in names:
                    size = struct.unpack_from("<H", body, off)[0]
                    off += 2
                    MEM[name] = struct.unpack_from("<H", body, off)[0]
                    off += size
                    print(f"[mock-plc] WRITE {name} <- {MEM[name]}")
                resp = (struct.pack(">H", CMD_WRITE_RESP) + struct.pack(">H", 0x0200)
                        + struct.pack("<H", 0) + struct.pack("<H", 0) + struct.pack("<H", blocks))
            elif cmd == CMD_READ:
                vals = [MEM.get(n, 0) for n in names]
                print(f"[mock-plc] READ  {names} -> {vals}")
                resp = (struct.pack(">H", CMD_READ_RESP) + struct.pack(">H", 0x0200)
                        + struct.pack("<H", 0) + struct.pack("<H", 0) + struct.pack("<H", blocks))
                for v in vals:
                    resp += struct.pack("<H", 2) + struct.pack("<H", v)
            else:
                raise AssertionError(f"알 수 없는 command 0x{cmd:04X}")

            conn.sendall(build_resp_header(invoke_id, len(resp)) + resp)
    except (ConnectionError, OSError):
        pass
    except AssertionError as exc:
        print(f"[mock-plc] ✗ 프로토콜 오류: {exc}")
    finally:
        conn.close()
        print(f"[mock-plc] 연결 종료 {addr}")


def main() -> int:
    p = argparse.ArgumentParser(description="모의 XGT PLC 서버")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=2004)
    p.add_argument("--plc-ready", type=int, default=1, help="D8000 초기값")
    p.add_argument("--conveyor-run", type=int, default=1, help="D8001 초기값")
    args = p.parse_args()

    MEM["%DW8000"] = args.plc_ready
    MEM["%DW8001"] = args.conveyor_run

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(5)
    print(f"[mock-plc] 대기 중 {args.host}:{args.port}  (초기 D8000={args.plc_ready}, "
          f"D8001={args.conveyor_run})")
    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        print("\n[mock-plc] 종료")
    finally:
        srv.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
