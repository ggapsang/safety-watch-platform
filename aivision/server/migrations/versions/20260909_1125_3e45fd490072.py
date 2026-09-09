"""카메라에 화면 표시 순서 추가

등록 순서가 곧 현장에서 보고 싶은 순서인 경우는 드물다(게이트 → 야적장 → 출하구 처럼
동선대로 보고 싶어 한다). 브라우저에 두지 않고 DB 에 두는 이유는 관제실 PC 가 여러 대여도
같은 순서로 보여야 하기 때문이다.

기존 카메라는 지금까지 보이던 순서(= id 순)를 그대로 유지한다. 전부 0 으로 두어도
동점 처리로 같은 결과가 나오지만, 값으로 남겨 두면 나중에 동점 처리를 바꿔도 흔들리지 않는다.

Revision ID: 3e45fd490072
Revises: fddd034e9643
Create Date: 2026-09-09 11:25:21.480853+09:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '3e45fd490072'
down_revision = 'fddd034e9643'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        # 기존 행이 있으므로 server_default 를 반드시 준다. 없으면 NOT NULL 추가가 실패한다.
        batch_op.add_column(sa.Column('sort_order', sa.Integer(),
                                      nullable=False, server_default='0'))
        batch_op.create_index(batch_op.f('ix_cameras_sort_order'), ['sort_order'], unique=False)
    op.execute("UPDATE cameras SET sort_order = id")


def downgrade() -> None:
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_cameras_sort_order'))
        batch_op.drop_column('sort_order')
