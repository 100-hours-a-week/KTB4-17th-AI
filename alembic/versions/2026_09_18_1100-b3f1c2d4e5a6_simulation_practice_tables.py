"""simulation · practice tables

Revision ID: b3f1c2d4e5a6
Revises: 7e6ab27fe22a
Create Date: 2026-09-18 11:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3f1c2d4e5a6"
down_revision: str | Sequence[str] | None = "7e6ab27fe22a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "simulations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("persona_a_id", sa.String(length=32), nullable=False),
        sa.Column("persona_b_id", sa.String(length=32), nullable=False),
        sa.Column("user_id_a", sa.String(length=64), nullable=True),
        sa.Column("user_id_b", sa.String(length=64), nullable=True),
        sa.Column("nickname_a", sa.String(length=20), nullable=False),
        sa.Column("nickname_b", sa.String(length=20), nullable=False),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("transcript", sa.JSON(), nullable=False),
        sa.Column("report", sa.JSON(), nullable=False),
        sa.Column("narrative_source", sa.String(length=8), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["persona_a_id"], ["personas.id"]),
        sa.ForeignKeyConstraint(["persona_b_id"], ["personas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_simulations_persona_a_id"), "simulations", ["persona_a_id"], unique=False)
    op.create_index(op.f("ix_simulations_persona_b_id"), "simulations", ["persona_b_id"], unique=False)
    op.create_index(op.f("ix_simulations_user_id_a"), "simulations", ["user_id_a"], unique=False)
    op.create_index(op.f("ix_simulations_user_id_b"), "simulations", ["user_id_b"], unique=False)

    op.create_table(
        "practice_sessions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("partner_persona_id", sa.String(length=32), nullable=False),
        sa.Column("partner_user_id", sa.String(length=64), nullable=True),
        sa.Column("partner_nickname", sa.String(length=20), nullable=False),
        sa.Column("my_persona_id", sa.String(length=32), nullable=True),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("my_nickname", sa.String(length=20), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["partner_persona_id"], ["personas.id"]),
        sa.ForeignKeyConstraint(["my_persona_id"], ["personas.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_practice_sessions_partner_persona_id"), "practice_sessions", ["partner_persona_id"], unique=False
    )
    op.create_index(
        op.f("ix_practice_sessions_partner_user_id"), "practice_sessions", ["partner_user_id"], unique=False
    )
    op.create_index(op.f("ix_practice_sessions_my_persona_id"), "practice_sessions", ["my_persona_id"], unique=False)
    op.create_index(op.f("ix_practice_sessions_user_id"), "practice_sessions", ["user_id"], unique=False)

    op.create_table(
        "practice_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.String(length=32), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=8), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["practice_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_practice_messages_session_id"), "practice_messages", ["session_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_practice_messages_session_id"), table_name="practice_messages")
    op.drop_table("practice_messages")
    op.drop_index(op.f("ix_practice_sessions_user_id"), table_name="practice_sessions")
    op.drop_index(op.f("ix_practice_sessions_my_persona_id"), table_name="practice_sessions")
    op.drop_index(op.f("ix_practice_sessions_partner_user_id"), table_name="practice_sessions")
    op.drop_index(op.f("ix_practice_sessions_partner_persona_id"), table_name="practice_sessions")
    op.drop_table("practice_sessions")
    op.drop_index(op.f("ix_simulations_user_id_b"), table_name="simulations")
    op.drop_index(op.f("ix_simulations_user_id_a"), table_name="simulations")
    op.drop_index(op.f("ix_simulations_persona_b_id"), table_name="simulations")
    op.drop_index(op.f("ix_simulations_persona_a_id"), table_name="simulations")
    op.drop_table("simulations")
