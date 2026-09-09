"""환경변수 설정.

운영값은 전부 환경변수로 주입한다(docker-compose 의 environment 참조).
카메라·솔루션·탐지규칙처럼 '운영 중 바뀌는 값'은 여기가 아니라 DB 에 둔다.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ── 기본 ────────────────────────────────────────────────────────────
    app_name: str = "AI Vision 통합관제 대시보드"
    log_level: str = "INFO"
    static_dir: Path = Path("static")          # 빌드된 SPA (Dockerfile 이 복사)
    # 커밋할 수 있는 설정(탐지 항목·바인딩·운영 설정)이 사는 파일. 이것이 원본이고
    # DB 는 사본이다 — DB 는 이벤트가 쌓여 GB 로 커지므로 저장소에 올릴 수 없다.
    config_file: Path = Path("../deploy/config/platform.json")

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
    # 같은 메시지(토픽+페이로드가 동일)를 이 간격 안에서는 한 번만 처리한다. 0 이면 끔.
    #
    # 중복 억제(event_dedup_sec)와는 층이 다르다. 그것은 '같은 상황을 두 번 기록하지
    # 않는다'(의미 수준, DB 로 판단)이고, 이것은 '같은 메시지를 두 번 처리하지 않는다'
    # (전송 수준, 메모리로 판단)이다. 초당 수백 개가 쏟아질 때 바인딩·DB 를 타기 전에
    # 문 앞에서 막는 것이 목적이다 — 특히 빈 페이로드를 연발하는 카메라가 있다.
    inbound_min_interval_sec: float = 1.0
    # 수신 원문 보관 일수 (사후 분석용)
    mqtt_log_retention_days: int = 7
    # 수신 원문을 DB 에 남기는 정책. 기본은 남기지 않는다.
    #
    # 남기지 않아도 '모르는 토픽 찾기'가 막히지 않는다 — 'MQTT 로그' 화면은 브로커에
    # 직결 구독하므로 실시간 발견은 DB 와 무관하다. DB 사본은 '어제 그 시간에 무엇이
    # 왔었나'를 되짚을 때만 쓰인다. 그래서 기본을 끔으로 둔다. 라이브 박스처럼 초당
    # 여러 번 들어오는 트래픽이 하루 수십만 줄을 쌓는 것을 기본 동작으로 둘 수는 없다.
    #
    #   off        아무것도 남기지 않는다 (기본)
    #   unmatched  바인딩에 안 걸린 것만 남긴다
    #   all        전부 남긴다 (짧게 켰다 끄는 용도)
    mqtt_log_mode: str = "off"
    # 특정 채널만 남길 때 쓰는 토픽 패턴(쉼표 구분, MQTT 와일드카드).
    # 채우면 mqtt_log_mode 보다 우선한다 — '이것만 남긴다'는 뜻이다.
    mqtt_log_topics: str = ""

    # ── 미디어 백엔드 ───────────────────────────────────────────────────
    # mediamtx: 카메라에서 한 번만 당겨 여러 소비자에게 나눠 준다(fan-out·녹화·재생).
    # direct  : 미디어 서버 없이 카메라에 직접 붙는다. fan-out 도 녹화도 없다.
    media_backend: str = "mediamtx"
    # 아래 기본값의 base-media 는 compose 서비스 이름(컨테이너 DNS)이다.
    # 위 media_backend 의 "mediamtx" 는 백엔드 종류를 고르는 값이라 서로 무관하다 —
    # 서비스 이름을 바꿔도 종류 이름은 그대로 둔다.
    mediamtx_api_url: str = "http://base-media:9997"      # 경로 등록/조회 (서버 → 미디어서버)
    mediamtx_playback_url: str = "http://base-media:9996"  # 녹화 구간 목록·추출
    mediamtx_rtsp_host: str = "base-media:8554"           # 분석 모듈이 가져가는 곳
    # 브라우저가 WebRTC/HLS 로 붙을 주소. 컨테이너 이름이 아니라 '밖에서 보이는' 주소여야 한다.
    mediamtx_public_url: str = "http://localhost:11884"
    # 미디어 서버 컨테이너 안에서 세그먼트를 쓰는 경로(미디어 서버에게 알려 주는 값).
    # 아래 record_dir 은 같은 볼륨을 코어 컨테이너에서 보는 경로다. 값이 같아도 주체가 다르다.
    mediamtx_record_dir: str = "/recordings"

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
    # 상시 녹화 세그먼트가 쌓이는 곳. 쓰는 것은 미디어 서버지만 코어도 같은 폴더를 본다 —
    # '용량이 얼마나 찼나'와 '넘치면 무엇부터 버리나'는 정책이고, 미디어 서버는 시간 기반
    # 회전(recordDeleteAfter)까지만 할 수 있기 때문이다.
    record_dir: Path = Path("./data/recordings")
    # 녹화가 실제로 쌓이는 곳의 '호스트 쪽' 이름. 화면에 보여 주기만 한다 —
    # 컨테이너 안에서는 늘 record_dir 이고, 어디에 붙일지는 배포가 정한다(compose 의
    # RECORD_PATH). 이 값으로 서버가 하는 일은 없다.
    record_location: str = ""
    # 상시 녹화 전체 용량 상한(GB). 0 이면 제한하지 않는다.
    #
    # 카메라별이 아니라 전체 하나로 두는 이유: 디스크가 하나라서다. 카메라마다 상한을 주면
    # 합이 디스크를 넘을 수 있어 정작 막고 싶었던 사고를 못 막는다.
    # 화면에서 바꿀 수 있게 runtime 설정으로도 덮어쓸 수 있다.
    record_max_gb: float = 0.0
    # 용량 정리를 얼마나 자주 볼지(초).
    record_quota_interval_sec: float = 300.0

    # ── 이벤트 ──────────────────────────────────────────────────────────
    snapshot_dir: Path = Path("./data/snapshots")
    # 이벤트 발생 시 해당 카메라의 현재 프레임을 캡쳐해 저장한다.
    snapshot_on_event: bool = True
    # 같은 카메라·같은 솔루션의 연속 신호를 하나의 이벤트로 묶는 창(초).
    # 실장비는 탐지 active 1회 → 약 15초 후 inactive 1회를 발행한다.
    event_dedup_sec: float = 20.0


    @property
    def clip_path(self) -> Path:
        p = Path(self.clip_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def record_path(self) -> Path:
        p = Path(self.record_dir)
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
