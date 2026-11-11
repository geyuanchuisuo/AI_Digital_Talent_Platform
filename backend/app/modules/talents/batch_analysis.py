"""Exact checks of selected, frozen profile records; no retrieval or generated SQL."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import unicodedata

from app.core.errors import BizError
from app.core.normalize import DEGREE_RANK, normalize_skill, normalize_skills

FIELDS = ('skills', 'degree', 'work_years', 'city', 'current_title')
OPERATORS = {'skills': {'contains'}, 'degree': {'eq', 'gte'}, 'work_years': {'gte'},
             'city': {'eq'}, 'current_title': {'eq'}}
VERSION = 'selected-profile-fields-v1'


def _text(value):
    return unicodedata.normalize('NFKC', value).strip().casefold()


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and 0 <= result <= 100 else None
    except (InvalidOperation, ValueError):
        return None


def validate_criteria(criteria):
    if not isinstance(criteria, list) or len(criteria) > 5:
        raise BizError(422, '最多支持5个明确的档案字段条件')
    result = []
    for item in criteria:
        if not isinstance(item, dict) or set(item) != {'field', 'operator', 'value'}:
            raise BizError(422, '条件只接受field/operator/value')
        field, op, value = item['field'], item['operator'], item['value']
        if not isinstance(field, str) or not isinstance(op, str) or field not in OPERATORS or op not in OPERATORS[field]:
            raise BizError(422, '不支持该档案字段或比较方式')
        if field == 'work_years':
            number = _number(value)
            if number is None:
                raise BizError(422, '工作年限条件需要0至100的有限数值')
            value = float(number)
        else:
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
                raise BizError(422, '条件值需要1至100个字符')
            value = value.strip()
            if field == 'degree' and (value not in DEGREE_RANK or value == '其他'):
                raise BizError(422, '请选择明确的学历档位')
            if field == 'skills':
                value = normalize_skill(value)
                if not value:
                    raise BizError(422, '技能条件不能为空')
        result.append({'field': field, 'operator': op, 'value': value})
    return result


def _check(profile, condition):
    field, op, expected = condition['field'], condition['operator'], condition['value']
    value = profile.get(field)
    if value is None or value == '' or value == []:
        return None
    if field == 'skills':
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return None
        return expected in normalize_skills(value)
    if field == 'degree':
        if not isinstance(value, str) or value not in DEGREE_RANK or value == '其他':
            return None
        return value == expected if op == 'eq' else DEGREE_RANK[value] >= DEGREE_RANK[expected]
    if field == 'work_years':
        number = _number(value)
        return None if number is None else number >= Decimal(str(expected))
    return _text(value) == _text(expected) if isinstance(value, str) else None


def build_analysis(snapshots, criteria):
    criteria = validate_criteria(criteria)
    ids = [s['person_id'] for s in snapshots]
    if len(set(ids)) != len(ids):
        raise BizError(422, '统计范围中包含重复人员')
    rows, groups = [], {'matched': [], 'not_matched': [], 'unknown': []}
    partial = False
    for saved in snapshots:
        profile = saved.get('profile') or {}
        incomplete = bool(saved.get('truncated') or saved.get('processing_incomplete') or saved.get('status') != 'ready')
        checks = [None if incomplete else _check(profile, criterion) for criterion in criteria]
        status = ('not_matched' if False in checks else 'unknown' if not checks or None in checks else 'matched')
        groups[status].append(saved['person_id'])
        source_partial = incomplete or saved.get('coverage') == 'partial'
        partial = partial or source_partial
        fields = {}
        for field in FIELDS:
            value = deepcopy(profile.get(field))
            if field == 'work_years' and value is not None:
                number = _number(value)
                value = float(number) if number is not None else None
            fields[field] = {'value': value, 'field': 'profile.' + field,
                'person_id': saved['person_id'], 'profile_version': saved['profile_version'],
                'snapshot_at': saved.get('snapshot_at')}
        rows.append({'person_id': saved['person_id'], 'profile_version': saved['profile_version'],
            'name': profile.get('name'), 'status': status, 'fields': fields,
            'condition_results': checks, 'source_partial': source_partial})
    return {'analysis_version': VERSION, 'scope_kind': 'selected_profiles', 'dedup_key': 'person_id',
        'population': len(ids), 'person_ids': ids, 'criteria': criteria, 'logic': 'and',
        'computed_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'time_basis': 'fixed_profile_versions', 'rows': rows,
        'matched_ids': groups['matched'], 'not_matched_ids': groups['not_matched'], 'unknown_ids': groups['unknown'],
        'matched_count': len(groups['matched']), 'not_matched_count': len(groups['not_matched']),
        'unknown_count': len(groups['unknown']), 'coverage_incomplete': partial,
        'exact': bool(criteria) and not groups['unknown'] and not partial,
        'possible_match_count': {'min': len(groups['matched']), 'max': len(groups['matched']) + len(groups['unknown'])},
        'basis': '所选固定版本的档案字段记录；字段未命中不代表本人不具备该能力'}
