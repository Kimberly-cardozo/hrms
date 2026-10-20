"""add ai audit logs

Revision ID: 0017_add_ai_audit_logs
Revises: 0016_employee_documents_uploaded_by
Create Date: 2026-10-20
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_add_ai_audit_logs"
down_revision: Union[str, None] = "0016_employee_documents_uploaded_by"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(length=64), nullable=False),
        sa.Column("tool_or_api_used", sa.String(length=128), nullable=True),
        sa.Column("action_status", sa.String(length=32), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["employees.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ai_audit_logs_id"), "ai_audit_logs", ["id"], unique=False)
    op.create_index(op.f("ix_ai_audit_logs_user_id"), "ai_audit_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_ai_audit_logs_role"), "ai_audit_logs", ["role"], unique=False)
    op.create_index(op.f("ix_ai_audit_logs_intent"), "ai_audit_logs", ["intent"], unique=False)
    op.create_index(op.f("ix_ai_audit_logs_tool_or_api_used"), "ai_audit_logs", ["tool_or_api_used"], unique=False)
    op.create_index(op.f("ix_ai_audit_logs_action_status"), "ai_audit_logs", ["action_status"], unique=False)
    op.create_index(op.f("ix_ai_audit_logs_timestamp"), "ai_audit_logs", ["timestamp"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_ai_audit_logs_timestamp"), table_name="ai_audit_logs")
    op.drop_index(op.f("ix_ai_audit_logs_action_status"), table_name="ai_audit_logs")
    op.drop_index(op.f("ix_ai_audit_logs_tool_or_api_used"), table_name="ai_audit_logs")
    op.drop_index(op.f("ix_ai_audit_logs_intent"), table_name="ai_audit_logs")
    op.drop_index(op.f("ix_ai_audit_logs_role"), table_name="ai_audit_logs")
    op.drop_index(op.f("ix_ai_audit_logs_user_id"), table_name="ai_audit_logs")
    op.drop_index(op.f("ix_ai_audit_logs_id"), table_name="ai_audit_logs")
    op.drop_table("ai_audit_logs")
