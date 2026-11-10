"""系统管理域请求体（A06-4）：与 `app/modules/system/models.py` 六张表一一对应。

仅承载入参校验；出参走 `_row_to_dict` 直接序列化 ORM 列，避免为每张表各写一套
response_model（统一由 `app.core.response.ok` 包裹）。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DepartmentCreate(BaseModel):
    name: str = Field(..., max_length=120)
    parent_id: int | None = None
    leader: str | None = Field(None, max_length=50)
    phone: str | None = Field(None, max_length=30)
    sort: int = 0
    status: str = "active"


class DepartmentUpdate(BaseModel):
    name: str | None = Field(None, max_length=120)
    parent_id: int | None = None
    leader: str | None = Field(None, max_length=50)
    phone: str | None = Field(None, max_length=30)
    sort: int | None = None
    status: str | None = None


class RoleCreate(BaseModel):
    code: str = Field(..., max_length=64)
    name: str = Field(..., max_length=120)
    description: str | None = None
    data_scope: str = "self"
    status: str = "active"


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    data_scope: str | None = None
    status: str | None = None


class MenuCreate(BaseModel):
    parent_id: int | None = None
    name: str = Field(..., max_length=120)
    path: str | None = Field(None, max_length=255)
    icon: str | None = Field(None, max_length=100)
    permission_code: str | None = Field(None, max_length=120)
    menu_type: str = "menu"
    hidden: bool = False
    sort: int = 0
    status: str = "active"


class MenuUpdate(BaseModel):
    parent_id: int | None = None
    name: str | None = None
    path: str | None = None
    icon: str | None = None
    permission_code: str | None = None
    menu_type: str | None = None
    hidden: bool | None = None
    sort: int | None = None
    status: str | None = None


class UserRoleBind(BaseModel):
    user_id: int


class PermissionItem(BaseModel):
    menu_id: int | None = None
    action: str
    data_scope: str = "self"


class RolePermissionSet(BaseModel):
    permissions: list[PermissionItem] = Field(default_factory=list)
