"""make template-journal many2many

Revision ID: c5ae51850724
Revises: 24bf1f9fbaed
Create Date: 2022-07-14 19:59:14.263493

"""
from alembic import op
import sqlalchemy as sa
import tradealpha


# revision identifiers, used by Alembic.
revision = 'c5ae51850724'
down_revision = '24bf1f9fbaed'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('journal_template_association',
    sa.Column('journal_id', sa.Integer(), nullable=False),
    sa.Column('template_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['journal_id'], ['journal.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['template_id'], ['template.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('journal_id', 'template_id')
    )
    op.drop_constraint('template_journal_id_fkey', 'template', type_='foreignkey')
    op.drop_column('template', 'journal_id')
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('template', sa.Column('journal_id', sa.INTEGER(), autoincrement=False, nullable=False))
    op.create_foreign_key('template_journal_id_fkey', 'template', 'journal', ['journal_id'], ['id'])
    op.drop_table('journal_template_association')
    # ### end Alembic commands ###
