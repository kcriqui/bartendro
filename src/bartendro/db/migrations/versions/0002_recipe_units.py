"""recipe units and drink instructions

Recipe items keep the amount and unit as written (2 oz); drinks get instructions, glass and
source.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-01
"""
import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('drink', schema=None) as batch_op:
        batch_op.add_column(sa.Column('instructions', sa.Text(), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('glass', sa.String(length=50), nullable=False, server_default=''))
        batch_op.add_column(sa.Column('source', sa.String(length=50), nullable=False, server_default=''))

    with op.batch_alter_table('recipe_item', schema=None) as batch_op:
        batch_op.add_column(sa.Column('amount', sa.Float(), nullable=True))
        batch_op.add_column(sa.Column('unit', sa.String(length=20), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('recipe_item', schema=None) as batch_op:
        batch_op.drop_column('unit')
        batch_op.drop_column('amount')

    with op.batch_alter_table('drink', schema=None) as batch_op:
        batch_op.drop_column('source')
        batch_op.drop_column('glass')
        batch_op.drop_column('instructions')
