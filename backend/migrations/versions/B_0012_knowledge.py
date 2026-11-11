"""Versioned institutional knowledge and durable ingestion/question tasks."""
from alembic import op
from app.modules.knowledge.models import KNOWLEDGE_MODELS

revision = 'B_0012_knowledge'
down_revision = 'B_0011_question_jobs'
branch_labels = None
depends_on = None


def upgrade():
    for model in KNOWLEDGE_MODELS:
        model.__table__.create(op.get_bind(), checkfirst=True)


def downgrade():
    for model in reversed(KNOWLEDGE_MODELS):
        model.__table__.drop(op.get_bind(), checkfirst=True)
