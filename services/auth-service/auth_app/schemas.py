from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterSchema(BaseModel):
    username: str = Field(..., min_length=3, max_length=255)
    email: EmailStr
    password: str = Field(..., min_length=6)


class LoginSchema(BaseModel):
    email: EmailStr
    password: str


class ResendVerificationEmailSchema(BaseModel):
    email: EmailStr


class UserResponseSchema(BaseModel):
    id: int | str
    username: str
    email: EmailStr
    email_verified: bool
    roles: list[str] = []

    model_config = ConfigDict(from_attributes=True)

    @field_validator("roles", mode="before")
    @classmethod
    def transform_roles(cls, v):
        if isinstance(v, list):
            return [r.name if hasattr(r, "name") else r for r in v]
        return v
