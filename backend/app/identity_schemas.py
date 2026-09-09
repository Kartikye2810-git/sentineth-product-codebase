from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


Role = Literal["owner", "member", "viewer"]


class Login(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalized_email(cls, value):
        value = value.strip().lower()
        if value.count("@") != 1 or any(ch.isspace() for ch in value):
            raise ValueError("Invalid email address")
        return value


class AcceptInvitation(Login):
    token: str = Field(min_length=20, max_length=256)
    password: SecretStr = Field(min_length=12, max_length=128)


class ChangePassword(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


class SessionIssued(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime


class UserResponse(BaseModel):
    id: UUID
    email: str
    model_config = ConfigDict(from_attributes=True)


class InviteRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    role: Role = "member"
    normalize = field_validator("email")(Login.normalized_email.__func__)


class MembershipChange(BaseModel):
    role: Role


class MembershipResponse(BaseModel):
    user_id: UUID
    email: str
    role: Role


class KeyCreate(BaseModel):
    role: Role = "member"
    label: str = Field(default="Integration", min_length=1, max_length=100)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def future_expiry(cls, value):
        from app.clock import utcnow

        if value is not None:
            if value.tzinfo:
                value = value.astimezone(UTC).replace(tzinfo=None)
            if value <= utcnow():
                raise ValueError("Expiry must be in the future")
        return value


class AuditResponse(BaseModel):
    id: UUID
    actor_type: str
    actor_id: UUID | None
    action: str
    resource_id: UUID | None
    request_id: str | None
    details: dict
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
