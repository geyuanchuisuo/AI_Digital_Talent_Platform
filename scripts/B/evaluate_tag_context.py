"""Reproducible synthetic mention/context evaluation, never business acceptance."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'backend'))


def evaluate(path):
    from app.modules.talents import tag_catalog
    data = Path(path).read_bytes()
    fixture = json.loads(data.decode('utf-8'))
    results = []
    matched_industries = set()
    for number, case in enumerate(fixture['cases'], 1):
        snapshot = {'person_id': number, 'profile_version': 1, 'source_pages': [
            {'file_id': number, 'filename': 'synthetic.txt', 'page_no': None,
             'quality_status': 'ready', 'text': case['text']}]}
        found = tag_catalog.suggest(snapshot)
        actual = {r['code']: r['context_status'] for r in found['items']}
        matched_industries.update(code for code in actual if code.startswith('industry:'))
        results.append({'id': case['id'], 'passed': actual==case['expected'],
                        'expected': case['expected'], 'actual': actual})
    return {'fixture_version': fixture['version'], 'fixture_sha256': sha256(data).hexdigest(),
        'dataset_kind': fixture['dataset_kind'], 'business_review_status': fixture['business_review_status'],
        'catalog_version': tag_catalog.VERSION, 'review_policy_version': tag_catalog.REVIEW_POLICY,
        'vocabulary_coverage': tag_catalog.catalog()['coverage'], 'covered_industry_terms': len(matched_industries),
        'total': len(results), 'passed': sum(r['passed'] for r in results),
        'acceptance_basis': '合成开发样例精确标签/语境核对；不是业务准确率、行业评审或能力验证',
        'results': results}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',type=Path,default=ROOT/'tests/B/fixtures/tag_context_eval_v1.json')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=evaluate(args.fixture)
    if args.output:
        target=args.output.resolve()
        if not target.is_relative_to((ROOT/'backend/data').resolve()):
            parser.error('Evaluation reports must remain in the ignored backend/data directory')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='results'},ensure_ascii=False))
    return 0 if result['passed']==result['total'] else 1


if __name__=='__main__':
    raise SystemExit(main())
