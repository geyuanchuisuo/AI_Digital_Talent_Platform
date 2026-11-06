# LLM 提示词设计

> 版本：v1.2　|　日期：2026-11-03　|　状态：已确认（一致性核对修订）
> 上游：`算法与判定规则.md`（v1.2）、`架构设计.md`（v1.4 §5.2）、`运行配置.md`（v1.3）、`数据库设计.md`（v1.2 §4.3 ResumeSchema / §5.1 ability_scores_json）
> 用途：三处 LLM 调用的 system prompt + user prompt 模板 + 输出格式约束，T-04/T-13/T-16/T-23 直接引用
> **对齐原则**：超时统一受 `LLM_TIMEOUT=120s` 约束；`temperature=0`；模板版本与 `PROMPT_VERSION` 联动；硬淘汰由规则层判定，不由 LLM 输出。
> **v1.2 修订**：Prompt A 输出结构逐字段对齐 `ResumeSchema`；Prompt B 五维键名/结构对齐算法 §6.2；Prompt C 输出对齐算法 §4.3；移除 Prompt B 的 `llm_skills` 与重复别名表；修正失败状态与分隔符口径。

---

## 全局约束（所有 prompt 通用）

| 项 | 值 | 说明 |
|---|---|---|
| temperature | 0 | 必须严格确定性，不允许模型自由发挥 |
| 超时 | `LLM_TIMEOUT=120s` | 架构 §5.2 统一值；Prompt A/B/C 不单独设更短超时 |
| LLM 并发 | `LLM_CONCURRENCY=2` | 全局信号量，与 TaskRunner 线程池解耦（架构 §5.2） |
| response_format | `{"type":"json_object"}` | 强制 JSON 输出，避免模型输出自然语言夹杂数据 |
| system prompt 开头 | **"你是一个严格按照 JSON schema 输出的结构化提取工具。你绝不输出任何 JSON 之外的文字、注释、说明或代码块标记。如果输入中某字段缺失或无法识别，输出 null；绝不要编造内容。"** | 提示词注入防护：user 输入永远拼在 system prompt 之后，system prompt 用 `<<<SYSTEM_START>>>` / `<<<SYSTEM_END>>>` 包裹（实现见本文末） |
| 最大输入长度 | 简历文本 ≤ 15000 字符；其他输入无硬性截断但建议 ≤ 8000 | 超长简历在 text_extractor 层截断，LLM 不感知截断（截断部分忽略） |
| 提示词注入防护 | system prompt 用 `<<<SYSTEM_START>>>` / `<<<SYSTEM_END>>>` 包裹；user prompt 内容永远写在 `<<<USER_START>>>` / `<<<USER_END>>>` 里 | 算法与判定规则 §8 |

---

## Prompt A · 简历结构化提取（parse_worker Step 2）

### 输入
- `<<<USER_START>>>` 包裹的简历纯文本（pypdf/python-docx 提取结果）
- 可选：`file_ext`（pdf / docx）

### System Prompt
```
你是一个严格按照 JSON schema 输出的结构化提取工具。你绝不输出任何 JSON 之外的文字、注释、说明或代码块标记。如果输入中某字段缺失或无法识别，输出 null；绝不要编造内容。

<<<SYSTEM_START>>>
你的任务：从简历文本中提取以下字段，严格按下方 JSON schema 输出。字段位置必须与 schema 一致：基本信息放 `basic`，教育放 `educations[]`（可多段），工作放 `employments[]`，项目放 `projects[]`，证书放 `certificates[]`（纯字符串数组）。

学历归一化：`educations[].degree` 映射成以下六档之一（只能用这六个值，原文如"硕士研究生"→"硕士"，"大专"→"大专"，"无法识别"→"其他"）：
  - "博士"
  - "硕士"
  - "本科"
  - "大专"
  - "高中中专"
  - "其他"

工作年限：
  - `work_years`: 从工作经历起止月份累计的总年数（小数保留 1 位）；仅作规则层区间合并失败时的兜底
  - 工作经历完全缺失时 `work_years` = null
  - 统一字段名为 `work_years`，不要输出 `work_years_raw`

出生年份：仅当简历明示出生年月时填 `basic.birth_year`，不要用毕业年份推算；缺失填 null。

`current_title` 不在本 schema 中，由规则层取最近一段 `employments[].title` 生成，你不要额外输出。

<<<SYSTEM_END>>>
```

