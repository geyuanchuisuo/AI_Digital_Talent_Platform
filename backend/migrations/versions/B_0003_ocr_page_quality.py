"""Record source location kind and per-page OCR quality without a second source store."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "B_0003_ocr_page_quality"
down_revision = "B_0002_merge_workflow_review"
branch_labels = None
depends_on = None


def upgrade():
    columns = [sa.Column("source_type", sa.String(20), nullable=False, server_default="text"),
               sa.Column("quality_status", sa.String(20), nullable=False, server_default="ready"),
               sa.Column("error_message", sa.Text(), nullable=True)]
    existing = set() if op.get_context().as_sql else {
        item["name"] for item in sa.inspect(op.get_bind()).get_columns("resume_pages")}
    for column in columns:
        if column.name not in existing:
            op.add_column("resume_pages", column)
    op.alter_column("resume_pages", "text_content", existing_type=sa.Text(),
                    type_=mysql.MEDIUMTEXT(), existing_nullable=False)


def downgrade():
    if not op.get_context().as_sql:
        large = op.get_bind().execute(sa.text(
            "SELECT COUNT(*) FROM resume_pages WHERE OCTET_LENGTH(text_content) > 65535")).scalar()
        if large:
            raise RuntimeError("Source pages exceed TEXT capacity; refusing a lossy downgrade")
    for name in ("error_message", "quality_status", "source_type"):
        op.drop_column("resume_pages", name)
    op.alter_column("resume_pages", "text_content", existing_type=mysql.MEDIUMTEXT(),
                    type_=sa.Text(), existing_nullable=False)
