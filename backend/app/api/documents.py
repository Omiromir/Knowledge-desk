from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, StrictBool
from starlette.concurrency import run_in_threadpool

from ..models import Document, DocumentCreate, DocumentPatch
from ..rate_limit import limit_expensive
from .. import originals, uploads
from ..storage import documents as document_store
from ..document_files import MAX_FILE_BYTES, MAX_PLAIN_BYTES, TEXT_EXTENSIONS, extension_for, extract_pdf_page
from ..domain import NotFoundError
from ..services import DocumentService
from ..summarizer import SummaryError
from ..tool_execution import ToolContext
from .dependencies import Context, check_document_access, document_service, editor, editor_document

router = APIRouter(prefix="/documents", tags=["documents"])
Service = Annotated[DocumentService, Depends(document_service)]
Editor = Annotated[ToolContext, Depends(editor)]
OwnerEditor = Annotated[ToolContext, Depends(editor_document)]


class Sharing(BaseModel):
    shared: StrictBool


UPLOAD_ERRORS = {
    "upload_format": 415,
    "upload_encoding": 415,
    "upload_empty": 422,
    "upload_pdf_locked": 422,
    "upload_pdf_no_text": 422,
    "upload_ocr_pages": 413,
    "upload_ocr_timeout": 504,
    "upload_ocr_busy": 503,
    "upload_ocr_unavailable": 503,
    "upload_unreadable": 422,
    "upload_too_large": 413,
    "upload_file_too_large": 413,
    "upload_text_too_large": 413,
    "upload_pdf_pages": 413,
    "upload_word_package": 413,
    "upload_index_unavailable": 503,
    "upload_storage_unavailable": 503,
}


def upload_error(exc: SummaryError) -> HTTPException:
    return HTTPException(UPLOAD_ERRORS[exc.code], {"code": exc.code, "message": str(exc)})


@router.get("", response_model=list[Document])
def list_documents(service: Service, context: Context):
    return document_store.readable_documents(context.user_id) if context.role else service.list()


@router.post("", response_model=Document, status_code=status.HTTP_201_CREATED)
def create_document(document: DocumentCreate, service: Service, context: Editor):
    return service.create(**document.model_dump(), owner_id=context.user_id if context.role else None)


@router.post("/upload", response_model=Document, status_code=status.HTTP_201_CREATED,
             openapi_extra={"requestBody": {"required": True, "content": {
                 "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
             }}})
async def upload_document(request: Request, filename: Annotated[str, Query(min_length=1, max_length=180)], context: Editor):
    try:
        plain = extension_for(filename) in TEXT_EXTENSIONS
        limit = MAX_PLAIN_BYTES if plain else MAX_FILE_BYTES
        data = bytearray()
        async for part in request.stream():
            if len(data) + len(part) > limit:
                if plain:
                    raise SummaryError("upload_too_large", "The file exceeds 64 KiB.")
                raise SummaryError("upload_file_too_large", "The file exceeds 10 MiB.")
            data.extend(part)
        limit_expensive(request, context.user_id)
        return await run_in_threadpool(uploads.add, filename, bytes(data), owner_id=context.user_id if context.role else None)
    except SummaryError as exc:
        raise upload_error(exc) from None


@router.get("/{document_id}", response_model=Document)
def get_document(document_id: str, service: Service, context: Context):
    check_document_access(document_id, context)
    return service.get(document_id)


@router.get("/{document_id}/original.pdf", response_class=Response)
def original_pdf(document_id: str, context: Context, page: Annotated[int | None, Query(ge=1)] = None):
    check_document_access(document_id, context)
    pdf = document_store.get_pdf(document_id)
    if pdf is None:
        raise NotFoundError("PDF original not found.")
    filename = quote(pdf["filename"], safe="")
    try:
        content = originals.pdf_content(pdf)
    except originals.StorageError:
        raise HTTPException(503, "Оригинал PDF временно недоступен.") from None
    if page is not None:
        content = extract_pdf_page(content, page)
    return Response(
        content, media_type="application/pdf", headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{filename}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.patch("/{document_id}", response_model=Document)
def update_document(document_id: str, updates: DocumentPatch, service: Service, context: OwnerEditor):
    try:
        return uploads.update(service, document_id, updates.model_dump(exclude_unset=True))
    except SummaryError as exc:
        raise upload_error(exc) from None


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(document_id: str, service: Service, context: OwnerEditor):
    uploads.delete(service, document_id)
    originals.retry_cleanup()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{document_id}/access", response_model=Sharing)
def get_sharing(document_id: str, context: OwnerEditor):
    value = document_store.sharing(document_id, context.user_id)
    if value is None:
        raise NotFoundError("Document access not found.")
    return value


@router.patch("/{document_id}/access", response_model=Sharing)
def set_sharing(document_id: str, payload: Sharing, context: OwnerEditor):
    value = document_store.sharing(document_id, context.user_id, payload.shared)
    if value is None:
        raise NotFoundError("Document access not found.")
    return value
