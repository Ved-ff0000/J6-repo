from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    SecretStr,
    ValidationInfo,
    field_validator,
)

from app.auth.models import Role


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def trim_non_secret_fields(cls, value: Any, info: ValidationInfo) -> Any:
        if info.field_name not in {"password", "refresh_token"} and isinstance(value, str):
            return value.strip()
        return value


class IdentityInput(InputModel):
    email: EmailStr = Field(max_length=254)
    # SecretStr avoids accidental repr/log output. Password whitespace is significant.
    password: SecretStr = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.casefold()


class MemberCreate(IdentityInput):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    role: Role = Role.VIEWER


class Register(IdentityInput):
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    organization_name: str = Field(min_length=1, max_length=200)
    organization_slug: str = Field(
        min_length=3, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
    )


class Login(InputModel):
    email: EmailStr = Field(max_length=254)
    password: SecretStr = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.casefold()


class Refresh(InputModel):
    refresh_token: SecretStr = Field(min_length=40, max_length=200)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    email: str
    first_name: str
    last_name: str
    role: Role
    status: str
    created_at: datetime


class OrganizationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    slug: str
    plan: str
    status: str
    created_at: datetime


class MemberUpdate(InputModel):
    role: Role | None = None
    status: Literal["ACTIVE", "DISABLED"] | None = None