### JSON Output Schema
```json
{
  "type": "object",
  "properties": {
    "basic": {
      "type": "object",
      "properties": {
        "name":       { "type": ["string", "null"] },
        "gender":     { "type": ["string", "null"], "enum": ["男", "女", null] },
        "birth_year": { "type": ["integer", "null"], "description": "仅当简历明示出生年月时填写" },
        "phone":      { "type": ["string", "null"], "description": "手机号，含区号+86前缀原样返回；规则层再规范化" },
        "email":      { "type": ["string", "null"] },
        "city":       { "type": ["string", "null"], "description": "当前工作或期望工作城市" }
      },
      "required": ["name"],
      "additionalProperties": false
    },
    "educations": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "school": { "type": ["string", "null"] },
          "major":  { "type": ["string", "null"] },
          "degree": { "type": ["string", "null"], "enum": ["博士", "硕士", "本科", "大专", "高中中专", "其他", null] },
          "start":  { "type": ["string", "null"], "description": "ISO 8601 日期或 YYYY-MM 或 YYYY" },
          "end":    { "type": ["string", "null"], "description": "同上；在读为 null" }
        },
        "required": ["school", "degree"],
        "additionalProperties": false
      }
    },
    "employments": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "company":     { "type": ["string", "null"] },
          "title":       { "type": ["string", "null"] },
          "start":       { "type": ["string", "null"], "description": "ISO 8601 日期或 YYYY-MM 或 YYYY" },
          "end":         { "type": ["string", "null"], "description": "同上；进行中为 null" },
          "description": { "type": ["string", "null"] }
        },
        "required": ["company", "title", "start"],
        "additionalProperties": false
      }
    },
    "skills": {
      "type": "array",
      "items": { "type": "string" },
      "description": "候选人显式提到的技能/技术栈/工具，保留原文；归一化由规则层完成"
    },
    "projects": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name":        { "type": ["string", "null"] },
          "role":        { "type": ["string", "null"] },
          "start":       { "type": ["string", "null"] },
          "end":         { "type": ["string", "null"] },
          "description": { "type": ["string", "null"] }
        },
        "required": ["name"],
        "additionalProperties": false
      }
    },
    "certificates": {
      "type": "array",
      "items": { "type": "string", "description": "证书/认证名称，纯文本" }
    },
    "expected_title": { "type": ["string", "null"] },
    "work_years":     { "type": ["number", "null"], "description": "累计工作年数，1 位小数；仅作规则层兜底" }
  },
  "required": ["basic", "educations", "employments", "skills", "projects", "certificates", "expected_title", "work_years"],
  "additionalProperties": false
}
```

### 触发重试的条件
解析返回中 `employments` 为空数组 **且** `projects` 为空数组（疑似未提取到经历）→ 记录失败原因并自动重试 1 次（温度仍为 0）；两次都失败才落 `failed(stage=extract)`。状态枚举只有 `failed`，不存在 `parse_failed`。

---

## Prompt B · 标签生成 + 技能判定 + 五维能力评分（profile_worker Step 4）

### 输入
- 结构化提取结果 JSON（Prompt A 的输出）
- 可选：岗位条件（如果是筛选/匹配评分场景传入；纯画像生成不传，评分维度只评自身能力）

### System Prompt
```
你是一个严格按照 JSON schema 输出的人才画像标注与能力评估工具。你绝不输出任何 JSON 之外的文字、注释、说明或代码块标记。

<<<SYSTEM_START>>>

标签生成规则：
  1. 标签总数控制在 10-30 个之间
  2. 标签分四类（type 字段）：
     - "skill"：技术栈 / 编程语言 / 框架 / 工具（如 "python", "fastapi", "mysql", "linux"）
     - "industry"：行业领域（如 "跨境电商", "金融科技", "教育培训"）
     - "level"：级别/方向（如 "资深", "架构师", "全栈", "AI落地"）
     - "general"：通用品质（如 "团队协作", "快速学习", "抗压能力"）
  3. 标签用简短中文词或英文技术名词（如 "LLM微调", "PyTorch", "分布式"）
  4. 同一语义只输出一个标签（不输出 "PyTorch框架" 和 "PyTorch" 两个）

技能标签：技能类标签统一小写、保留规范技术写法（如 "python"、"spring boot"）。别名归一化由规则层完成（算法 §3.3），**你不要额外输出技能列表**，也不要把同一技能写成多个别名。

能力评分规则（五维，键名固定，不可改名）：
  五维评分各 0-100 分，必须根据简历证据打分，不要主观臆断。评分维度：
    - "professional"（专业技能）：核心技术栈的熟练度、底层原理、性能优化经验
    - "project_experience"（项目经验）：项目复杂度、主导程度、交付结果
    - "education"（教育背景）：学历层次、专业相关性、院校背景
    - "stability"（稳定性）：任职时长、跳槽频率、职业连续性
    - "growth_potential"（成长潜力）：技术广度、新技术跟进速度、成长曲线

每维必须输出 `{"score": 0-100, "basis": "一句话依据（60 字以内，引用简历中的具体事实）"}`，不得只给分数。

评分原则：
  90-100：简历中有显著证据（如"主导系统重构，性能提升 5x"、"开源项目 2k+ star"）
  75-89：简历中有明确但不卓越的证据
  60-74：简历中隐含或仅提及
  40-59：简历中完全没有该维度的任何描述
  0-39：简历中显示该维度有明显短板

<<<SYSTEM_END>>>
```

