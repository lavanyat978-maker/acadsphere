from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str
    role: str
    department: str
    mfa_enabled: bool = False
    last_login_at: datetime | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut
    mfa_required: bool = False


class MfaChallengeResponse(BaseModel):
    mfa_required: bool = True
    mfa_token: str
    expires_at: datetime
    method: str = "TOTP"


class MfaVerifyRequest(BaseModel):
    mfa_token: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=6, max_length=6)


class MfaSetupResponse(BaseModel):
    secret: str
    otpauth_uri: str
    instructions: str


class MfaConfirmRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=10, max_length=200)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        classes = sum(
            [
                any(char.islower() for char in value),
                any(char.isupper() for char in value),
                any(char.isdigit() for char in value),
                any(not char.isalnum() for char in value),
            ]
        )
        if classes < 3:
            raise ValueError("Password must mix at least three of: lower, upper, digit, symbol")
        return value


class DemoCodeRequest(BaseModel):
    mfa_token: str = Field(min_length=10, max_length=2000)


class DemoCodeResponse(BaseModel):
    code: str
    seconds_remaining: int
    warning: str
