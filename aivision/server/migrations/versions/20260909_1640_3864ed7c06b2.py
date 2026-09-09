"""카메라 비밀번호를 평문으로

암호화를 걷어낸다. 키(SECRET_KEY)를 DB 옆(.env)에 두는 구조라 보호가 되지 않았다 —
DB 에 닿는 사람은 키에도 닿는다. 그러면서 '키를 잃으면 비밀번호를 못 읽는다' 는 함정만
남았다. 폐쇄망 전제이므로 가장하지 않고 평문으로 둔다.

컬럼을 새로 만들고 지우는 대신 **이름을 바꾸고 값을 푼다.** 새로 만들면 현장에서 넣어 둔
비밀번호가 통째로 날아간다.

푸는 데는 SECRET_KEY 가 필요하다. 이 마이그레이션이 도는 시점에는 아직 환경에 남아 있다
(compose 가 넘겨 준다). 없거나 풀리지 않으면 **빈 값으로 둔다.** 암호문을 비밀번호랍시고
남겨 두면 RTSP 인증이 알 수 없는 이유로 실패하는데, 빈 값이면 화면에 '비밀번호 없음' 으로
바로 드러나 다시 넣으면 된다.

Revision ID: 3864ed7c06b2
Revises: b9e8d8ae9574
Create Date: 2026-09-09 16:40:00.000000+09:00
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os

import sqlalchemy as sa
from alembic import op

revision = '3864ed7c06b2'
down_revision = 'b9e8d8ae9574'
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")

# Fernet 토큰은 이 접두로 시작한다. 이미 평문인 값을 건드리지 않기 위한 표시다.
_TOKEN_PREFIX = "gAAAAA"


def _fernet():
    """예전 키로 Fernet 을 만든다. 만들 수 없으면 None."""
    key = (os.environ.get("SECRET_KEY") or "").strip()
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        return None
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        # 임의 문자열도 키로 받아 주던 경로(해시로 32바이트를 만들어 썼다).
        digest = hashlib.sha256(key.encode()).digest()
        try:
            return Fernet(base64.urlsafe_b64encode(digest))
        except (ValueError, TypeError):
            return None


def upgrade() -> None:
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.alter_column('password_enc', new_column_name='password',
                              existing_type=sa.Text(), existing_nullable=False)

    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, password FROM cameras WHERE password <> ''")).fetchall()
    tokens = [(r[0], r[1]) for r in rows if str(r[1]).startswith(_TOKEN_PREFIX)]
    if not tokens:
        return

    fernet = _fernet()
    failed: list[int] = []
    for cam_id, token in tokens:
        plain = ""
        if fernet is not None:
            try:
                plain = fernet.decrypt(str(token).encode()).decode()
            except Exception:                                # noqa: BLE001
                plain = ""
        if not plain:
            failed.append(cam_id)
        conn.execute(sa.text("UPDATE cameras SET password = :p WHERE id = :i"),
                     {"p": plain, "i": cam_id})

    log.info("카메라 비밀번호 평문 전환: %d건 중 %d건 복호화",
             len(tokens), len(tokens) - len(failed))
    if failed:
        log.warning("복호화하지 못해 비워 둔 카메라: %s — 관리자 화면에서 다시 넣으세요",
                    ", ".join(f"#{i}" for i in failed))


def downgrade() -> None:
    # 되돌리기는 이름만 되돌린다. 평문을 다시 암호화하려면 키가 있어야 하는데,
    # 그 키를 없애려고 한 변경이라 여기서 되살릴 수 없다.
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.alter_column('password', new_column_name='password_enc',
                              existing_type=sa.Text(), existing_nullable=False)
