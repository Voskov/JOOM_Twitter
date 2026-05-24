"""Initial schema: users, messages, follows tables with indexes.

Revision ID: 0001
Revises:
Create Date: 2026-05-24 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- users -----------------------------------------------------------
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("username", sa.String(50), unique=True, nullable=False),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    # --- messages --------------------------------------------------------
    op.create_table(
        "messages",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("content", sa.String(140), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "idx_messages_user_created",
        "messages",
        [sa.text("user_id"), sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_messages_created",
        "messages",
        [sa.text("created_at DESC")],
    )

    # --- follows ---------------------------------------------------------
    op.create_table(
        "follows",
        sa.Column(
            "follower_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "followed_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("follower_id", "followed_id"),
    )
    op.create_index("idx_follows_follower", "follows", ["follower_id"])
    op.create_index("idx_follows_followed", "follows", ["followed_id"])


def downgrade() -> None:
    op.drop_table("follows")
    op.drop_table("messages")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
