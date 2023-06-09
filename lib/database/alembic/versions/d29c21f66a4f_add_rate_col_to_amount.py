"""add rate col to amount

Revision ID: d29c21f66a4f
Revises: c1cf25c23103
Create Date: 2022-11-20 15:54:42.097204

"""
import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'd29c21f66a4f'
down_revision = 'c1cf25c23103'
branch_labels = None
depends_on = None


def upgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('amount', sa.Column('rate', sa.Numeric(), nullable=True))
    op.drop_constraint('balance_transfer_id_fkey', 'balance', type_='foreignkey')
    op.drop_column('balance', 'transfer_id')
    op.alter_column('journal', 'chapter_interval',
               existing_type=postgresql.INTERVAL(),
               type_=sa.Enum('DAY', 'WEEK', 'MONTH', name='intervaltype'),
               existing_nullable=True)
    # ### end Alembic commands ###


def downgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('journal', 'chapter_interval',
               existing_type=sa.Enum('DAY', 'WEEK', 'MONTH', name='intervaltype'),
               type_=postgresql.INTERVAL(),
               existing_nullable=True)
    op.add_column('balance', sa.Column('transfer_id', sa.INTEGER(), autoincrement=False, nullable=True))
    op.create_foreign_key('balance_transfer_id_fkey', 'balance', 'transfer', ['transfer_id'], ['id'], ondelete='SET NULL')
    op.drop_column('amount', 'rate')
    # ### end Alembic commands ###