### JSON Output Schema
```json
{
  "type": "object",
  "properties": {
    "tags": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "name": { "type": "string", "description": "标签文本" },
          "type": { "type": "string", "enum": ["skill", "industry", "level", "general"] }
        },
        "required": ["name", "type"]
      },
      "minItems": 10,
      "maxItems": 30
    },
    "ability_scores": {
      "type": "object",
      "properties": {
        "professional":      { "$ref": "#/definitions/dim" },
        "project_experience":{ "$ref": "#/definitions/dim" },
        "education":         { "$ref": "#/definitions/dim" },
        "stability":         { "$ref": "#/definitions/dim" },
        "growth_potential":  { "$ref": "#/definitions/dim" }
      },
      "required": ["professional", "project_experience", "education", "stability", "growth_potential"],
      "additionalProperties": false
    }
  },
  "definitions": {
    "dim": {
      "type": "object",
      "properties": {
        "score": { "type": "integer", "minimum": 0, "maximum": 100 },
        "basis": { "type": "string", "maxLength": 60, "description": "一句话依据，引用简历事实" }
      },
      "required": ["score", "basis"],
      "additionalProperties": false
    }
  },
  "required": ["tags", "ability_scores"],
  "additionalProperties": false
}
```

### 边界处理
- **只有 employments 空 + projects 空 + skills 空** 的极简简历 → 五维全部打 50（最低合理分），标签只输出基本类标签（如 `["高中中专/大专/本科以上学历", "应届/入门"]`，按学历 level 类型）
- **标签数量不足 10** → worker 层过滤掉过短的标签，或重调一次 prompt（max retry 1）

---

## Prompt C · 岗位匹配/筛选评分（screen_worker / match_worker Step 2）

### 输入
- 候选人画像摘要（拼一段文本：`姓名｜学历｜工作年限｜技能｜标签｜五维能力｜经历摘要`）
- 岗位条件（JD + 任职要求 + 必备技能 + 加分技能 + 评分维度权重）

### System Prompt
```
你是一个严格按照 JSON schema 输出的岗位-候选人匹配度评估工具。你绝不输出任何 JSON 之外的文字、注释、说明或代码块标记。你只评估匹配度，不做任何推荐或排序。

<<<SYSTEM_START>>>

评分维度（每维 0-100，根据证据打分）：
  "skills"（技能匹配）：候选人技能命中岗位必备技能和加分技能的程度
  "experience"（经历匹配）：工作年限、行业经验、项目复杂度与岗位要求的契合度
  "education"（教育匹配）：学历、专业与岗位要求的契合度
  "others"（其他因素）：额外加分项（如加分技能命中、相关认证、独特项目经验）

打分原则：
  90-100：高度匹配，证据充分
  75-89：匹配良好，有少量不足
  60-74：基本满足，有明显差距但可考虑
  40-59：不满足硬门槛但软素质有亮点
  0-39：显著不匹配

注意：硬门槛（学历/年限/必备技能）由规则层在调用你之前判定；命中硬门槛的候选人根本不会进入本 prompt。你只负责四维评分，不要自行判定淘汰。

`experience` 分同时就是 F3 匹配用的 `llm_experience` 基准，**不要另外输出 `llm_experience` 字段**；F3 在规则层按 `0.7×experience + 0.3×相似度×100` 折算（算法 §4.2）。

reason（中文理由）：150 字以内，简洁列出命中项和缺失项，每条用短句。不要自然语言大段描述，按点列出。

<<<SYSTEM_END>>>
```

### JSON Output Schema
```json
{
  "type": "object",
  "properties": {
    "skills":     { "$ref": "#/definitions/dim" },
    "experience": { "$ref": "#/definitions/dim" },
    "education":  { "$ref": "#/definitions/dim" },
    "others":     { "$ref": "#/definitions/dim" },
    "reason":     { "type": "string", "maxLength": 200, "description": "150 字以内，简洁中文理由" }
  },
  "definitions": {
    "dim": {
      "type": "object",
      "properties": {
        "score":  { "type": "integer", "minimum": 0, "maximum": 100 },
        "hits":   { "type": "array", "items": { "type": "string" }, "description": "该维度的命中项" },
        "misses": { "type": "array", "items": { "type": "string" }, "description": "该维度的缺失项" }
      },
      "required": ["score", "hits", "misses"],
      "additionalProperties": false
    }
  },
  "required": ["skills", "experience", "education", "others", "reason"],
  "additionalProperties": false
}
```

