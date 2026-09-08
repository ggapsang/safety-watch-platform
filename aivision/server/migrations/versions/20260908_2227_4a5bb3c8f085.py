"""바인딩에 판정 주체 표현식 추가

module_id 에 바인딩 이름을 넣고 있었다. 그러면 '어느 규칙이 걸렸나'(binding_id)와
'무엇이 판정했나'(module_id)가 뒤섞여, 정작 판정 주체가 기록되지 않는다.
페이로드에 모듈 식별자가 실려 오면 뽑을 수 있도록 표현식 칸을 만든다. 비워도 된다 —
카메라 엣지처럼 자기를 밝히지 않는 소스가 많다.

Revision ID: 4a5bb3c8f085
Revises: 045f53242664
Create Date: 2026-09-08 22:27:45.847613+09:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = '4a5bb3c8f085'
down_revision = '045f53242664'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('inbound_bindings', schema=None) as batch_op:
        # 기존 행이 있으므로 server_default 를 반드시 준다. 없으면 NOT NULL 추가가 실패한다.
        batch_op.add_column(sa.Column('module_expr', sa.String(length=200),
                                      nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('inbound_bindings', schema=None) as batch_op:
        batch_op.drop_column('module_expr')
