from typing import Annotated

from fastapi import Depends, HTTPException, Request

from .. import auth
from ..domain import NotFoundError
from ..services import DocumentService, RequestService
from ..storage import documents as document_store, requests as request_store
from ..tool_execution import ToolContext, local_tool_context


def document_service() -> DocumentService:
    return DocumentService(document_store)


def access_context(request: Request, local: Annotated[ToolContext, Depends(local_tool_context)]) -> ToolContext:
    if auth.settings() is None:
        return local
    member = auth.current_member(request)
    if member.role == "admin":
        raise HTTPException(403, "Эта учётная запись управляет только пользователями.")
    return ToolContext(
        member.user_id, local.allowed_tools, member.role, member.email, member.can_operate_requests
    )


Context = Annotated[ToolContext, Depends(access_context)]


def editor(context: Context) -> ToolContext:
    if context.role not in (None, "editor"):
        raise HTTPException(403, "Управление документами доступно редактору.")
    return context


def editor_document(document_id: str, context: Annotated[ToolContext, Depends(editor)]) -> ToolContext:
    if context.role and document_store.sharing(document_id, context.user_id) is None:
        raise NotFoundError("Document not found.")
    return context


def check_document_access(document_id: str, context: ToolContext) -> None:
    if context.role and not document_store.document_access(document_id, context.user_id):
        raise NotFoundError("Document not found.")


def request_service(context: Context) -> RequestService:
    if not context.user_id:
        raise HTTPException(403, detail={"code": "request_access_denied"})
    return RequestService(
        request_store, context.user_id, role=context.role, can_operate_requests=context.can_operate_requests
    )


RequestHandler = Annotated[RequestService, Depends(request_service)]
