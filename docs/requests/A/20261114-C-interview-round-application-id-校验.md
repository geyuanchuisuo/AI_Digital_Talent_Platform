# 协作请求：C 在 interview_rounds 落库前校验 application_id 引用完整性

- 请求 ID：REQ-A-20261114-220500-C-interview-application-id-校验
- 请求人/目标维护角色/日期/本人分支：A → **C** / 2026-11-14 22:05 / feature/A/continuation（master=748f4e6）
- 优先级：**非阻断**——面试轮次创建主链路不受影响；缺口是跨域引用可指向不存在/他人的申请，影响 T-A23「同一候选人的筛选→邀约→面试引用可连续查询」验收的引用完整性。
- 期望响应检查点：2026-11-16 前 C 受理；交付检查点由 C 受理记录给出（A 不指定）。
- 状态：**已关闭**（2026-11-15 消费方验证通过，UPD-A-20261115-0730）
  - 提供方交付：C `40dc84e`（service.validate_application_ref + ai_router 同口径 + 10 例测试）+ MASTER 受理登记 `361261e`；C 测试 10 passed（backend 全量 975 collected exit 0，C 交付记录）。
  - 消费方验证：A 复跑定向 223 passed（含 C 新 10 例）+ collaboration 33 OK；**跨域连续查询实测 PASS**（A 申请 548 × C 轮次 5，application_id 互认、按 talent_profile_id=398 两域可查）。
  - 关闭依据：提供方交付 + 消费方验证齐备（本请求模板条款）。
- 对应 Fxx 与相关更新：V2.6 F02（人工面试）；A 契约 [招聘申请契约](../../modules/A/招聘申请契约.md) §2/§4；T-A23 核对（UPD-A-20261114-2210，创建后追加链接）。
- 依据的需求/契约版本、关联决策 ID：V2.6 冻结版；A 契约「招聘申请契约」v0.1（§1 无物理外键，一致性由 service 层保证——仓库根 AGENTS.md §1 约定）。
- 目标路径与所属角色：`backend/app/modules/interviews/service.py`（C 域）。
- 需要的输入、输出、字段与示例（无秘密）：
  - 现状（2026-11-14 master 实读）：`create_round` 对 `payload.application_id` **直接落库**，无存在性校验；`ai_router.py` 同样透传。
  - 期望校验（与 A 侧 applications 的收窄口径一致）：
    1. `application_id` 非空时：`applications` 中存在该 id，否则 400「application_id 不存在」；
    2. 归属一致：`applications.talent_profile_id == round.talent_profile_id`（两者都非空时），不一致 400（防止把面试挂到别人的申请上）；
    3. 建议范围：`create_round` 与轮次更新（若允许改 application_id）同口径；`ai_router` 的自动安排路径同样校验。
- 兼容性、迁移和消费者影响：无表结构变化；历史行若存在悬空 application_id，建议校验只对新写入生效，历史行按只读核对登记（不要求迁移修数）。
- 验收方式、阻塞范围及可继续的独立工作：C 侧负例测试（不存在 id → 400；归属不一致 → 400）+ 正例；A 侧 T-A23 联调验证（连续查询）在 C 交付后并入既有挂起项「与 C 联调面试引用」。A 侧不阻塞，可继续独立工作（T-A51/52 收口）。
- 请求方不得修改的对方文件：`backend/app/modules/interviews/**`、`tests/C/**` 及 C 域全部文件。
- 受理与回复：维护人在 `docs/updates/C/` 新增受理记录，注明受理/需补充/不适用、预计交付检查点及契约版本，链接回本请求。
- 提供方交付证据：提交/契约版本、实际验证、未测项。
- 消费方验证与关闭依据：A 在 C 交付版本上复跑联调核对（连续查询链路）后关闭；不适用则注明依据。
- 超期/阻塞处理：超期由 A 提请用户/MASTER 协调；期间 A 侧按挂起项继续独立工作。
