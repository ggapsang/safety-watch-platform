"""녹화 보존 단위를 일에서 시간으로

일 단위로는 "6시간만 두고 싶다" 같은 요구를 표현할 수 없다. 미디어 서버의
recordDeleteAfter 는 원래 시간 단위를 받으므로, 코어가 일로 받아 24를 곱하던 것을
그만두고 그대로 시간으로 둔다.

컬럼을 새로 만들고 지우는 대신 **이름을 바꾸고 값을 24배** 한다. 새로 만들면 현장에서
정해 둔 보존기간이 기본값으로 되돌아간다.

Revision ID: fddd034e9643
Revises: 4a5bb3c8f085
Create Date: 2026-09-09 10:21:00.000000+09:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'fddd034e9643'
down_revision = '4a5bb3c8f085'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.alter_column('record_retention_days',
                              new_column_name='record_retention_hours',
                              existing_type=sa.Integer(), existing_nullable=False)
    op.execute("UPDATE cameras SET record_retention_hours = record_retention_hours * 24")


def downgrade() -> None:
    op.execute("UPDATE cameras SET record_retention_hours = "
               "CASE WHEN record_retention_hours < 24 THEN 1 "
               "ELSE record_retention_hours / 24 END")
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.alter_column('record_retention_hours',
                              new_column_name='record_retention_days',
                              existing_type=sa.Integer(), existing_nullable=False)
