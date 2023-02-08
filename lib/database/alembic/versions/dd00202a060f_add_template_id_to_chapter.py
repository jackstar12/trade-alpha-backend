"""add template_id to chapter

Revision ID: dd00202a060f
Revises: 0856da4f1911
Create Date: 2023-02-04 18:07:17.732711

"""
import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'dd00202a060f'
down_revision = '0856da4f1911'
branch_labels = None
depends_on = None


def upgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('chapter', sa.Column('template_id', sa.Integer(), nullable=True))
    op.create_foreign_key(None, 'chapter', 'template', ['template_id'], ['id'], ondelete='SET NULL')
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
    op.drop_constraint(None, 'chapter', type_='foreignkey')
    op.drop_column('chapter', 'template_id')
    # ### end Alembic commands ###
