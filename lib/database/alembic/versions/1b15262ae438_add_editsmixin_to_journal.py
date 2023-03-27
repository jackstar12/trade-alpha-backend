"""add editsmixin to journal

Revision ID: 1b15262ae438
Revises: 02c46315e2b9
Create Date: 2023-03-05 21:59:05.191913

"""
import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '1b15262ae438'
down_revision = '02c46315e2b9'
branch_labels = None
depends_on = None


def upgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('journal', sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True))
    op.add_column('journal', sa.Column('last_edited', sa.DateTime(timezone=True), nullable=True))
    # ### end Alembic commands ###


def downgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('journal', 'last_edited')
    op.drop_column('journal', 'created_at')
    # ### end Alembic commands ###
