from pydantic import BaseModel, Field, JsonValue


class ErrorResponse(BaseModel):
    detail: str


class ValidationIssue(BaseModel):
    loc: list[str | int]
    msg: str
    type: str
    input: JsonValue = None
    ctx: dict[str, JsonValue] = Field(default_factory=dict)


class ValidationErrorResponse(BaseModel):
    detail: list[ValidationIssue]
