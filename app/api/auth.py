"""Demo token issuer. In production the company's identity provider would issue the JWT."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.container import Container, get_container
from app.core.security import create_token

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


@router.post("/token")
def issue_token(body: LoginRequest, container: Container = Depends(get_container)):
    user = container.db.check_login(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    # company_id and role come from the user database, never from the request.
    token, expires_in = create_token(container.settings, user["username"], user["company_id"], user["role"])
    return {"access_token": token, "token_type": "bearer", "expires_in": expires_in}
