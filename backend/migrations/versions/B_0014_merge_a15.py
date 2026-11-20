"""Merge the B graph/QA/knowledge chain into the A/C/D integrated head.

C_0011 已由 master 改名为 C_0011_enroll_study 并汇入 A_0014_merge_remote，
故此处只保留两条链的汇合：B（B_0013_graph_outbox）与 A/C/D（A_0015_merge_remote2）。
"""
revision = 'B_0014_merge_a15'
down_revision = ('B_0013_graph_outbox', 'A_0015_merge_remote2')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
