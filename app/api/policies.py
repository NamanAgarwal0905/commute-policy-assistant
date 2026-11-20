"""Admin routes. The company is ALWAYS the admin's own company (from the token).
There is no company_id parameter anywhere, so an admin cannot touch another tenant."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.core.container import Container, get_container
from app.core.security import CurrentUser, require_admin
from app.services.ingestion import IngestionFailed, InvalidPolicy, VersionExists, ingest_policy

router = APIRouter(prefix="/admin/policies", tags=["admin"])


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str = Field(min_length=1, max_length=20)
    document_name: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=200_000)  # Markdown with '## ' section headings


@router.post("", status_code=201)
def ingest(body: IngestRequest, admin: CurrentUser = Depends(require_admin),
           container: Container = Depends(get_container)):
    try:
        return ingest_policy(container.db, container.store, container.settings,
                             admin.company_id, body.version, body.document_name, body.content)
    except VersionExists as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except InvalidPolicy as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except IngestionFailed as exc:
        raise HTTPException(status_code=500, detail=f"{exc}. The previous policy version is still active.")


@router.get("/versions")
def versions(admin: CurrentUser = Depends(require_admin), container: Container = Depends(get_container)):
    return {"company_id": admin.company_id, "versions": container.db.list_versions(admin.company_id)}


@router.get("/active")
def active(admin: CurrentUser = Depends(require_admin), container: Container = Depends(get_container)):
    return {"company_id": admin.company_id, "active": container.db.get_active_version(admin.company_id)}
