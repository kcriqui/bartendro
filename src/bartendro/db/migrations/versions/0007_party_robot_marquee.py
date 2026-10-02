"""parties: which robot sits behind the menus, and an LED sign's text

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('party', schema=None) as batch_op:
        batch_op.add_column(sa.Column('robot', sa.String(length=20), server_default='', nullable=False))
        batch_op.add_column(sa.Column('marquee', sa.String(length=200), server_default='', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('party', schema=None) as batch_op:
        batch_op.drop_column('marquee')
        batch_op.drop_column('robot')
