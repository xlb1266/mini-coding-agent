"""阶段十七：聊天输入框档位与模型切换——会话级覆盖三列。

聊天页输入框可直接切换执行档位（审批档位 × 自主档位的预设组合）与
模型。作用范围为会话级：覆盖值写在 chat_sessions 上，只影响当前会话，
新会话仍沿用 Agent 配置的默认值；三列均可空，空表示跟随 Agent。

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-08

"""
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.add_column(sa.Column("approval_level", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("autonomy_mode", sa.String(length=16), nullable=True))
        batch.add_column(sa.Column("model_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_chat_sessions_model_id", "model_configs", ["model_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("chat_sessions") as batch:
        batch.drop_constraint("fk_chat_sessions_model_id", type_="foreignkey")
        batch.drop_column("model_id")
        batch.drop_column("autonomy_mode")
        batch.drop_column("approval_level")
