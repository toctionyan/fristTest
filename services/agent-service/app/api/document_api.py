from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File

from app.security import Actor, current_actor, require_api_permission
from app.schemas.document_schema import DocumentUploadResponse, DocumentListResponse, DocumentSearchRequest

# Mounted under /api by app.main.  The actor scope is mandatory for every path.
router = APIRouter(prefix="/documents", tags=["documents"])


def _scope(actor: Actor) -> dict[str, str | None]:
    return {"tenant_id": actor.tenant_id, "user_id": actor.user_id, "role": actor.role}


@router.post("/upload", response_model=DocumentUploadResponse, dependencies=[Depends(require_api_permission("documents:write"))])
def upload_document(request: Request, file: UploadFile = File(...), actor: Actor = Depends(current_actor)):
    try:
        return request.app.state.document_service.upload(file, **_scope(actor))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("", response_model=DocumentListResponse, dependencies=[Depends(require_api_permission("documents:read"))])
def list_documents(request: Request, actor: Actor = Depends(current_actor)):
    return {"documents": request.app.state.document_service.list_documents(**_scope(actor))}


@router.post("/search", dependencies=[Depends(require_api_permission("documents:read"))])
def search_documents(request: Request, payload: DocumentSearchRequest, actor: Actor = Depends(current_actor)):
    return {"items": request.app.state.document_service.search(payload.query, top_k=payload.top_k, **_scope(actor))}


@router.get("/jobs/{job_id}", dependencies=[Depends(require_api_permission("documents:read"))])
def get_document_job(request: Request, job_id: str, actor: Actor = Depends(current_actor)):
    job = request.app.state.document_service.get_job(job_id, **_scope(actor))
    if not job:
        raise HTTPException(status_code=404, detail="document job not found")
    return {"job": job}


@router.get("/{doc_id}", dependencies=[Depends(require_api_permission("documents:read"))])
def get_document(request: Request, doc_id: str, actor: Actor = Depends(current_actor)):
    doc = request.app.state.document_service.get_document(doc_id, **_scope(actor))
    if not doc:
        raise HTTPException(status_code=404, detail="document not found")
    return {"document": doc}


@router.get("/{doc_id}/chunks", dependencies=[Depends(require_api_permission("documents:read"))])
def list_document_chunks(request: Request, doc_id: str, actor: Actor = Depends(current_actor)):
    chunks = request.app.state.document_service.list_chunks(doc_id, **_scope(actor))
    if not chunks and request.app.state.document_service.get_document(doc_id, **_scope(actor)) is None:
        raise HTTPException(status_code=404, detail="document not found")
    return {"chunks": chunks}
