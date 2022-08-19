"""remove unnecessary journal cols

Revision ID: b260b2f5d99b
Revises: 2ffbb0bd6229
Create Date: 2022-07-13 20:25:10.692508

"""
from alembic import op
import sqlalchemy as sa
import tradealpha
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'b260b2f5d99b'
down_revision = '2ffbb0bd6229'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('journal', 'chapter_interval',
               existing_type=postgresql.INTERVAL(),
               nullable=True)
    op.drop_column('journal', 'auto_generate')
    op.drop_column('journal', 'track_performance')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('journal', sa.Column('track_performance', sa.BOOLEAN(), autoincrement=False, nullable=True))
    op.add_column('journal', sa.Column('auto_generate', sa.BOOLEAN(), autoincrement=False, nullable=True))
    op.alter_column('journal', 'chapter_interval',
               existing_type=postgresql.INTERVAL(),
               nullable=False)
    # ### end Alembic commands ###