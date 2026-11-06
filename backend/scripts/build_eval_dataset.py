"""构建 T-30 评测集：AI 合成 22 份简历 DOCX + 两份标注 JSON。

口径（需求 §10 / 算法 §7 / todolist T-30）：
- **AI 合成**：LLM（spark，temperature=0）按「事实表」撰写简历正文——事实由本脚本控制
  （标注即事实，100% 可信），LLM 只负责措辞多样化；生成后校验关键事实全部出现在文本中，
  缺失则重试一次，仍缺失则该份退化为模板直写（保证不丢失样本）。
- 版式多样化：flow（段落流）/ table（经历为表格）/ two_col（双栏表格）三种渲染。
- **子串陷阱样本**：JavaScript-only × Java 岗、Golang 写法、Google Cloud 干扰、c++ × c、
  react-native × react、nosql × sql（判定正确性由 match eval 断言）。
- 标注学历用归一化字面值（六档，D-G）；技能标注为**归一化后规范名**（与入库口径一致）。
- **固定截止日（2026-11-06 修 T-30）**：事实表里 `work_years` 是 ground truth，但「至今」是
  相对时间，按评测日实算会比标注大 1.1~2.9 年。生成正文时一律把「至今」替换为
  `cutoff_for(facts)` = 最早入职年月 + 标注年限，使日期区间合并后恰好等于标注年限
  （生产链路 `calc_work_years(employments)` 同口径）。同时要求正文原样写出「工作年限：X 年」。

用法：`./.venv/Scripts/python.exe scripts/build_eval_dataset.py`（幂等：整目录重建）。
输出：`data/eval/resumes/*.docx`、`data/eval/resume_parse_eval.json`、`data/eval/match_eval.json`。
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).resolve().parents[1]  # backend/
EVAL_DIR = BASE_DIR / "data" / "eval"
RESUME_DIR = EVAL_DIR / "resumes"

# --------------------------------------------------------------------------- #
# 事实表（22 份）：annot 字段即 ground truth；trap 标记子串陷阱样本
# --------------------------------------------------------------------------- #
FACT_SHEETS: list[dict] = [
    # —— 达标样本（后端岗：python+mysql，本科 3 年起）——
    dict(filename="eval-01-张伟-backend-pass.docx", layout="flow",
         facts=dict(name="张伟", phone="13801010101", city="北京", degree="本科", major="计算机科学与技术",
                    school="北京工业大学", work_years=6.0, expected="资深后端工程师",
                    skills=["Python", "FastAPI", "MySQL", "Redis", "Docker"],
                    jobs=[("云梯科技", "高级后端工程师", "2020.03", "至今", "负责订单中台，QPS 提升 3 倍"),
                          ("蓝鲸信息", "后端工程师", "2017.07", "2020.02", "支付网关开发")],
                    projects=["智能推荐平台（2021，负责人）"], certs=["PMP"]),
         expect="passed"),
    dict(filename="eval-02-李娜-backend-pass.docx", layout="table",
         facts=dict(name="李娜", phone="13902020202", city="上海", degree="硕士", major="软件工程",
                    school="复旦大学", work_years=4.5, expected="后端开发专家",
                    skills=["Python 3.11", "MySQL", "FastAPI", "golang"],
                    jobs=[("星河互联", "后端工程师", "2021.06", "至今", "库存系统重构"),
                          ("晨光数据", "Java 开发工程师", "2019.07", "2021.05", "报表服务")],
                    projects=[], certs=["CET-6"]),
         expect="passed"),
    dict(filename="eval-03-王强-backend-pass.docx", layout="flow",
         facts=dict(name="王强", phone="13603030303", city="深圳", degree="本科", major="信息管理",
                    school="华南理工大学", work_years=3.2, expected="后端工程师",
                    skills=["python", "MySQL", "Flask", "Redis"],
                    jobs=[("鹏城软件", "后端工程师", "2022.04", "至今", "CRM 系统开发")],
                    projects=[], certs=[]),
         expect="passed"),
    dict(filename="eval-04-赵敏-backend-pass.docx", layout="two_col",
         facts=dict(name="赵敏", phone="13504040404", city="杭州", degree="博士", major="计算机应用",
                    school="浙江大学", work_years=3.5, expected="算法后端工程师",
                    skills=["Python", "MySQL", "FastAPI", "Milvus", "Kubernetes"],
                    jobs=[("阿里云实习转正", "研发工程师", "2023.07", "至今", "向量检索服务")],
                    projects=["大规模语义检索系统（2023）"], certs=[]),
         expect="passed"),  # 博士 3.5 年 → 达标
    dict(filename="eval-05-陈晨-backend-edge-3y.docx", layout="flow",
         facts=dict(name="陈晨", phone="13705050505", city="成都", degree="本科", major="软件工程",
                    school="电子科技大学", work_years=3.0, expected="后端工程师",
                    skills=["Python", "MySQL", "Django"],
                    jobs=[("天府信息", "后端工程师", "2022.11", "至今", "会员系统开发")],
                    projects=[], certs=[]),
         expect="passed"),  # 边缘：恰好 3.0 年 → 达标
    dict(filename="eval-06-周洁-backend-pass.docx", layout="table",
         facts=dict(name="周洁", phone="18806060606", city="南京", degree="硕士", major="计算机技术",
                    school="东南大学", work_years=5.0, expected="高级后端工程师",
                    skills=["Python", "MySQL", "FastAPI", "MongoDB"],
                    jobs=[("金陵科技", "高级后端工程师", "2021.01", "至今", "风控系统"),
                          ("紫金软件", "后端工程师", "2018.07", "2020.12", "OA 系统")],
                    projects=[], certs=[]),
         expect="passed"),
    dict(filename="eval-07-吴磊-backend-pass.docx", layout="flow",
         facts=dict(name="吴磊", phone="13907070707", city="武汉", degree="本科", major="网络工程",
                    school="武汉大学", work_years=7.5, expected="技术专家",
                    skills=["Python", "MySQL", "Redis", "Kafka", "golang"],
                    jobs=[("长江存储", "资深工程师", "2019.05", "至今", "数据管道"),
                          ("光谷信息", "工程师", "2015.07", "2019.04", "运维平台")],
                    projects=[], certs=["RHCE"]),
         expect="passed"),
    dict(filename="eval-08-郑爽-backend-pass.docx", layout="two_col",
         facts=dict(name="郑爽", phone="18608080808", city="西安", degree="本科", major="计算机科学",
                    school="西安交通大学", work_years=4.0, expected="后端工程师",
                    skills=["Python", "MySQL", "FastAPI"],
                    jobs=[("秦风科技", "后端工程师", "2021.09", "至今", "网关服务")],
                    projects=[], certs=[]),
         expect="passed"),
    # —— 不达标：技能缺失 / 学历不足 / 年限不足 / 边缘 ——
    dict(filename="eval-09-孙悦-backend-no-degree.docx", layout="flow",
         facts=dict(name="孙悦", phone="15009090909", city="北京", degree="大专", major="软件技术",
                    school="北京信息职业技术学院", work_years=6.0, expected="后端工程师",
                    skills=["Python", "MySQL", "FastAPI"],
                    jobs=[("亦庄信息", "后端工程师", "2019.03", "至今", "内容管理")],
                    projects=[], certs=[]),
         expect="rejected"),  # 学历不足（大专 < 本科）
    dict(filename="eval-10-马涛-backend-edge-2y9m.docx", layout="flow",
         facts=dict(name="马涛", phone="15201010102", city="广州", degree="本科", major="计算机科学",
                    school="中山大学", work_years=2.9, expected="后端工程师",
                    skills=["Python", "MySQL"],
                    jobs=[("珠江数码", "后端工程师", "2023.01", "至今", "小程序后端")],
                    projects=[], certs=[]),
         expect="rejected"),  # 边缘：2.9 年 < 3
    dict(filename="eval-11-林芳-backend-no-mysql.docx", layout="flow",
         facts=dict(name="林芳", phone="15302020203", city="厦门", degree="本科", major="计算机科学",
                    school="厦门大学", work_years=4.0, expected="后端工程师",
                    skills=["Python", "Flask", "SQLite", "Redis"],
                    jobs=[("鹭岛数据", "后端工程师", "2021.05", "至今", "数据服务")],
                    projects=[], certs=[]),
         expect="rejected"),  # 缺 MySQL
    dict(filename="eval-12-何军-backend-fresh.docx", layout="flow",
         facts=dict(name="何军", phone="15603030304", city="长沙", degree="本科", major="软件工程",
                    school="湖南大学", work_years=1.0, expected="初级后端工程师",
                    skills=["Python", "MySQL"],
                    jobs=[("湘江科技", "初级工程师", "2024.07", "至今", "内部工具")],
                    projects=[], certs=[]),
         expect="rejected"),  # 年限不足
    # —— 子串陷阱（对 Java 岗：只有 JavaScript → 必须不命中；go/google、c/c++、sql/nosql、react/react-native）——
    dict(filename="eval-13-冯旭-trap-javascript.docx", layout="flow",
         facts=dict(name="冯旭", phone="15804040405", city="深圳", degree="本科", major="软件工程",
                    school="深圳大学", work_years=5.0, expected="前端工程师",
                    skills=["JavaScript", "TypeScript", "Vue", "Webpack"],
                    jobs=[("南山网络", "前端工程师", "2020.06", "至今", "营销活动页")],
                    projects=[], certs=[]),
         expect="rejected", trap="javascript/java"),
    dict(filename="eval-14-苏晴-trap-go-google.docx", layout="flow",
         facts=dict(name="苏晴", phone="15905050506", city="上海", degree="本科", major="电子商务",
                    school="上海大学", work_years=3.0, expected="增长工程师",
                    skills=["Python", "MySQL", "Google Analytics", "SQL"],
                    jobs=[("魔都电商", "增长工程师", "2022.02", "至今", "埋点分析")],
                    projects=[], certs=[]),
         expect="passed", trap="go/google"),  # 有 python+mysql 且 3 年 → 后端岗 passed；'Google Analytics' 不得命中 go
    dict(filename="eval-15-杜鹏-trap-cpp.docx", layout="table",
         facts=dict(name="杜鹏", phone="16006060607", city="杭州", degree="本科", major="通信工程",
                    school="杭州电子科技大学", work_years=6.0, expected="嵌入式工程师",
                    skills=["C", "C++", "Linux", "TCP/IP"],
                    jobs=[("西子通信", "嵌入式工程师", "2019.04", "至今", "基站固件")],
                    projects=[], certs=[]),
         expect="rejected", trap="c/cpp"),  # 后端岗 must python+mysql → rejected
    dict(filename="eval-16-姜蕾-trap-nosql.docx", layout="flow",
         facts=dict(name="姜蕾", phone="16107070708", city="苏州", degree="本科", major="信息工程",
                    school="苏州大学", work_years=4.0, expected="数据库工程师",
                    skills=["NoSQL", "MongoDB", "Python"],
                    jobs=[("金鸡湖软件", "数据库工程师", "2021.08", "至今", "分布式存储")],
                    projects=[], certs=[]),
         expect="rejected", trap="sql/nosql"),  # NoSQL ≠ mysql/sql 技能；缺 MySQL → rejected
    dict(filename="eval-17-许飞-trap-react-native.docx", layout="flow",
         facts=dict(name="许飞", phone="16208080809", city="广州", degree="本科", major="软件工程",
                    school="华南师范大学", work_years=3.5, expected="移动端工程师",
                    skills=["React Native", "TypeScript", "React", "iOS", "Android"],
                    jobs=[("天河移动", "移动端工程师", "2022.03", "至今", "App 重构")],
                    projects=[], certs=[]),
         expect="rejected", trap="react/react-native"),  # 缺 python+mysql → rejected（react 不得穿透）
    # —— 前端岗正样本（前端岗 must javascript+react，min 大专 1 年）——
    dict(filename="eval-18-韩雪-frontend-pass.docx", layout="flow",
         facts=dict(name="韩雪", phone="16309090910", city="北京", degree="本科", major="数字媒体",
                    school="中国传媒大学", work_years=3.0, expected="资深前端工程师",
                    skills=["JavaScript", "React", "TypeScript", "Vite"],
                    jobs=[("望京互动", "前端工程师", "2022.01", "至今", "直播控制台")],
                    projects=[], certs=[]),
         expect="passed"),
    dict(filename="eval-19-曹阳-frontend-pass.docx", layout="two_col",
         facts=dict(name="曹阳", phone="16400000011", city="北京", degree="大专", major="计算机应用",
                    school="北京联合大学", work_years=2.0, expected="前端工程师",
                    skills=["JavaScript", "React", "Redux"],
                    jobs=[("码农集市", "前端工程师", "2023.06", "至今", "商家后台")],
                    projects=[], certs=[]),
         expect="passed"),  # 前端岗大专 1 年门槛 → 达标
    dict(filename="eval-20-潘玉-frontend-junior.docx", layout="flow",
         facts=dict(name="潘玉", phone="16501111112", city="北京", degree="本科", major="计算机科学",
                    school="北京化工大学", work_years=0.8, expected="初级前端工程师",
                    skills=["JavaScript", "React"],
                    jobs=[("清河科技", "初级前端", "2025.01", "至今", "组件库")],
                    projects=[], certs=[]),
         expect="rejected"),  # 前端岗 1 年门槛：0.8 < 1
    # —— Java 岗（must java）——
    dict(filename="eval-21-沈从-java-pass.docx", layout="table",
         facts=dict(name="沈从", phone="16602222213", city="上海", degree="本科", major="计算机科学",
                    school="同济大学", work_years=5.0, expected="Java 开发工程师",
                    skills=["Java", "Spring Boot", "MySQL", "Kafka"],
                    jobs=[("浦东信息港", "Java 工程师", "2020.05", "至今", "交易系统")],
                    projects=[], certs=[]),
         expect="passed"),
    dict(filename="eval-22-薛冰-backend-senior.docx", layout="flow",
         facts=dict(name="薛冰", phone="16703333314", city="成都", degree="硕士", major="计算机科学",
                    school="四川大学", work_years=8.0, expected="架构师",
                    skills=["Python", "MySQL", "FastAPI", "Milvus", "Kubernetes", "Docker"],
                    jobs=[("天府云", "架构师", "2020.01", "至今", "AI 平台"),
                          ("锦城信息", "后端工程师", "2016.07", "2019.12", "网关")],
                    projects=["大模型检索平台（2023，架构负责人）"], certs=["系统架构设计师"]),
         expect="passed"),
]

#: 匹配评测岗位（技能入库前归一化——与 job_profiles 存储口径一致）
JOBS = {
    "高级后端工程师": dict(
        title="高级后端工程师", jd_text="负责核心服务设计与开发", requirements_text="本科 3 年，Python 技术栈",
        min_degree="本科", min_work_years=3.0,
        must_skills=["python", "mysql"], bonus_skills=["milvus"],
        hard_rule_switches={"degree": True, "work_years": True, "must_skills": True},
        pass_threshold=75.0, pending_threshold=60.0,
        weights={"skills": 40, "experience": 30, "education": 15, "others": 15},
    ),
    "资深前端工程师": dict(
        title="资深前端工程师", jd_text="负责 Web 前端架构", requirements_text="大专 1 年，React 技术栈",
        min_degree="大专", min_work_years=1.0,
        must_skills=["javascript", "react"], bonus_skills=["typescript"],
        hard_rule_switches={"degree": True, "work_years": True, "must_skills": True},
        pass_threshold=75.0, pending_threshold=60.0,
        weights={"skills": 40, "experience": 30, "education": 15, "others": 15},
    ),
    "Java 开发工程师": dict(
        title="Java 开发工程师", jd_text="负责交易系统开发", requirements_text="本科 3 年，Java 技术栈",
        min_degree="本科", min_work_years=3.0,
        must_skills=["java"], bonus_skills=["kafka"],
        hard_rule_switches={"degree": True, "work_years": True, "must_skills": True},
        pass_threshold=75.0, pending_threshold=60.0,
        weights={"skills": 40, "experience": 30, "education": 15, "others": 15},
    ),
}

GEN_PROMPT = """你是简历撰写助手。严格按给定【事实清单】撰写一份中文简历正文，要求：
1. 事实清单中的姓名、手机号、城市、学历、专业、院校、技能名（含大小写与写法，如 Python 3.11 / JavaScript / C++）、公司名、职位名、起止年月、年数**一字不改**地出现；
2. 不得新增任何事实清单之外的公司、技能、证书或数字；
3. 输出为普通文本：第一行姓名，第二行联系方式；随后用「工作经历」「项目经历」「教育经历」「专业技能」等小节标题（可用「一、二、」或「■」开头）；
4. 篇幅 200-400 字，语言自然专业；
5. **必须单独写一行「工作年限：X 年」**（X 用事实清单中的数字，一字不改）；经历里的起止年月
   必须与事实清单完全一致（事实清单里的终点已是固定年月，不要改回「至今」）。

