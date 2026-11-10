from pathlib import Path
from alembic.config import Config
from alembic.script import ScriptDirectory


def test_integrated_b_and_d_have_one_migration_head():
    config = Config()
    config.set_main_option('script_location', str(Path(__file__).parents[2] / 'backend/migrations'))
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) == 1
    head = script.get_revision(script.get_current_head())
    assert head.revision == 'B_0009_merge_a11'
    assert set(head.down_revision) == {'A_0011_app_snapshot','B_0008_profile_department'}
    from_b8 = {s.revision.revision for s in script._upgrade_revs(head.revision,'B_0008_profile_department')}
    assert from_b8 == {'D_0012_digital_employee','A_0009_users_department_id','A_0010_batch_idempotency',
                      'A_0011_app_snapshot','B_0009_merge_a11'}
    from_a11 = {s.revision.revision for s in script._upgrade_revs(head.revision,'A_0011_app_snapshot')}
    assert from_a11 == {'B_0008_profile_department','B_0009_merge_a11'}
    head=script.get_revision('B_0008_profile_department')
    assert head.down_revision == 'B_0007_merge_learning_evidence'
    head=script.get_revision(head.down_revision)
    integrated_head = head
    qa_head = script.get_revision('B_0006_profile_questions')
    assert set(integrated_head.down_revision) == {qa_head.revision,'C_0007_learning_evidence_key'}
    assert {s.revision.revision for s in script._upgrade_revs(integrated_head.revision,qa_head.revision)} == {
        integrated_head.revision,'C_0007_learning_evidence_key','D_0010_workflow_skills','D_0011_ai_platform'}
    assert {s.revision.revision for s in script._upgrade_revs(integrated_head.revision,'C_0007_learning_evidence_key')} == {
        integrated_head.revision,qa_head.revision,'B_0005_merge_c_capacity',
        'B_0003_ocr_page_quality','B_0004_parse_attempt_trace'}
    head = script.get_revision('B_0005_merge_c_capacity')
    assert qa_head.down_revision == head.revision
    trace = script.get_revision('B_0004_parse_attempt_trace')
    quality = script.get_revision('B_0003_ocr_page_quality')
    merge = script.get_revision('B_0002_merge_workflow_review')
    assert set(head.down_revision) == {trace.revision, 'C_0006_course_capacity'}
    assert trace.down_revision == quality.revision
    assert quality.down_revision == merge.revision
    assert set(merge.down_revision) == {'B_0001_profile_history', 'D_0009_workflow_review'}
    # Use the actual Alembic command planner: a simple ancestry walk omits sibling branches.
    from_b = {step.revision.revision for step in script._upgrade_revs(head.revision, 'B_0001_profile_history')}
    from_d = {step.revision.revision for step in script._upgrade_revs(head.revision, 'D_0009_workflow_review')}
    additions = {head.revision, trace.revision, quality.revision, merge.revision,
                 'C_0004_application_fk', 'C_0005_plan_item_due_date', 'C_0006_course_capacity'}
    assert from_b == additions | {'D_0009_workflow_review'}
    assert from_d == additions | {'B_0001_profile_history'}
    from_trace = {step.revision.revision for step in script._upgrade_revs(head.revision, trace.revision)}
    from_capacity = {step.revision.revision for step in script._upgrade_revs(head.revision, 'C_0006_course_capacity')}
    assert from_trace == {head.revision, 'C_0004_application_fk', 'C_0005_plan_item_due_date', 'C_0006_course_capacity'}
    assert from_capacity == {head.revision, quality.revision, trace.revision}
