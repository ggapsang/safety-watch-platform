"""카메라 비밀번호 암호화.

RTSP 계정 비밀번호를 평문으로 DB 에 두지 않는다. SECRET_KEY(Fernet 키)로 암·복호화하고,
API 응답에는 절대 싣지 않는다(has_password 불리언만 내보낸다).

주의: SECRET_KEY 를 분실하면 저장된 비밀번호를 복구할 수 없다. 재등록해야 한다.
"""

from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

log = logging.getLogger(__name__)


def _fernet() -> Fernet:
    key = (get_settings().secret_key or "").strip()
    if not key:
        raise RuntimeError(
            "SECRET_KEY 가 비어 있습니다. .env 에 설정하세요.\n"
            '  python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        # 사람이 아무 문자열이나 넣어도 동작하도록 파생 키를 만든다(권장 방식은 아니지만
        # 현장에서 키 생성 절차를 놓쳐 서버가 아예 안 뜨는 쪽이 더 나쁘다).
        digest = hashlib.sha256(key.encode()).digest()
        return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plain: str) -> str:
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt(token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        log.error("카메라 비밀번호 복호화 실패 — SECRET_KEY 가 바뀌었을 수 있습니다")
        return ""
