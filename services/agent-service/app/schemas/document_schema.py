from pydantic import BaseModel


class DocumentUploadResponse(BaseModel):
    doc_id: str = ""
    title: str
    chunks: int = 0
    source: str
    job_id: str
    status: str
    visibility: str | None = None


class DocumentListResponse(BaseModel):
    documents: list[dict]


class DocumentSearchRequest(BaseModel):
    query: str
    top_k: int = 5
