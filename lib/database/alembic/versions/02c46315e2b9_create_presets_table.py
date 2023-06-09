"""create presets table

Revision ID: 02c46315e2b9
Revises: 3450fa178ecf
Create Date: 2023-02-15 20:53:44.555120

"""
import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '02c46315e2b9'
down_revision = 'dd00202a060f'
branch_labels = None
depends_on = None


def upgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('preset',
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_edited', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', fastapi_users_db_sqlalchemy.generics.GUID(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('type', sa.String(), nullable=False),
    sa.Column('attrs', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
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
    op.drop_table('preset')
    # ### end Alembic commands ###
