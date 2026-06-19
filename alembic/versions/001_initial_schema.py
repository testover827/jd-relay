"""Initial schema — work_orders, agents, approvals, build_results

Revision ID: 001
Create Date: 2026-06-19
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── work_orders ──────────────────────────────────────────────
    op.create_table(
        'work_orders',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('order_no', sa.String(64), nullable=False),
        sa.Column('issue', sa.String(128), nullable=False),
        sa.Column('project', sa.String(128), nullable=False),
        sa.Column('branch', sa.String(256), nullable=False),
        sa.Column('build_cmd', sa.Text(), nullable=False, server_default=''),
        sa.Column('state', sa.String(32), nullable=False, server_default='DRAFT'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('created_by', sa.String(64), nullable=False, server_default=''),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_no'),
    )
    op.create_index('ix_work_orders_order_no', 'work_orders', ['order_no'])
    op.create_index('ix_work_orders_project', 'work_orders', ['project'])
    op.create_index('ix_work_orders_state', 'work_orders', ['state'])

    # ── agents ───────────────────────────────────────────────────
    op.create_table(
        'agents',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('agent_id', sa.String(64), nullable=False),
        sa.Column('projects', sa.Text(), nullable=False),
        sa.Column('ecdsa_pub_pem', sa.Text(), nullable=False),
        sa.Column('is_online', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('last_seen_at', sa.DateTime(), nullable=True),
        sa.Column('connected_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('agent_id'),
    )
    op.create_index('ix_agents_agent_id', 'agents', ['agent_id'])

    # ── approvals ────────────────────────────────────────────────
    op.create_table(
        'approvals',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('work_order_id', sa.Integer(), nullable=False),
        sa.Column('approver', sa.String(64), nullable=False),
        sa.Column('status', sa.String(16), nullable=False, server_default='PENDING'),
        sa.Column('comment', sa.Text(), nullable=False, server_default=''),
        sa.Column('review_type', sa.String(16), nullable=False, server_default='first'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('responded_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['work_order_id'], ['work_orders.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_approvals_work_order_id', 'approvals', ['work_order_id'])

    # ── build_results ────────────────────────────────────────────
    op.create_table(
        'build_results',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('work_order_id', sa.Integer(), nullable=False),
        sa.Column('build_number', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='PENDING'),
        sa.Column('log_url', sa.Text(), nullable=False, server_default=''),
        sa.Column('duration_seconds', sa.Integer(), nullable=True),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['work_order_id'], ['work_orders.id'], ondelete='CASCADE'),
    )
    op.create_index('ix_build_results_work_order_id', 'build_results', ['work_order_id'])


def downgrade() -> None:
    op.drop_table('build_results')
    op.drop_table('approvals')
    op.drop_table('agents')
    op.drop_table('work_orders')
