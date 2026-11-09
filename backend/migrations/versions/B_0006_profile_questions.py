"""Add immutable profile QA request and answer snapshots."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = 'B_0006_profile_questions'
down_revision = 'B_0005_merge_c_capacity'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('talent_profile_questions',
        sa.Column('id', mysql.BIGINT(unsigned=True), primary_key=True, autoincrement=True),
        sa.Column('person_id', mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column('profile_version', sa.Integer(), nullable=False),
        sa.Column('asked_by', mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column('status', sa.String(32), nullable=False),
        sa.Column('snapshot_json', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        mysql_engine='InnoDB', mysql_charset='utf8mb4', mysql_collate='utf8mb4_unicode_ci')
    op.create_index('ix_talent_profile_questions_person_id', 'talent_profile_questions', ['person_id'])


def downgrade():
    op.drop_table('talent_profile_questions')
