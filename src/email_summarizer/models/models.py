from pydantic import BaseModel, Field


class UserPrompt(BaseModel):
    """User prompt data model."""

    prompt: str = Field(default="")
    max_tokens: int = Field(default=100, ge=1, le=2048)
    temperature: float = Field(default=0.7, ge=0.0, le=1.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    frequency_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)


# data model  for the response from the model
class ModelResponse(BaseModel):
    """Model response data model."""

    response: str = Field(default="")
    tokens_used: int = Field(default=0, ge=0)
