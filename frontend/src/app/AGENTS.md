# frontend/src/app：Next.js 路由与页面

本目录按 `.collaboration/ownership.json` 分配实际文件：A 维护公共布局、登录、岗位、匹配及 screen/page.tsx 组装；B 维护人才页面和上传/解析队列组件；C/D 维护各自登记的新模块页面。screen/ 为混合归属，不能按目录取得全部写权限。开始前读取根 AGENTS.md、更新记录、本人任务及四人最新交接。

现有 Next.js 页面继续复用；新增模块按统一需求与四人分工接入，不再创建 Vite 入口。公共布局、依赖和路由组装请求 A 处理；跨角色组件/类型变更由维护人实现，不能顺手代改。

组件测试随本文件归属，集成测试放 tests/<本人>/，交接新增到 docs/updates/<本人>/。本规则仅解释根机器归属，不扩张权限；AGENTS.md 仍由 A 维护。
