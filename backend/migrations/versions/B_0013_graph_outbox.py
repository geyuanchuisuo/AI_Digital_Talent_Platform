"""B09 graph synchronization intents and monotonic source tombstones."""
from alembic import op
from app.modules.talents.graph_models import GRAPH_MODELS
revision = 'B_0013_graph_outbox'
down_revision = 'B_0012_knowledge'
branch_labels = None
depends_on = None


def upgrade():
    for model in GRAPH_MODELS:
        model.__table__.create(op.get_bind(),checkfirst=True)


def downgrade():
    for model in reversed(GRAPH_MODELS):
        model.__table__.drop(op.get_bind(),checkfirst=True)
