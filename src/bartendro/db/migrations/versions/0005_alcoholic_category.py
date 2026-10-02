"""booze vs mixer, drink categories, pumpable bitters

ingredient.alcoholic replaces kind "alcohol" (the strength button now goes by it; old
"unknown"-type bottles with an ABV count too). Bitters, absinthe, cream and half and half may
now go on a pump, so they're no longer "never pumped". drink.category: menu section override.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

PUMPABLE_NOW = ("Angostura Bitters", "Bitters, Angostura", "Peychaud's Bitters", "Absinthe",
                "Cream", "Half and Half")


def upgrade() -> None:
    with op.batch_alter_table('ingredient', schema=None) as batch_op:
        batch_op.add_column(sa.Column('alcoholic', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE ingredient SET alcoholic = 1 WHERE abv > 0 OR kind = 'alcohol'")
    op.execute("UPDATE ingredient SET kind = 'other' WHERE kind = 'alcohol'")
    names = ", ".join("'" + n.replace("'", "''") + "'" for n in PUMPABLE_NOW)
    op.execute(f"UPDATE ingredient SET manual = 0 WHERE name IN ({names})")

    with op.batch_alter_table('drink', schema=None) as batch_op:
        batch_op.add_column(sa.Column('category', sa.String(length=50), nullable=False, server_default=''))


def downgrade() -> None:
    with op.batch_alter_table('drink', schema=None) as batch_op:
        batch_op.drop_column('category')
    op.execute("UPDATE ingredient SET kind = 'alcohol' WHERE alcoholic = 1 AND kind = 'other'")
    with op.batch_alter_table('ingredient', schema=None) as batch_op:
        batch_op.drop_column('alcoholic')
