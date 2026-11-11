"""Selected-profile record comparison/counting never counts retrieved chunks."""
import pytest

from app.modules.talents.batch_analysis import validate_criteria, build_analysis
from app.core.errors import BizError


def snap(pid, **fields):
    return {'person_id': pid, 'profile_version': 3, 'status': 'ready',
            'snapshot_at': '2026-11-11T14:00:00+08:00', 'coverage': 'complete',
            'profile': {'name': '同名样例', **fields}, 'evidence': []}


def test_all_selected_profiles_are_counted_once_with_unknown_separate():
    rows = [snap(1, skills=['Java']), snap(2, skills=['JavaScript']), snap(3)]
    result = build_analysis(rows, [{'field': 'skills', 'operator': 'contains', 'value': 'java'}])
    assert result['population'] == 3 and result['dedup_key'] == 'person_id'
    assert result['matched_ids'] == [1] and result['not_matched_ids'] == [2]
    assert result['unknown_ids'] == [3] and result['exact'] is False
    assert result['possible_match_count'] == {'min': 1, 'max': 2}
    assert [r['person_id'] for r in result['rows']] == [1, 2, 3]
    assert result['rows'][0]['fields']['skills']['field'] == 'profile.skills'
    assert result['rows'][0]['fields']['skills']['profile_version'] == 3


def test_zero_years_and_and_conditions_are_not_missing_or_or():
    result = build_analysis([snap(1, work_years=0, degree='本科'), snap(2, work_years=5, degree='大专'),
        snap(3, work_years=6, degree='硕士')], [
        {'field': 'work_years', 'operator': 'gte', 'value': 1},
        {'field': 'degree', 'operator': 'gte', 'value': '本科'}])
    assert result['matched_ids'] == [3] and result['not_matched_ids'] == [1, 2]
    assert result['unknown_ids'] == [] and result['exact'] is True


def test_aliases_are_normalized_but_not_substrings():
    result = build_analysis([snap(1, skills=['Golang']), snap(2, skills=['Google'])],
        [{'field': 'skills', 'operator': 'contains', 'value': 'go'}])
    assert result['matched_ids'] == [1] and result['not_matched_ids'] == [2]


def test_truncated_and_processing_failures_cannot_produce_exact_totals():
    a = snap(1, skills=['Python']); a['truncated'] = True
    b = snap(2, skills=['Go']); b['processing_incomplete'] = True
    result = build_analysis([a, b], [{'field': 'skills', 'operator': 'contains', 'value': 'Python'}])
    assert result['exact'] is False and result['unknown_ids'] == [1, 2]


def test_compare_without_criteria_still_lists_bound_fields_for_same_names():
    result = build_analysis([snap(1, city='甲城'), snap(2, city='乙城')], [])
    assert result['rows'][0]['fields']['city']['value'] == '甲城'
    assert result['rows'][1]['fields']['city']['value'] == '乙城'
    assert result['rows'][0]['person_id'] != result['rows'][1]['person_id']
    assert result['exact'] is False and result['matched_ids'] == []


@pytest.mark.parametrize('criteria', [
    [{'field': 'phone', 'operator': 'eq', 'value': '1'}],
    [{'field': 'work_years', 'operator': 'gte', 'value': -1}],
    [{'field': 'work_years', 'operator': 'gte', 'value': 'NaN'}],
    [{'field': 'skills', 'operator': 'sql', 'value': 'select 1'}],
    [{'field': 'degree', 'operator': 'gte', 'value': '随意学历'}],
    [{'field': 'city', 'operator': 'gte', 'value': '北京'}],
    [{'field': 'skills', 'operator': 'contains', 'value': ''}],
    [{'field': 'city', 'operator': 'eq', 'value': '城', 'sql': 'raw'}],
    [{'field': 'city', 'operator': ['eq'], 'value': '城'}],
])
def test_invalid_or_unsupported_criteria_are_rejected(criteria):
    with pytest.raises(BizError) as error:
        validate_criteria(criteria)
    assert error.value.code == 422


def test_duplicate_people_are_not_silently_double_counted():
    with pytest.raises(BizError):
        build_analysis([snap(1), snap(1)], [])


def test_malformed_record_values_are_unknown_not_task_failures():
    result = build_analysis([snap(1, degree=['本科'])], [{'field': 'degree', 'operator': 'gte', 'value': '本科'}])
    assert result['unknown_ids'] == [1]
