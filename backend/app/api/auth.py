from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, StringConstraints

from .. import auth
from ..config import require_auth

router = APIRouter(prefix="/auth", tags=["auth"])


class Login(BaseModel):
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=1024)]


@router.get("/session")
def session(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if auth.settings() is None:
        return {"mode": "local", "member": None}
    if not request.cookies.get(auth.COOKIE) and not request.headers.get("authorization"):
        return {"mode": "supabase", "member": None}
    try:
        member = auth.current_member(request)
    except HTTPException as error:
        if error.status_code != 401:
            raise
        response.delete_cookie(auth.COOKIE)
        member = None
    return {"mode": "supabase", "member": member}


@router.post("/login")
def login(data: Login, request: Request, response: Response):
    result = auth.provider("token?grant_type=password", payload=data.model_dump())
    token, profile = result.get("access_token"), result.get("user")
    if not isinstance(token, str) or not isinstance(profile, dict):
        raise HTTPException(503, "Сервис входа вернул неполный ответ.")
    member = auth.member(profile)
    response.headers["Cache-Control"] = "no-store"
    response.set_cookie(auth.COOKIE, token, httponly=True, samesite="strict",
                        secure=require_auth() or request.url.scheme == "https", max_age=int(result.get("expires_in", 3600)))
    return {"mode": "supabase", "member": member}


@router.post("/logout", status_code=204)
def logout(response: Response):
    response.delete_cookie(auth.COOKIE)
