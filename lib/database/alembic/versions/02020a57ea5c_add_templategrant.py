"""add templategrant

Revision ID: 02020a57ea5c
Revises: d29c21f66a4f
Create Date: 2022-11-24 20:12:16.457330

"""
import fastapi_users_db_sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.orm import Session

from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '02020a57ea5c'
down_revision = 'd29c21f66a4f'
branch_labels = None
depends_on = None


def upgrade():
    session = Session(bind=op.get_bind())
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('templategrant',
                    sa.Column('template_id', sa.Integer(), nullable=False),
                    sa.Column('grant_id', sa.Integer(), nullable=False),
                    sa.ForeignKeyConstraint(['grant_id'], ['authgrant.id'], name='authgrant_id_fkey',
                                            ondelete='CASCADE'),
                    sa.ForeignKeyConstraint(['template_id'], ['template.id'], name='template_id_fkey',
                                            ondelete='CASCADE'),
                    sa.PrimaryKeyConstraint('template_id', 'grant_id')
                    )
    op.drop_column('event', 'public')
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
    op.add_column('event', sa.Column('public', sa.BOOLEAN(), autoincrement=False, nullable=False))
    op.drop_table('templategrant')
    # ### end Alembic commands ###
