from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

PASSWORD_FIELD = Field(..., min_length=6)


class RegisterSchema(BaseModel):
    username: str = Field(..., min_length=3, max_length=255)
    email: EmailStr
    password: str = PASSWORD_FIELD


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


class RefreshSchema(BaseModel):
    refresh_token: str


class LogoutSchema(BaseModel):
    refresh_token: str | None = None


class ForgotPasswordSchema(BaseModel):
    email: EmailStr


class ResetPasswordSchema(BaseModel):
    token: str
    new_password: str = PASSWORD_FIELD


class ChangePasswordSchema(BaseModel):
    current_password: str
    new_password: str = PASSWORD_FIELD

    @model_validator(mode="after")
    def _must_actually_change(self) -> "ChangePasswordSchema":
        if self.new_password == self.current_password:
            raise ValueError("new_password must differ from current_password")
        return self
