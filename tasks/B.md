# B 工作包：文件/OCR、人才主档、知识与档案问答

维护角色：B；姓名由组内绑定。日期：2026-11-06。状态：上传、解析、人才档案及语义检索源码已导入，OCR 与问答待续建，本机未联调。

先读根 AGENTS.md、README、更新记录、四个角色最新交接、目标路径规则及[统一需求 V2.2](../docs/需求文档.md)。接入事实见[B 契约基线](../docs/modules/B/源码接入基线.md)、[接入决策](../docs/源码接入与需求决策.md)和[来源参考区](../docs/imports/hhy_gycs_master/README.md)；全组安排见[四人分工](../docs/四人分工与试行方案.md)。README 旧进度和来源测试不是当前验收证据。

## 继承的现成文件

后端继承 resumes/talents 相关 api、dao、schemas、models、service，tasks/parse_worker.py、core/normalize.py、profile_text.py 和文件工具；沿用上传、队列、提取、解析、画像与档案服务。core/prompts/parse.txt、profile.txt 业务模板归 B，公共 loader/schemas 框架归 D。

前端继承 app/(main)/talents 页面和 screen/UploadPanel、QueuePanel、api/types/utils 及对应测试。screen/ScreenBatchPanel 与 page.tsx 由 A 维护，不修改整页组装或筛选组件。已有 files/talents/knowledge 新模块空目录保留，按需要增量接入，不复制现成主档。

以上是主要继承点，唯一机器来源是[ownership.json](../.collaboration/ownership.json)，导入路径已登记；后端位置相对 backend/app，前端相对 frontend/src。具体写入范围及测试归属按机器表和沿途规则核对，不复制完整清单。

## 剩余任务

1. 来源只有文本 PDF/DOCX 提取，补图片、扫描/混合 PDF OCR、逐页质量与原文证据，保留缺失字段和待核实项；损坏、加密、无有效文字明确提示。
2. 复核批量上传计数、失败隔离、去重、取消和失败阶段重试；解析成功而向量/匹配失败分别记录，重试不重复建档。文件去重不等于同人合并，不按姓名自动合档。
3. 沿用 TalentProfile 与 talent_profile_id，先明确 person_id 映射，补档案版本、完整来源/页码/片段、人工修改前后记录和确认值保护，不新建第二套主档。现有截断 raw_text 与 structured_json 不等于完整证据快照；画像雷达不当作真实测评结果。
4. 补制度知识处理、权限过滤、引用、删除/重建索引一致性及单人/批量档案问答。现有语义搜索仅返回人员，不等于已实现带引用问答。复用现有 Milvus，不主动移除或另造向量框架。
5. 补原文件与档案的个人端范围鉴权，提供带版本人员快照和证据服务；接口需登记后实现，不把目标函数名写成现成能力。

## 跨包依赖与交付接口

A 提供身份、公共下载授权接入、路由/依赖和 screen 组装；B 提交 OCR 依赖及资源用途，由 A 集成。D 提供真实 LLM、独立 Embedding、Milvus 与 TaskRunner，B 维护业务提示、索引权限及生命周期，不复制客户端。C 使用 B 的 person_id 映射与档案版本，不能自行建主档。

向 A/C/D 提供人员快照、证据与文件授权契约；向 D 提供解析/档案指标的时间、失败/未完成状态、去重与授权口径。精确人数使用全量授权数据核对，不拿 top-k 片段当全量。契约写 docs/modules/B/，跨包变更写 docs/requests/B/。

## 验收与检查点

文本 PDF、DOCX、图片、扫描 PDF 和损坏/重复反例组成混合批次；合法项生成可核对档案，单份失败不影响其他项，失败阶段重试不重复产物。人工修订、档案版本和历史引用持久化；制度及单/批档案问答引用真实来源，不混淆人员，覆盖不全明确提示。个人端无法下载他人原件、查询他人档案或受限制度。

3–4 天为首轮检查窗口：先用四类文件/OCR 与引用探针核对能力，再联调招聘和问答，不保证全范围四天完成。Agnes、Embedding、Milvus、OCR 和浏览器流程各自记录真实结果；外部运行探针未执行，历史 588 项测试不是本次通过。

后续按本人 clone/分支、机器范围及规则实施，代码变更新增 docs/updates/B/ 交接，不改来源或他人记录。本轮完成源码导入与协作基线调整；业务代码原样保留，外部运行探针未执行；不授予业务角色。
