from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_integrated_b_and_d_have_one_migration_head():
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parents[2] / 'backend/migrations'))
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) == 1
    head = script.get_revision(script.get_current_head())
    assert head.revision == 'B_0004_parse_attempt_trace'
    quality = script.get_revision('B_0003_ocr_page_quality')
    merge = script.get_revision('B_0002_merge_workflow_review')
    assert head.down_revision == quality.revision
    assert quality.down_revision == merge.revision
    assert set(merge.down_revision) == {'B_0001_profile_history', 'D_0009_workflow_review'}
    # Use the actual Alembic command planner: a simple ancestry walk omits sibling branches.
    from_b = {step.revision.revision for step in script._upgrade_revs(head.revision, 'B_0001_profile_history')}
    from_d = {step.revision.revision for step in script._upgrade_revs(head.revision, 'D_0009_workflow_review')}
    assert from_b == {head.revision, quality.revision, merge.revision, 'D_0009_workflow_review'}
    assert from_d == {head.revision, quality.revision, merge.revision, 'B_0001_profile_history'}
