"""by-hand ingredients on hand, before/after steps

ingredient.on_hand: what the guest can add by hand right now (bitters, mint, half and half).
Manual ingredients were always treated as available before, so they start out on hand.
recipe_item.step: a by-hand line is added "before" or "after" the pour.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01
"""
from alembic import op
import sqlalchemy as sa


revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('ingredient', schema=None) as batch_op:
        batch_op.add_column(sa.Column('on_hand', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE ingredient SET on_hand = manual")

    with op.batch_alter_table('recipe_item', schema=None) as batch_op:
        batch_op.add_column(sa.Column('step', sa.String(length=10), nullable=False, server_default='after'))


def downgrade() -> None:
    with op.batch_alter_table('recipe_item', schema=None) as batch_op:
        batch_op.drop_column('step')
    with op.batch_alter_table('ingredient', schema=None) as batch_op:
        batch_op.drop_column('on_hand')
