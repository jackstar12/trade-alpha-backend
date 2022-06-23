"""add symbol column to transfer

Revision ID: ad402e99dea8
Revises: d8c03a699bb7
Create Date: 2022-05-15 21:01:12.451922

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ad402e99dea8'
down_revision = 'd8c03a699bb7'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('transfer', sa.Column('symbol', sa.String(), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('transfer', 'symbol')
    # ### end Alembic commands ###
