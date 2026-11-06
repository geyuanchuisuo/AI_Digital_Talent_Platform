# A 工作包：身份与公共基础、岗位招聘、匹配复核

维护角色：A；姓名由组内绑定。日期：2026-11-06。状态：招聘子系统源码已导入，待本机联调；目标功能不等于已完成。

先读根 AGENTS.md、README、更新记录、四个角色最新交接、目标路径规则及[统一需求 V2.2](../docs/需求文档.md)。接入事实见[A 契约基线](../docs/modules/A/源码接入基线.md)、[接入决策](../docs/源码接入与需求决策.md)和[来源参考区](../docs/imports/hhy_gycs_master/README.md)；全组安排见[四人分工](../docs/四人分工与试行方案.md)。README 运行入口尚未实测，不按旧进度认定完成。

## 继承的现成文件

后端继承 api/deps.py、api/auth.py、认证相应 dao/schemas/service、models/user.py/job.py，job_profiles/screen/match 对应 api/dao/schemas/service 及 tasks/screen_worker.py、match_worker.py。公共 main.py、配置、数据库连接、模型聚合与迁移链由 A 集成。

前端继承 Next.js App Router 布局及路由骨架、login、job-profiles、match 页面，components 公共组件及 lib/http.ts、auth.ts、types.ts。screen/ScreenBatchPanel 及对应测试归 A，screen/page.tsx 由 A 组装；UploadPanel/QueuePanel、api/types/utils 归 B。core/prompts/match.txt 的业务模板归 A，loader/schemas 框架归 D。usePolling 及其测试归 D，不按 lib 父目录整包处理。

以上只说明主要继承点，不是完整写入清单；唯一机器来源是[ownership.json](../.collaboration/ownership.json)，导入路径已按工作包登记。具体源码相对 backend/app 或 frontend/src，修改前按机器表和沿途规则核对，不扩大其他角色权限。

## 剩余任务

1. 核对当前 HR 登录与个人端缺口，补账号与 person_id 绑定、本人数据授权及双端入口；复核依赖、初始化和迁移基线，沿用 Next.js 14/React 18/antd 5 与 FastAPI/SQLAlchemy/MySQL。
2. 补人才需求、AI JD 草稿、招聘状态及邀约记录。人工面试模块由 C 维护，A 保存招聘引用并对齐安排/反馈，避免重复建模。
3. 对照 AI 综合评分需求处理来源硬门槛、加权与向量混合评分差异；正式 Worker 当前未带完整经历/项目证据，需接 B 的带版本档案补齐输入，不能拿较丰富的历史评测输入替代正式链路验收。
4. 补岗位版本、分数线、批次人员与输入快照、模型/Prompt 版本及失败记录；自动初筛与人工改判分别保存，自动结果不等于录用或终结申请。年龄/性别不参与本版评分。
5. 保持现有 code/message/data、request_id 与 X-Request-ID 兼容，记录目标契约差异，不全仓重写响应；接入其他角色页面、任务和路由，不重写其业务。

## 跨包依赖与交付接口

B 提供 talent_profile_id 到 person_id 的稳定映射、档案版本、完整输入和证据；A 不新建人才主档。D 提供 LLM、任务状态与健康契约，保留来源 Spark/独立 Embedding 与 Agnes 待验证的区别。C 提供人工面试安排与结果引用。向 D 提供岗位需求、人工推进、缺口和匹配/复核指标的时间、状态、去重与允许维度。

公共依赖/入口由 A 集成，模型/任务公共框架变更请求 D；跨包请求写本人 docs/requests/A/，契约写 docs/modules/A/。公共整合职责不授予 B/C/D 业务目录权限。

## 验收与检查点

管理员能创建并修改岗位、真实生成 JD、发起匹配、按分数线初筛并人工改判；刷新及重启后结果和历史输入可追溯。验证等于分数线、字段缺失、评分失败、重复请求、批次快照与个人端越权；与 C 联调人工面试引用，向 D 提供可核对指标。

3–4 天为首轮检查窗口：先核对入口、身份与数据库，再复核招聘链路和失败边界，据实际探针重新评估剩余工期，不保证全范围四天完成。记录实际浏览器是否验证；外部运行探针未执行，来源 588 项测试不能称本次通过。

后续代码工作在本人 clone/个人分支和既定身份下进行，按机器范围检查并新增 docs/updates/A/ 交接；不自动提交、push 或集成 master。本轮完成源码导入与协作基线调整；业务代码原样保留，外部运行探针未执行；不授予业务角色。
