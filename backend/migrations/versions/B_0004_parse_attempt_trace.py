"""Preserve the input/output and prompt configuration of each parse attempt."""
from alembic import op
import sqlalchemy as sa

revision = "B_0004_parse_attempt_trace"
down_revision = "B_0003_ocr_page_quality"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().as_sql or 'trace_json' not in {
            item['name'] for item in sa.inspect(op.get_bind()).get_columns('parse_attempts')}:
        op.add_column('parse_attempts', sa.Column('trace_json', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('parse_attempts', 'trace_json')
