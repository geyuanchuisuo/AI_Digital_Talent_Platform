"""Explicit profile organization membership; never infer it from a name."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql
revision='B_0008_profile_department'
down_revision='B_0007_merge_learning_evidence'
branch_labels=None
depends_on=None


def upgrade():
    if 'department_id' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('talent_profiles')}:
        op.add_column('talent_profiles',sa.Column('department_id',mysql.BIGINT(unsigned=True),nullable=True))


def downgrade():
    op.drop_column('talent_profiles','department_id')
