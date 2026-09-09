"""로컬 개발용 서버 실행기.

운영은 Docker(리눅스)로 띄운다. 이 스크립트는 Windows 개발 PC 에서 서버만 따로 돌릴 때 쓴다.

  python dev.py                       # SQLite + localhost 브로커
  python dev.py --port 8001

주의: Windows 기본 이벤트 루프(Proactor)는 add_reader/remove_writer 를 지원하지 않아
  aiomqtt(paho) 가 소켓을 등록할 때 NotImplementedError 로 죽는다.
  그래서 루프를 만들기 전에 Selector 정책으로 바꿔 준다. 리눅스에서는 해당 없음.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys


def main() -> int:
    p = argparse.ArgumentParser(description="AI Vision 서버 (개발용)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--reload", action="store_true", help="코드 변경 시 자동 재시작")
    p.add_argument("--sqlite", default="sqlite+aiosqlite:///./dev.db",
                   help="개발용 DB. PostgreSQL 을 쓰려면 DATABASE_URL 을 직접 설정하세요.")
    args = p.parse_args()

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    os.environ.setdefault("DATABASE_URL", args.sqlite)
    os.environ.setdefault("SNAPSHOT_DIR", "./data/snapshots")
    os.environ.setdefault("TZ", "Asia/Seoul")
    # 도커 밖에서 도는 개발 서버는 호스트에 매핑된 브로커 포트로 붙는다.
    # (컨테이너 안의 서버가 쓰는 base-broker:1883 과 다르다.)
    os.environ.setdefault("MQTT_HOST", "localhost")
    os.environ.setdefault("MQTT_PORT", "1883")
    os.environ.setdefault("MQTT_WS_URL", "ws://localhost:11881")
    if not os.environ.get("SECRET_KEY"):
        # 개발용 임시 키. 재시작하면 바뀌므로 저장된 카메라 비밀번호는 복호화되지 않는다.
        from cryptography.fernet import Fernet

        os.environ["SECRET_KEY"] = Fernet.generate_key().decode()
        print("주의: SECRET_KEY 미설정 — 임시 키를 생성했습니다(재시작 시 카메라 비밀번호 재입력 필요).")

    import uvicorn

    uvicorn.run("aivision_server.main:app", host=args.host, port=args.port,
                reload=args.reload, loop="asyncio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
