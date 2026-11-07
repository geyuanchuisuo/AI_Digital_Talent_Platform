"""C 模块路由接入清单：A 只需引用本文件即可注册 C 的三域路由。

用法（由 A 在 `backend/app/main.py` 中执行）：

```python
from app.modules.assessments.registry import ALL_ROUTERS as C_ROUTERS
for router in C_ROUTERS:
    app.include_router(router)
```

模型清单已拆到 `app/modules/assessments/registry_models.py`（只依赖模型层）：
`router` 会经 `app.api.deps` 间接导入 `app.models`，若模型清单与路由同文件，
「先导入模型清单」的入口会触发循环导入。建表/结构断言请只导入 registry_models。

不要在 C 的 `models.py` 中反向导入公共注册表，避免与 A 的初始化顺序耦合。
"""

from __future__ import annotations

from fastapi import APIRouter

from app.modules.assessments.registry_models import ALL_MODELS
from app.modules.assessments.router import router as assessments_router
from app.modules.interviews.ai_router import router as ai_interviews_router
from app.modules.interviews.router import router as interviews_router
from app.modules.inventory.router import router as inventory_router
from app.modules.training.router import router as training_router

ALL_ROUTERS: tuple[APIRouter, ...] = (
    assessments_router,
    interviews_router,
    ai_interviews_router,
    training_router,
    inventory_router,
)
