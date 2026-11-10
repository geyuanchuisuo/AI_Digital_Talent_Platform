"""Versioned vocabulary; literal mentions are review candidates, never inferred abilities."""
import re

VERSION = 'talent-tags-v1'
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
    return {'version': VERSION, 'items': [
        {'code': kind + ':' + name, 'name': name, 'type': kind,
         'aliases': list(_ALIASES.get(name, ()))}
        for kind, names in _GROUPS.items() for name in names.split('|')]}


def suggest(snapshot):
    pages = snapshot.get('source_pages') or [dict(row, text=row['snippet'])
        for row in snapshot.get('evidence', []) if row.get('snippet')]
    output = []
    for definition in catalog()['items']:
        alternatives = sorted([definition['name'], *definition['aliases']], key=len, reverse=True)
        # ASCII boundaries allow Chinese-adjacent mentions without Go matching Google.
        patterns = []
        for name in alternatives:
            left = r'(?<![A-Za-z0-9_])' if name[0].isascii() and name[0].isalnum() else ''
            right = r'(?![A-Za-z0-9_])' if name[-1].isascii() and name[-1].isalnum() else ''
            patterns.append(left + re.escape(name) + right)
        pattern = re.compile('|'.join(patterns), re.IGNORECASE)
        for page in pages:
            text = page.get('text', '')
            match = pattern.search(text)
            if match:
                output.append({**definition, 'matched_text': match.group(), 'file_id': page['file_id'],
                    'filename': page.get('filename'), 'page_no': page.get('page_no'),
                    'snippet': text[max(0, match.start()-50):match.end()+100],
                    'review_status': 'pending_review'})
                break
    return {'person_id': snapshot['person_id'], 'profile_version': snapshot['profile_version'],
            'catalog_version': VERSION, 'items': output}
