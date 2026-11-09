"""Merge verified B OCR history with the integrated C training migrations.

Both branches retain their original revisions; no schema operations are repeated.
"""

revision = "B_0005_merge_c_capacity"
down_revision = ("B_0004_parse_attempt_trace", "C_0006_course_capacity")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
