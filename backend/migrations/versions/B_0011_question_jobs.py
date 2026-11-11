"""Durable profile QA jobs; audit records retain their separate purpose."""
from alembic import op
from app.models.talent import ProfileQuestionJob

revision = 'B_0011_question_jobs'
down_revision = 'A_0014_account_scope_mode'
branch_labels = None
depends_on = None


def upgrade():
    ProfileQuestionJob.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    ProfileQuestionJob.__table__.drop(op.get_bind(), checkfirst=True)
