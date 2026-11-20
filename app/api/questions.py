from typing import List, Optional

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.container import Container, get_container
from app.core.security import CurrentUser, get_current_user

router = APIRouter(tags=["questions"])


class QuestionRequest(BaseModel):
    # extra="forbid": a body like {"question": "...", "company_id": "northstar"} is rejected (422).
    # There is deliberately no company field: the company comes from the JWT only.
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)

    @field_validator("question")
    @classmethod
    def not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question must not be blank")
        return v


class Citation(BaseModel):
    document: str
    section: str
    chunk_id: str


class AnswerResponse(BaseModel):
    status: str  # answered | not_covered | retry
    answer: str
    citations: List[Citation]
    policy_version: Optional[str]
    request_id: str
    meta: dict


@router.post("/questions", response_model=AnswerResponse)
def ask(
    body: QuestionRequest,
    user: CurrentUser = Depends(get_current_user),
    container: Container = Depends(get_container),
):
    result = container.answers.answer(user.company_id, body.question)
    if result["status"] == "retry":
        # 503 + Retry-After so clients and monitors can see this is transient.
        return JSONResponse(status_code=503, content=result, headers={"Retry-After": "5"})
    return result
