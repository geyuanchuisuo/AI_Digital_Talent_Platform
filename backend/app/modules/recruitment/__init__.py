"""招聘域模块（M3，V2.6 冻结口径）：人才需求/项目、JD 投放、岗位申请与五阶段状态机。

归属：A。模型注册经 `app/models/__init__.py` 直接 import 本包 models.py；
路由由 main.py import 本包 router.py 注册（不建模型+路由捆绑 registry，避免加载环）。
"""
