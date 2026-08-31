from pydantic import BaseModel, Field


class AnswerResult(BaseModel):
    answer: str = Field(description="Final natural-language answer for the user.")
    warning: str | None = Field(
        default=None,
        description="Set when the answer is degraded or produced without an LLM.",
    )
