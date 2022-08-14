"""make chapter table more flexible

Revision ID: e3b3306173d5
Revises: 760c7f61c259
Create Date: 2022-07-15 14:54:21.030735

"""
from alembic import op
import sqlalchemy as sa
import tradealpha


# revision identifiers, used by Alembic.
revision = 'e3b3306173d5'
down_revision = '760c7f61c259'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('chapter', 'end_date')
    op.drop_column('chapter', 'start_date')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('chapter', sa.Column('start_date', sa.DATE(), autoincrement=False, nullable=False))
    op.add_column('chapter', sa.Column('end_date', sa.DATE(), autoincrement=False, nullable=False))
    # ### end Alembic commands ###
