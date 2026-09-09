"""카메라에 보조(저화질) 스트림 경로 추가

추론 모듈이 4K 를 디코딩할 이유가 없다. 모델 입력은 어차피 640 으로 줄여 넣으므로
3840x2160 을 풀어 놓고 다시 줄이는 것은 CPU 를 그냥 버리는 것이다. 카메라가 이미
저화질 프로파일을 함께 내보내므로(한화비전은 profile3 등) 그 경로를 적어 두면 미디어
서버에 보조 경로를 하나 더 만들어 모듈에게 준다.

비워 두면 보조 스트림이 없는 것으로 본다. 기존 카메라는 비워 둔다 — 프로파일 번호는
기종마다 다르므로 짐작해서 채우면 없는 주소를 가리키게 된다.

Revision ID: b9e8d8ae9574
Revises: 3e45fd490072
Create Date: 2026-09-09 15:58:00.000000+09:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = 'b9e8d8ae9574'
down_revision = '3e45fd490072'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        # 기존 행이 있으므로 server_default 를 반드시 준다.
        batch_op.add_column(sa.Column('rtsp_path_sub', sa.String(length=200),
                                      nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('cameras', schema=None) as batch_op:
        batch_op.drop_column('rtsp_path_sub')
