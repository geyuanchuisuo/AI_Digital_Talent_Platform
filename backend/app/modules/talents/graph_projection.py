"""B-owned deterministic graph evidence. No graph connection or inferred ability."""
from hashlib import sha256
import json
import unicodedata

from app.core.errors import BizError
from app.core.normalize import normalize_skill, SKILL_ALIASES
from app.modules.talents import tag_catalog

VERSION = 'b09-literal-v1'
KINDS = {'profile': 'Person', 'knowledge': 'Policy', 'job': 'Job'}
MAX_TEXT = 2_000_000
MAX_PARTS = 4000
MAX_TERMS = 300
MAX_CITATIONS = 8


def _hash(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _id(value):
    if type(value) is not int or value < 1:
        raise BizError(422, '图谱来源ID和版本必须为正整数')
    return value


def _name(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def _root(kind, source_id, version, label):
    if kind not in KINDS:
        raise BizError(422, '不支持的图谱来源类型')
    _id(source_id);_id(version)
    return {'key': f'{kind}:{source_id}:v{version}', 'kind': KINDS[kind],
        'source_kind': kind, 'source_id': source_id, 'source_version': version,
        'label': str(label or '')[:200]}


def tombstone(kind, source_id, version):
    root = _root(kind, source_id, version, '')
    return {'operation': 'invalidate', 'source_kind': kind, 'source_id': source_id,
        'through_version': version, 'root_key': root['key'], 'projection_version': VERSION}


def _projection(root, parts, *, skills=(), certificates=(), partial=False):
    if any(not isinstance(p.get('text', ''), str) or type(p.get('offset_base',0)) is not int
           or p.get('offset_base',0)<0 for p in parts):
        raise BizError(422, '图谱原文或定位格式无效')
    if len(parts) > MAX_PARTS or sum(len(p.get('text', '')) for p in parts) > MAX_TEXT:
        raise BizError(422, '图谱证据超过本阶段上限，需分批处理')
    definitions = {}
    for row in tag_catalog.catalog()['items']:
        if row['type'] == 'skill':
            definitions[normalize_skill(row['name'])] = (row['name'], tuple(row['aliases']))
    for skill in skills:
        if isinstance(skill, str) and skill.strip() and len(skill) <= 100:
            canonical = normalize_skill(skill)
            if not canonical:
                continue
            label, aliases = definitions.get(canonical, (skill, ()))
            # Normalization and source matching must share the public alias contract.
            variants = {skill, *aliases, *(alias for alias, target in SKILL_ALIASES.items()
                                         if normalize_skill(target)==canonical)}
            definitions[canonical] = (label, tuple(sorted(variants)))
    certs = {_name(v): v for v in certificates if isinstance(v, str) and v.strip() and len(v) <= 100}
    if len(definitions)+len(certs) > MAX_TERMS:
        raise BizError(422, '图谱候选词超过上限，需分批处理')
    terms = [('Skill', key, value) for key, value in definitions.items()]
    terms += [('Certificate', key, (value, ())) for key, value in sorted(certs.items())]
    nodes = [root]
    edges = []
    bounded = False
    for kind, canonical, (term, aliases) in terms:
        evidence, truncated = [], False
        pattern = tag_catalog._pattern(term, aliases)
        for part in parts:
            text = part.get('text', '')
            for match in pattern.finditer(text):
                citation = {k: v for k, v in part.items() if k != 'text'}
                citation.update(source_kind=root['source_kind'], source_id=root['source_id'],
                    source_version=root['source_version'], char_start=part.get('offset_base', 0)+match.start(),
                    char_end=part.get('offset_base', 0)+match.end(), matched_text=match.group(),
                    snippet_start=part.get('offset_base',0)+max(0,match.start()-50),
                    snippet_end=part.get('offset_base',0)+min(len(text),match.end()+100),
                    snippet=text[max(0, match.start()-50):match.end()+100],
                    context_status=tag_catalog.context_status(text, match.start(), match.end()))
                # Overlapping knowledge chunks can contain the same physical occurrence.
                identity = (citation.get('file_id'), citation.get('source_part'), citation.get('document_id'), citation.get('page_no'),
                    citation.get('char_start'), citation.get('char_end'), citation.get('matched_text'))
                if any(e['_identity'] == identity for e in evidence):
                    continue
                if len(evidence) >= MAX_CITATIONS:
                    truncated = True;break
                evidence.append({**citation, '_identity': identity})
            if truncated:break
        if evidence:
            for item in evidence:item.pop('_identity')
            key = f'{kind.lower()}:{_hash(canonical)}'
            nodes.append({'key': key, 'kind': kind, 'label': canonical})
            relation = 'MENTIONS_SKILL' if kind == 'Skill' else 'MENTIONS_CERTIFICATE'
            edges.append({'key': _hash([root['key'], relation, key]), 'from': root['key'], 'to': key,
                'type': relation, 'review_status': 'pending_review', 'ability_verified': False,
                'evidence': evidence, 'evidence_truncated': truncated})
            bounded = bounded or truncated
    nodes.sort(key=lambda n:n['key']);edges.sort(key=lambda e:e['key'])
    payload = {'projection_version': VERSION, 'context_policy': tag_catalog.REVIEW_POLICY,
        'root': root, 'nodes': nodes, 'edges': edges, 'neo4j_state': 'not_connected',
        'requires_current_authorization': True,
        'coverage': {'complete': bool(parts) and not partial and not bounded,
            'source_partial': partial or not parts, 'evidence_truncated': bounded,
            'scope': 'declared_literal_candidates', 'semantic_extraction_complete': False,
            'basis': 'literal_mentions_not_capability'}}
    payload['fingerprint'] = _hash({k:v for k,v in payload.items() if k != 'neo4j_state'})
    return payload


def profile_projection(snapshot):
    if snapshot.get('status') != 'ready' or snapshot.get('version_source') != 'persisted':
        raise BizError(409, '图谱预览需要当前可访问的正式档案版本')
    root = _root('profile', snapshot['person_id'], snapshot['profile_version'], (snapshot.get('profile') or {}).get('name'))
    parts = []
    if snapshot.get('source_pages'):
        for index,page in enumerate(snapshot['source_pages']):
            parts.append({**{k:v for k,v in page.items() if k in ('file_id','filename','page_no','text','quality_status','locator_type')},
                'source_part':index,'offset_unit':'source_part_text'})
    else:
        parts = [{**{k:v for k,v in row.items() if k in ('file_id','filename','page_no')},
            'text':row['snippet'],'locator_type':'legacy_snippet','source_part':index,'offset_unit':'legacy_snippet'}
            for index,row in enumerate(snapshot.get('evidence',[])) if row.get('snippet')]
    sources = [snapshot.get('parsed') or {}, *snapshot.get('sources', [])]
    skills = [*(snapshot.get('profile') or {}).get('skills', [])]
    certificates = []
    for source in sources:
        skills.extend(source.get('skills') or [])
        certificates.extend(source.get('certificates') or [])
    partial = bool(snapshot.get('truncated') or snapshot.get('coverage')=='partial'
        or any(p.get('quality_status','unknown')!='ready' for p in parts))
    return _projection(root, parts, skills=skills, certificates=certificates, partial=partial)


def policy_projection(*, document_id, revision_id, version, title, chunks):
    root = _root('knowledge', document_id, version, title)
    root['revision_id'] = _id(revision_id)
    parts = [{'document_id': document_id, 'revision_id': revision_id, 'chunk_id': _id(c['id']),
        'page_no': c.get('page_no'), 'locator_type':'chunk', 'offset_base': c['offset_start'],
        'text': c['text']} for c in chunks]
    return _projection(root, parts)


def job_projection(snapshot):
    # This pure producer contract is not an A authorization/reading implementation.
    root = _root('job', snapshot['job_id'], snapshot['version'], snapshot.get('title'))
    text = snapshot.get('jd_text') or ''
    return _projection(root, [{'text': text, 'locator_type':'jd_text','page_no':None}] if text else [],
        skills=snapshot.get('must_skills') or [])
