"""parties: a theme and a drink list per party

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-02
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('party',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=100), nullable=False),
    sa.Column('welcome', sa.Text(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('color_page', sa.String(length=7), nullable=False),
    sa.Column('color_frame', sa.String(length=7), nullable=False),
    sa.Column('color_heading', sa.String(length=7), nullable=False),
    sa.Column('color_button', sa.String(length=7), nullable=False),
    sa.Column('color_go', sa.String(length=7), nullable=False),
    sa.Column('logo', sa.String(length=100), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_party'))
    )
    op.create_table('party_drink',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('party_id', sa.Integer(), nullable=False),
    sa.Column('drink_id', sa.Integer(), nullable=False),
    sa.Column('featured', sa.Boolean(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['drink_id'], ['drink.id'], name=op.f('fk_party_drink_drink_id_drink'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['party_id'], ['party.id'], name=op.f('fk_party_drink_party_id_party'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_party_drink')),
    sa.UniqueConstraint('party_id', 'drink_id', name=op.f('uq_party_drink_party_id'))
    )
    with op.batch_alter_table('party_drink', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_party_drink_party_id'), ['party_id'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('party_drink', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_party_drink_party_id'))

    op.drop_table('party_drink')
    op.drop_table('party')
