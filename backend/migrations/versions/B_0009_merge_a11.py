"""Merge approved master A11 with the existing B department migration."""
revision = 'B_0009_merge_a11'
down_revision = ('A_0011_app_snapshot', 'B_0008_profile_department')
branch_labels = None
depends_on = None


def upgrade():
    # Old B8 databases applied C7 before its upstream parent was changed to D11.
    # Alembic now regards D10/D11 as ancestors and skips their create-table steps.
    from alembic import op
    from app.models import Base
    names = ('workflow_skills', 'workflow_skill_versions', 'ai_providers',
             'ai_prompt_versions', 'ai_call_logs')
    Base.metadata.create_all(op.get_bind(), tables=[Base.metadata.tables[n] for n in names], checkfirst=True)


def downgrade():
    pass
