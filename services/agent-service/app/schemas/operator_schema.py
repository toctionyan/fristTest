from pydantic import BaseModel, Field


class CommandRequest(BaseModel):
    """Normal operations command; the backend owns reviewer/time/status facts."""

    command: str = Field(..., min_length=2, max_length=64)
    expected_version: int = Field(..., ge=1)
    note: str | None = Field(default=None, max_length=1000)
