"""환경변수 설정.

운영값은 전부 환경변수로 주입한다(docker-compose 의 environment 참조).
카메라·솔루션·탐지규칙처럼 '운영 중 바뀌는 값'은 여기가 아니라 DB 에 둔다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── 기본 ────────────────────────────────────────────────────────────
    app_name: str = "AI Vision 통합관제 대시보드"
    log_level: str = "INFO"
    static_dir: Path = Path("static")          # 빌드된 SPA (Dockerfile 이 복사)

    # ── DB ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://aivision:aivision@localhost:5432/aivision"
    db_echo: bool = False

    # ── MQTT ────────────────────────────────────────────────────────────
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_client_id: str = "aivision-server"
    # 서버가 구독할 토픽. 카메라가 MAC 접두 토픽으로 발행하므로 기본은 전체 구독.
    mqtt_subscribe: str = "#"
    # 브라우저('MQTT 로그' 화면)가 직접 붙을 주소. 컨테이너 내부 이름이 아닌 외부에서 보이는 주소.
    mqtt_ws_url: str = "ws://localhost:9001"
    # 수신 원문 보관 일수 (MQTT 로그 화면 및 사후 분석용)
    mqtt_log_retention_days: int = 7

    # ── 미디어 백엔드 ───────────────────────────────────────────────────
    # mediamtx: 카메라에서 한 번만 당겨 여러 소비자에게 나눠 준다(fan-out·녹화·재생).
    # direct  : 미디어 서버 없이 카메라에 직접 붙는다. fan-out 도 녹화도 없다.
    media_backend: str = "mediamtx"
    mediamtx_api_url: str = "http://mediamtx:9997"        # 경로 등록/조회 (서버 → 미디어서버)
    mediamtx_playback_url: str = "http://mediamtx:9996"   # 녹화 구간 목록·추출
    mediamtx_rtsp_host: str = "mediamtx:8554"             # 분석 모듈이 가져가는 곳
    # 브라우저가 WebRTC/HLS 로 붙을 주소. 컨테이너 이름이 아니라 '밖에서 보이는' 주소여야 한다.
    mediamtx_public_url: str = "http://localhost:11884"
    mediamtx_record_dir: str = "/recordings"              # 미디어 서버 컨테이너 안의 경로

    # ── 영상 ────────────────────────────────────────────────────────────
    stream_fps: float = 12.0                   # MJPEG 송출 상한
    jpeg_quality: int = 75
    stream_max_width: int = 1280               # 송출 전 축소(원본 4K 그대로 보내지 않는다)
    rtsp_reconnect_sec: float = 3.0
    rtsp_read_timeout_sec: float = 10.0
    rtsp_ffmpeg_options: str = "rtsp_transport;tcp|buffer_size;1024000|stimeout;5000000"
    # 영상 프레임이 이 시간 이상 안 들어오면 '영상 끊김'으로 본다.
    camera_stale_sec: float = 8.0
    # MQTT heartbeat 가 이 시간 이상 없으면 '장비 무응답'으로 본다.
    # 주의: 영상보다 훨씬 길게 잡아야 한다. heartbeat 주기는 장비·앱마다 다르고(관측: 카메라당
    #   수 초~수십 초), 영상 임계값과 같게 두면 주기가 조금만 길어도 상태가 깜빡인다.
    heartbeat_stale_sec: float = 60.0

    # ── 녹화 ────────────────────────────────────────────────────────────
    # 이벤트 클립을 뽑을 때 앞뒤로 얼마나 붙일지. 사고는 발생 직전 맥락이 중요하다.
    clip_pre_sec: float = 10.0
    clip_post_sec: float = 10.0
    # 클립은 코어 자산이라 우리 저장소에 둔다(미디어 서버의 세그먼트 회전과 무관해야 한다).
    clip_dir: Path = Path("./data/clips")

    # ── 이벤트 ──────────────────────────────────────────────────────────
    snapshot_dir: Path = Path("./data/snapshots")
    # 이벤트 발생 시 해당 카메라의 현재 프레임을 캡쳐해 저장한다.
    snapshot_on_event: bool = True
    # 같은 카메라·같은 솔루션의 연속 신호를 하나의 이벤트로 묶는 창(초).
    # 실장비는 탐지 active 1회 → 약 15초 후 inactive 1회를 발행한다.
    event_dedup_sec: float = 20.0

    # ── 보안 ────────────────────────────────────────────────────────────
    # Fernet 키. 카메라 비밀번호 암호화에 쓴다.
    secret_key: str = Field(default="", repr=False)

    @property
    def clip_path(self) -> Path:
        p = Path(self.clip_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def snapshot_path(self) -> Path:
        p = Path(self.snapshot_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache
def get_settings() -> Settings:
    return Settings()
