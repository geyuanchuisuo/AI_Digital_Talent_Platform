"""Persist the transition from legacy roles to explicit RBAC authorization."""
from alembic import op
import sqlalchemy as sa

revision = "A_0014_account_scope_mode"
down_revision = "B_0010_merge_a13"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "data_scope_mode" not in {c["name"] for c in sa.inspect(bind).get_columns("users")}:
        op.add_column("users", sa.Column("data_scope_mode", sa.String(20), nullable=False, server_default="legacy"))
    # Includes disabled/orphaned bindings: their disappearance must not widen scope.
    op.execute(sa.text("UPDATE users SET data_scope_mode = 'scoped' "
                       "WHERE id IN (SELECT user_id FROM sys_user_roles)"))


def downgrade():
    # Losing this marker would silently restore legacy all after revocation.
    raise RuntimeError("Authorization mode downgrade requires an explicit security migration")
