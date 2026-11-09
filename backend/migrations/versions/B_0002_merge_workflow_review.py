"""Merge B profile history and D workflow review without rewriting either branch."""
revision = 'B_0002_merge_workflow_review'
down_revision = ('B_0001_profile_history', 'D_0009_workflow_review')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