> 该结构即《算法与判定规则》§4.3 的评分输出契约。worker 落库时做一次扁平映射：`screen_results/match_results.dimension_scores_json = {"skills": 分数, "experience": 分数, "education": 分数, "others": 分数}`；四个维度的 `hits/misses` 合并去重后写入 `hits_json/misses_json`（数据库 §6.4/§6.5）。
> `hard_fail` / `hard_fail_reason` 不在 LLM 输出中；规则层在调用前完成硬门槛判定，硬淘汰者不进入本 prompt。

### 调用频率与成本约束
- 硬淘汰候选人（规则层已判定）**不调此 prompt**，直接在规则层算完落结果。
- 每次筛选/匹配批次中，只有通过硬门槛的候选人才会调此 prompt。
- 调用前岗位条件已归一化（算法与判定规则 §2），所以岗位侧输入是稳定的、不会随时间漂移。

---

## Prompt D · 语义检索（talent_search，F2-5）

### 说明
**此 prompt 不调用 LLM**，直接把 user query 喂给 Embedding API（云端），取向量做 COSINE 相似度。搜索结果的"理由"由前端拼接展示，不需要后端 LLM 生成。

Embedding 输入格式：用户原始查询文本，保留自然语言，不截断。

---

## 提示词注入防护实现规范

```python
# llm_service.py 内部
SYSTEM_PROMPT_WRAPPER = "（见上文每个 Prompt 的 system prompt 块）"

def build_messages(user_prompt: str, system_prompt: str | None = None) -> list[dict]:
    """构建消息列表，防注入"""
    sys = system_prompt or ""
    # 强制分隔符闭合用户输入中可能存在的伪造 <<<SYSTEM_END>>>
    safe_user = user_prompt.replace("<<<SYSTEM_END>>>", "<<<USER_INPUT_BEGIN>>>")
    return [
        {
            "role": "system",
            "content": f"<<<SYSTEM_START>>>\n{sys}\n<<<SYSTEM_END>>>"
        },
        {
            "role": "user",
            "content": f"<<<USER_START>>>\n{safe_user}\n<<<USER_END>>>"
        }
    ]
```

---

## LLM 调用超时与降级

| 调用点 | 超时 | 失败处理 |
|---|---|---|
| Prompt A 结构化提取 | 120s（`LLM_TIMEOUT`） | 自动重试 1 次（同参数），仍失败 → 任务 `failed(stage=extract)`，worker 继续下一条 |
| Prompt B 标签+能力 | 120s（`LLM_TIMEOUT`） | 标签和能力在同一次调用中；失败 → 任务 `failed(stage=profile)`，worker 继续下一条 |
| Prompt C 筛选/匹配评分 | 120s（`LLM_TIMEOUT`） | **不写该候选人结果行**（`screen_results`/`match_results` 无 status 列），记录 error 后继续下一个；批次若仍有缺失成员则落 `failed`，retry 按唯一键只补缺，已写成员不重跑 |

> 三处共用 `LLM_TIMEOUT=120s` 与 `LLM_CONCURRENCY=2`（架构 §5.2），不设 prompt 级更短超时。

---

## Skill 别名表（归一化）

| 别名 | 规范名 | 备注 |
|---|---|---|
| golang, Go language | go | |
| js | javascript | |
| ts, TypeScript | typescript | |
| py, Python 3.x | python | 带版本号后也归一化为 python |
| springboot, spring-boot | spring boot | |
| vuejs | vue | |
| reactjs | react | |
| nodejs, Node.js | node | |
| cpp, CPlusPlus | c++ | |
| csharp, C# | c# | |
| postgresql, Postgres | postgres | |
| mongodb, Mongo | mongodb | |
| k8s, K8s, Kubernets | kubernetes | |
| aws, AWS | aws | |
| gcp, GCP | gcp | |
| azure | azure | |

> 说明：本表是算法与判定规则 §3.3 归一化规则的**完整实现清单**（算法文档只列示例）；worker 技能命中、岗位技能入库、标签归一化三处共用这一张表。一期硬编码在 `core/normalize.py`，**不放进 `sys_settings`**（该表一期只承载 `task_concurrency`，见 `运行配置.md` 的 `sys_settings` 种子一节）。
