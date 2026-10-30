from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.enums import Role


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=2, max_length=160)
    role: Role
    department: str = Field(default="Computer Science", max_length=120)
    # Optional. When omitted a temporary password is generated and returned once.
    password: str | None = Field(default=None, min_length=10, max_length=200)
    # Teachers are assigned, students enrolled. Ignored for HOD.
    subject_ids: list[str] = Field(default_factory=list, max_length=50)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=160)
    department: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None


class AdminUserOut(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    department: str
    is_active: bool
    mfa_enabled: bool
    must_change_password: bool
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    subject_ids: list[str] = []


class UserCreatedOut(BaseModel):
    user: AdminUserOut
    temporary_password: str | None = None


class PasswordResetOut(BaseModel):
    user_id: str
    temporary_password: str
