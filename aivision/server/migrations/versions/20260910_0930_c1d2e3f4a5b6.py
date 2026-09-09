"""외래키를 전부 걷어낸다

개발 원칙이다 — 스키마에 FOREIGN KEY 제약을 두지 않는다. 참조 컬럼은 인덱스 있는 일반
컬럼으로 두고, 부모를 지울 때 자식을 어떻게 할지는 **애플리케이션이 명시적으로** 정한다
(`services/cleanup.py`).

컬럼은 그대로 두고 **제약만 떨어뜨린다.** 값은 하나도 건드리지 않으므로 되돌릴 것이 없다.

제약 이름을 코드에 박지 않는다. Alembic 의 naming convention 없이 만들어진 테이블이라
PostgreSQL 이 붙인 이름(`events_camera_id_fkey` 같은)에 의존하게 되는데, 손으로 만든
DB 나 다른 백엔드에서는 이름이 다르다. 인스펙터로 **실제 있는 것을 찾아서** 지운다.

SQLite 는 테이블을 다시 만들지 않으면 제약을 뗄 수 없다. 다만 SQLite 는 기본적으로 외래키를
강제하지 않아(`PRAGMA foreign_keys=OFF` 가 기본) 남아 있어도 동작에 차이가 없다. 개발용
경로라 여기서는 건너뛴다 — 새로 만드는 DB 는 어차피 FK 없이 만들어진다.

recordings.camera_id 를 널 허용으로 바꾼다. 카메라를 지워도 녹화 기록은 남기고 참조만
끊기 때문이다. 파일이 디스크에 실제로 있고, 사고 클립은 카메라 등록 정보보다 오래
보관해야 한다.

Revision ID: c1d2e3f4a5b6
Revises: 3864ed7c06b2
Create Date: 2026-09-10 09:30:00.000000+09:00
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = 'c1d2e3f4a5b6'
down_revision = '3864ed7c06b2'
branch_labels = None
depends_on = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    conn = op.get_bind()

    if conn.dialect.name != "sqlite":
        inspector = sa.inspect(conn)
        dropped = 0
        for table in inspector.get_table_names():
            for fk in inspector.get_foreign_keys(table):
                name = fk.get("name")
                if not name:
                    continue
                op.drop_constraint(name, table, type_="foreignkey")
                dropped += 1
        log.info("외래키 제약 %d개 제거", dropped)

    # 카메라를 지워도 녹화 기록은 남는다 — 참조만 끊으므로 널을 허용해야 한다.
    with op.batch_alter_table('recordings', schema=None) as batch_op:
        batch_op.alter_column('camera_id', existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    # 되돌리지 않는다. 외래키를 다시 걸려면 지금 데이터가 전부 무결한지 보장해야 하는데,
    # 제약 없이 운영한 뒤에는 그것을 알 수 없다. 되돌릴 일이 생기면 무엇을 다시 걸지
    # 그때 정해서 새 마이그레이션으로 쓴다.
    with op.batch_alter_table('recordings', schema=None) as batch_op:
        batch_op.alter_column('camera_id', existing_type=sa.Integer(), nullable=False)
