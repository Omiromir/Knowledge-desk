from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, Field, SecretStr, StringConstraints
from starlette.concurrency import run_in_threadpool

from .. import auth
from ..storage import members as member_store
from ..rate_limit import limit_expensive

router = APIRouter(prefix="/profile", tags=["profile"])
ProfileMember = Annotated[auth.Member, Depends(auth.current_member)]
UPLOAD_LIMIT = 2 * 1024 * 1024
PIXEL_LIMIT = 16_000_000


class NameUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    display_name: Annotated[
        str, StringConstraints(strip_whitespace=True, max_length=60, pattern=r"^[^\x00-\x1f\x7f]*$"),
    ]


class Profile(BaseModel):
    display_name: str
    avatar_version: str | None


class PasswordUpdate(BaseModel):
    model_config = {"extra": "forbid"}
    current_password: Annotated[SecretStr, Field(min_length=1, max_length=1024)]
    new_password: Annotated[SecretStr, Field(min_length=12, max_length=72)]


@router.get("", response_model=Profile)
def get_profile(member: ProfileMember, response: Response):
    response.headers["Cache-Control"] = "private, no-store"
    return member_store.read(member.user_id)


@router.patch("", response_model=Profile)
def change_name(update: NameUpdate, member: ProfileMember):
    return member_store.save_name(member.user_id, update.display_name)


@router.get("/avatar")
def get_avatar(member: ProfileMember):
    return avatar_response(member_store.read_avatar(member.user_id))


def avatar_response(image: bytes | None):
    if image is None:
        raise HTTPException(404, "Фото профиля отсутствует.")
    return Response(image, media_type="image/webp", headers={
        "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
    })


def normalize_avatar(content: bytes) -> bytes:
    try:
        with Image.open(BytesIO(content), formats=("JPEG", "PNG", "WEBP")) as original:
            if original.width * original.height > PIXEL_LIMIT:
                raise HTTPException(400, "Уменьшите разрешение фото до 16 мегапикселей.")
            oriented = ImageOps.exif_transpose(original)
            converted = ImageOps.fit(oriented.convert("RGBA"), (192, 192), Image.Resampling.LANCZOS)
            output = BytesIO()
            converted.save(output, format="WEBP", quality=82)
            return output.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise HTTPException(415, "Выберите читаемое изображение JPG, PNG или WebP.") from None


@router.put("/avatar", response_model=Profile)
async def upload_avatar(request: Request, member: ProfileMember):
    limit_expensive(request, member.user_id)
    content = bytearray()
    async for part in request.stream():
        if len(content) + len(part) > UPLOAD_LIMIT:
            raise HTTPException(413, "Размер фото должен быть не больше 2 МиБ.")
        content.extend(part)
    avatar = await run_in_threadpool(normalize_avatar, bytes(content))
    return await run_in_threadpool(member_store.save_avatar, member.user_id, avatar)


@router.delete("/avatar", response_model=Profile)
def remove_avatar(member: ProfileMember):
    return member_store.save_avatar(member.user_id, None)


@router.put("/password", status_code=204)
def change_password(update: PasswordUpdate, member: ProfileMember, request: Request, response: Response):
    old = update.current_password.get_secret_value()
    new = update.new_password.get_secret_value()
    if old == new or len(new.encode("utf-8")) > 72:
        raise HTTPException(400, "Выберите другой пароль длиной от 12 символов и до 72 байт.")

    limit_expensive(request, member.user_id)
    try:
        credentials = auth.provider("token?grant_type=password", payload={"email": member.email, "password": old})
    except HTTPException as error:
        if error.status_code == 401:
            raise HTTPException(400, "Текущий пароль указан неверно.") from None
        raise
    token, user = credentials.get("access_token"), credentials.get("user")
    if not isinstance(token, str) or not token or not isinstance(user, dict) or user.get("id") != member.user_id:
        raise HTTPException(503, "Не удалось подтвердить текущий аккаунт.")
    try:
        auth.provider("user", token=token, method="PUT", payload={"password": new, "current_password": old})
    except HTTPException as error:
        if error.status_code == 401:
            raise HTTPException(400, "Сервис входа не принял новый пароль. Проверьте требования к паролю.") from None
        raise
    response.delete_cookie(auth.COOKIE)