【事实清单】
{facts}

直接输出简历正文，不要输出任何解释。"""


def parse_ym(value: str) -> tuple[int, int]:
    """'2019.07' / '2019-07' / '2019' → (年, 月)。"""
    parts = [p for p in value.strip().replace("-", ".").replace("/", ".").split(".") if p]
    return int(parts[0]), (int(parts[1]) if len(parts) > 1 else 1)


def cutoff_for(facts: dict) -> str:
    """最早入职年月 + 标注年限 → 固定截止日 'YYYY.MM'（T-30 修复，见模块 docstring）。"""
    earliest = min(parse_ym(job[2]) for job in facts["jobs"])
    months = round(float(facts["work_years"]) * 12)
    index = earliest[0] * 12 + (earliest[1] - 1) + months
    year, month0 = divmod(index, 12)
    return f"{year}.{month0 + 1:02d}"


def _facts_text(facts: dict) -> str:
    cutoff = cutoff_for(facts)
    jobs = "；".join(
        f"{c} {t} {s}-{cutoff if e == '至今' else e}（{d}）"
        for c, t, s, e, d in facts["jobs"]
    )
    projects = "；".join(facts.get("projects") or []) or "无"
    certs = "、".join(facts.get("certs") or []) or "无"
    return (
        f"姓名：{facts['name']}；手机号：{facts['phone']}；城市：{facts['city']}；"
        f"最高学历：{facts['degree']}；专业：{facts['major']}；院校：{facts['school']}；"
        f"期望职位：{facts['expected']}；工作年限：{facts['work_years']} 年；"
        f"技能：{'、'.join(facts['skills'])}；工作经历：{jobs}；项目：{projects}；证书：{certs}"
    )


def _verify(text: str, facts: dict) -> list[str]:
    """生成内容必须包含全部关键事实（防 LLM 改写事实破坏标注）。"""
    missing = []
    for key in (facts["name"], facts["phone"], facts["school"], facts["major"], facts["city"]):
        if key not in text:
            missing.append(key)
    for skill in facts["skills"]:
        if skill.lower() not in text.lower():
            missing.append(f"技能 {skill}")
    for company, title, start, end, _ in facts["jobs"]:
        if company not in text or start[:4] not in text:
            missing.append(f"经历 {company}")
    # T-30：正文必须明示工作年限（LLM 曾漏写，导致只能靠相对日期推算）
    if f"工作年限：{facts['work_years']}" not in text:
        missing.append("工作年限行")
    return missing


def generate_resumes() -> dict[str, str]:
    """LLM 撰写正文 → 校验事实 → 返回 {filename: 正文}。失败重试一次，仍失败退化为模板直写。"""
    from app.core.llm_client import chat_text

    texts: dict[str, str] = {}
    for index, sheet in enumerate(FACT_SHEETS, 1):
        facts = sheet["facts"]
        content = None
        for attempt in (1, 2):
            try:
                text = chat_text(GEN_PROMPT.format(facts=_facts_text(facts)), max_tokens=1500)
                missing = _verify(text, facts)
                if not missing:
                    content = text
                    break
                print(f"  [{index}/{len(FACT_SHEETS)}] 第 {attempt} 次生成缺事实：{missing}，重试")
            except Exception as exc:  # noqa: BLE001
                print(f"  [{index}] LLM 调用失败（{type(exc).__name__}: {exc}），重试")
        if content is None:
            content = _fallback_text(facts)
            print(f"  [{index}] 退化为模板直写：{sheet['filename']}")
        # T-30 写盘前防呆：历史产物 eval-16 正文完全没有电话（_verify 之后仍丢失），
        # 导致该样本 phone 不可达、重试也拿不到分。重建时必须保证电话在正文里。
        if facts["phone"] not in content:
            lines = content.splitlines()
            lines.insert(1 if lines else 0, f"电话：{facts['phone']}")
            content = "\n".join(lines)
        texts[sheet["filename"]] = content
    return texts


def _fallback_text(facts: dict) -> str:
    lines = [facts["name"], f"电话：{facts['phone']}　城市：{facts['city']}", "求职意向：" + facts["expected"], "工作经历"]
    for company, title, start, end, desc in facts["jobs"]:
        lines.append(f"{company}　{title}　{start}-{end}　{desc}")
    lines.append("项目经历")
    lines.extend(facts.get("projects") or ["无"])
    lines.append("教育经历")
    lines.append(f"{facts['school']}　{facts['major']}　{facts['degree']}")
    lines.append("专业技能")
    lines.append("、".join(facts["skills"]))
    return "\n".join(lines)


def render_docx(filename: str, layout: str, content: str) -> Path:
    """正文 → DOCX；table/two_col 版式把经历类内容放进表格（版式多样化）。"""
    from docx import Document
    from docx.shared import Pt

    doc = Document()
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    title, rest = lines[0], lines[1:]

    doc.add_heading(title, level=1)
    section_keys = ("工作经历", "项目经历", "教育经历", "专业技能")

    def split_sections(items: list[str]) -> list[tuple[str, list[str]]]:
        sections: list[tuple[str, list[str]]] = []
        current: tuple[str, list[str]] | None = None
        for line in items:
            matched = next((k for k in section_keys if k in line and len(line) <= 12), None)
            if matched:
                current = (matched, [])
                sections.append(current)
            elif current is not None:
                current[1].append(line)
            else:
                sections.append(("概述", [line]))
                current = ("概述", [])
        return sections

    sections = split_sections(rest)
    if layout in ("table", "two_col"):
        contact = next((s for s in sections if s[0] == "概述"), None)
        if contact:
            doc.add_paragraph("\n".join(contact[1]))
        table = doc.add_table(rows=0, cols=2 if layout == "two_col" else 1)
        table.style = "Table Grid"
        for name, body in sections:
            if name == "概述":
                continue
            row = table.add_row().cells
            row[0].text = f"【{name}】\n" + "\n".join(body)
            if layout == "two_col" and len(row) > 1:
                row[1].text = " "
        tail = [s for s in sections if s[0] == "概述" and sections.index(s) != 0]
        for _, body in tail:
            for line in body:
                doc.add_paragraph(line)
    else:
        for name, body in sections:
            doc.add_heading(name, level=2)
            for line in body:
                doc.add_paragraph(line)
    for paragraph in doc.paragraphs:
        for run in paragraph.runs:
            run.font.size = Pt(10.5)

    out = RESUME_DIR / filename
    doc.save(out)
    return out


def build_annotations() -> tuple[list[dict], list[dict]]:
    parse_annotations = []
    match_annotations = []
    for sheet in FACT_SHEETS:
        facts = sheet["facts"]
        parse_annotations.append(
            {
                "filename": sheet["filename"],
                "name": facts["name"],
                "phone": facts["phone"],
                "degree": facts["degree"],  # 归一化字面值（D-G）
                "major": facts["major"],
                "school": facts["school"],
                "work_years": facts["work_years"],
                "skills": sorted({s.lower() for s in facts["skills"]}),  # 归一化前的基础形态
                "layout": sheet["layout"],
            }
        )
        for job_title, job in JOBS.items():
            # 期望结论由事实 + 规则推得（与 match_engine.hard_gate 同口径）：
            # 硬门槛：学历排序值 / 年限 / 必备技能（逐元素命中，归一化后）
            from app.core.normalize import degree_rank, normalize_skill, skill_hit

            reasons = []
            if job["hard_rule_switches"].get("degree") and job.get("min_degree"):
                if degree_rank(facts["degree"]) < degree_rank(job["min_degree"]):
                    reasons.append("学历")
            if job["hard_rule_switches"].get("work_years") and job.get("min_work_years") is not None:
                if facts["work_years"] + 1e-9 < float(job["min_work_years"]):
                    reasons.append("年限")
            if job["hard_rule_switches"].get("must_skills"):
                for skill in job["must_skills"]:
                    if any_skill_hit_local(skill, facts["skills"]):
                        continue
                    reasons.append(f"缺失{normalize_skill(skill)}")
            verdict = "rejected" if reasons else "passed"
            match_annotations.append(
                {
                    "filename": sheet["filename"],
                    "job_title": job_title,
                    "expected_verdict": verdict,
                    "expect_reason_contains": reasons,  # 硬淘汰时理由必须含这些关键词
                    "trap": sheet.get("trap"),
                }
            )
    return parse_annotations, match_annotations


def any_skill_hit_local(job_skill: str, candidate_skills: list[str]) -> bool:
    from app.core.normalize import skill_hit

    return any(skill_hit(job_skill, element) for element in candidate_skills)


def main() -> None:
    RESUME_DIR.mkdir(parents=True, exist_ok=True)
    # 幂等：清空旧评测集
    for old in RESUME_DIR.glob("*.docx"):
        old.unlink()

    print("1/3 LLM 撰写简历正文（22 份，spark temperature=0）…")
    texts = generate_resumes()

    print("2/3 渲染 DOCX（flow/table/two_col 三种版式）…")
    for sheet in FACT_SHEETS:
        render_docx(sheet["filename"], sheet["layout"], texts[sheet["filename"]])
    print(f"   {len(FACT_SHEETS)} 份 → {RESUME_DIR}")

    print("3/3 生成标注 JSON …")
    parse_annotations, match_annotations = build_annotations()
    (EVAL_DIR / "resume_parse_eval.json").write_text(
        json.dumps(parse_annotations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (EVAL_DIR / "match_eval.json").write_text(
        json.dumps(match_annotations, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    traps = sum(1 for a in match_annotations if a.get("trap"))
    print(f"完成：{len(parse_annotations)} 解析标注 / {len(match_annotations)} 匹配标注（含 {traps} 条子串陷阱）")


if __name__ == "__main__":
    main()
