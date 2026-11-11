"""Versioned vocabulary; literal mentions are review candidates, never inferred abilities."""
import re
import json
from hashlib import sha256
from functools import lru_cache

VERSION = 'talent-tags-v1'
REVIEW_POLICY = 'literal-context-v1'
MAX_EVIDENCE = 8
_GROUPS = {
    'skill': (
        'Python|Java|JavaScript|TypeScript|Go|Rust|C语言|C++|C#|PHP|Ruby|Swift|Kotlin|Scala|R语言|MATLAB|'
        'SQL|HTML|CSS|React|Vue|Angular|Next.js|Node.js|Django|Flask|FastAPI|Spring|Spring Boot|'
        '.NET|Android|iOS|Flutter|React Native|微信小程序|MySQL|PostgreSQL|SQLite|Oracle|SQL Server|'
        'MongoDB|Redis|Elasticsearch|Milvus|Neo4j|Kafka|RabbitMQ|Spark|Hadoop|Flink|Airflow|'
        'Docker|Kubernetes|Linux|Git|Jenkins|GitLab CI|Terraform|Ansible|Nginx|Prometheus|Grafana|'
        'PyTorch|TensorFlow|scikit-learn|Pandas|NumPy|OpenCV|机器学习|深度学习|自然语言处理|计算机视觉|'
        '数据分析|数据建模|数据治理|数据仓库|ETL|RAG|大语言模型|提示词工程|向量检索|知识图谱|'
        '单元测试|自动化测试|性能测试|安全测试|接口测试|需求分析|系统设计|项目管理|产品设计|'
        '用户研究|交互设计|Figma|Axure|原型设计|敏捷开发|Scrum|网络安全|渗透测试|'
        '财务分析|成本核算|预算管理|供应链管理|采购管理|质量管理|生产计划|库存管理|'
        '招聘管理|薪酬管理|绩效管理|培训管理|劳动关系|销售管理|客户服务|市场调研'
    ),
    'industry': '制造业|金融业|保险业|银行业|零售业|电子商务|物流运输|医疗健康|生物医药|教育培训|'
        '能源电力|建筑工程|房地产|汽车行业|半导体|通信行业|软件服务|信息安全|文化传媒|旅游酒店|农业|公共服务',
    'level': '助理工程师|初级工程师|中级工程师|高级工程师|技术专家|架构师|项目经理|产品经理|部门经理|总监',
    'general': '跨部门协作|团队协作|客户沟通|技术写作|文档编写|知识分享|技术培训|需求调研|'
        '风险管理|变更管理|问题分析|流程优化|质量改进|供应商沟通|合同管理|合规管理|'
        '数据可视化|技术支持|方案设计|业务分析',
}
_ALIASES = {'Go': ('golang',), 'Kubernetes': ('k8s',), 'PostgreSQL': ('Postgres',),
            'JavaScript': ('ECMAScript',), '大语言模型': ('LLM',)}


def catalog():
    items = [
        {'code': kind + ':' + name, 'name': name, 'type': kind,
         'aliases': list(_ALIASES.get(name, ()))}
        for kind, names in _GROUPS.items() for name in names.split('|')]
    return {'version': VERSION, 'review_policy_version': REVIEW_POLICY, 'items': items,
        'coverage': {'vocabulary_size': len(items),
            'by_type': {kind: len(names.split('|')) for kind, names in _GROUPS.items()},
            'business_review_status': 'pending',
            'basis': '版本化开发词表；数量不是个人标签目标，也不代表行业覆盖已验收'}}


@lru_cache(maxsize=256)
def _pattern(name, aliases):
    patterns = []
    for value in sorted([name, *aliases], key=len, reverse=True):
        left = r'(?<![A-Za-z0-9_])' if value[0].isascii() and value[0].isalnum() else ''
        right = r'(?![A-Za-z0-9_])' if value[-1].isascii() and value[-1].isalnum() else ''
        patterns.append(left + re.escape(value) + right)
    return re.compile('|'.join(patterns), re.IGNORECASE)


def context_status(text, start, end):
    # Review hints only. 'mentioned' deliberately does not mean verified ability.
    split = r'[。！？；;\n，,]|但(?:是)?|不过|然而|\bbut\b|\bhowever\b'
    before = re.split(split, text[max(0, start-80):start], flags=re.I)[-1]
    after = re.split(split, text[end:end+35], flags=re.I)[0]
    negative = (r'不(?:会|熟悉|擅长|具备|掌握)|未(?:曾)?(?:使用|接触|掌握|学过)|'
                r'没有\s*$|无\s*$|缺乏|not (?:familiar|proficient)|never (?:used|worked)|no (?:experience|knowledge)(?: with| of)?\s*$')
    if re.search(negative, before, re.I) or re.match(r'\s*(?:经验不足|经验欠缺|尚未掌握|不熟悉|经验为零)', after):
        return 'negated'
    if re.search(r'计划(?:学习|使用|掌握)?|准备学习|希望学习|正在学习|打算|拟学习|求职意向|期望职位|'
                 r'plan(?:ning)? to (?:learn|use)|want to learn|currently learning', before, re.I):
        return 'aspirational'
    return 'mentioned'


def suggest(snapshot):
    pages = snapshot.get('source_pages') or [dict(row, text=row['snippet'])
        for row in snapshot.get('evidence', []) if row.get('snippet')]
    output = []
    for definition in catalog()['items']:
        pattern = _pattern(definition['name'], tuple(definition['aliases']))
        evidence, truncated = [], False
        for page in pages:
            text = page.get('text', '')
            for match in pattern.finditer(text):
                if len(evidence) >= MAX_EVIDENCE:
                    truncated = True
                    break
                evidence.append({'matched_text': match.group(), 'file_id': page['file_id'],
                    'filename': page.get('filename'), 'page_no': page.get('page_no'),
                    'snippet': text[max(0, match.start()-50):match.end()+100],
                    'char_start': match.start(), 'char_end': match.end(),
                    'context_status': context_status(text, match.start(), match.end()),
                    'quality_status': page.get('quality_status', 'unknown')})
            if truncated:
                break
        if evidence:
            states = {item['context_status'] for item in evidence}
            identity = [snapshot['person_id'], snapshot['profile_version'], VERSION, REVIEW_POLICY, definition['code'], evidence]
            output.append({**definition, **evidence[0], 'evidence': evidence,
                'candidate_id': sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                'context_status': next(iter(states)) if len(states) == 1 else 'mixed',
                'evidence_truncated': truncated, 'review_status': 'pending_review'})
    return {'person_id': snapshot['person_id'], 'profile_version': snapshot['profile_version'],
            'catalog_version': VERSION, 'review_policy_version': REVIEW_POLICY, 'items': output,
            'source_truncated': bool(snapshot.get('truncated')),
            'source_partial': bool(snapshot.get('truncated') or snapshot.get('coverage') == 'partial')}
