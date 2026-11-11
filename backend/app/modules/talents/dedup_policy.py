"""Deterministic identity evidence, separate from content/vector similarity."""
import re
import unicodedata

from app.core.normalize import normalize_email, normalize_phone

POLICY_VERSION = 'identity-v1'
DECISION_FIELD = '_dedup_decision_v1'
AUTO_NOTE = 'identity-v1:automatic'


def assess(basic, target, *, raw_text, candidate_count):
    """Rule scores are not calibrated probabilities. Only exact identity can be high."""
    incoming = {
        'name': str(basic.get('name') or '').strip(),
        'phone': normalize_phone(basic.get('phone')),
        'email': normalize_email(basic.get('email')),
        'birth_year': basic.get('birth_year'), 'gender': basic.get('gender'),
    }
    existing = {
        'name': str(target.name or '').strip(),
        'phone': normalize_phone(target.phone), 'email': normalize_email(target.email),
        'birth_year': target.birth_year, 'gender': target.gender,
    }
    matched = tuple(k for k in ('name', 'phone', 'email')
                    if incoming[k] and incoming[k] == existing[k])
    conflicts = tuple(k for k in incoming if incoming[k] not in (None, '')
                      and existing[k] not in (None, '') and str(incoming[k]) != str(existing[k]))
    blockers = []
    if candidate_count != 1:
        blockers.append('multiple_candidates')
    if incoming['name'] in ('未知', '未提供', 'unknown', 'Unknown'):
        blockers.append('placeholder_name')
    raw = unicodedata.normalize('NFKC', raw_text or '')
    phone = incoming['phone']
    # Verify this number, rather than greedily joining digits from the next email/token.
    phone_pattern = r'(?<!\d)(?:\+?86[ ()-]*|0086[ ()-]*)?' + r'[ ()-]*'.join(
        re.escape(char) for char in phone or '') + r'(?!\d)'
    phone_verified = bool(phone and len(phone) >= 7 and re.search(phone_pattern, raw))
    emails = {normalize_email(v) for v in re.findall(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', raw)}
    if not (incoming['name'] and incoming['name'] in raw
            and phone_verified and incoming['email'] in emails
            and incoming['phone'] and incoming['email']):
        blockers.append('source_unverified')
    high = len(matched) == 3 and not conflicts and not blockers
    return dict(confidence='high' if high else 'medium',
                score=100 if high else 70 if len(matched) >= 2 else 50,
                matched_fields=matched, conflict_fields=conflicts, block_reasons=tuple(blockers),
                candidate_count=candidate_count)


def snapshot(match, *, automatic=False):
    return dict(policy_version=POLICY_VERSION, confidence=match.confidence, score=match.score,
                matched_fields=list(match.matched_fields), conflict_fields=list(match.conflict_fields),
                block_reasons=list(match.block_reasons), candidate_count=match.candidate_count,
                automatic=automatic)
