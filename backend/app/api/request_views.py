"""Build the public view of a request and its visible participants."""

from dataclasses import asdict

from .. import certificate_notifications, certificates
from ..domain import NotFoundError, RequestRecord
from ..services import RequestService
from ..storage import members as member_store


def request_detail(record: RequestRecord, service: RequestService):
    if record.kind == certificates.KIND:
        owner = record.user_id == service.user_id and service.role is not None
        issuer = certificates.can_issue(service.user_id, service.can_operate_requests)
        can_download = owner or issuer
        history = certificates.clarifications(record.id, service.user_id, issuer=issuer) if can_download else []
        awaiting_reply = any(entry["answered_at"] is None for entry in history)
        return {
            **(with_message_authors(record, service, history) if can_download else asdict(record)),
            "certificate_owned": owner or None,
            "certificate_downloadable": can_download,
            "certificate_awaiting_reply": awaiting_reply or None,
            "certificate_can_answer": (awaiting_reply and owner) or None,
            "certificate_email_status": (
                certificate_notifications.email_status(record.id) if can_download and record.status == "resolved" else None
            ),
        }
    return with_message_authors(record, service)


def participant_profiles(record: RequestRecord, history: list[dict]):
    participants = {record.user_id}
    participants.update(reply.author_id for reply in record.replies or () if reply.author_id)
    participants.update(entry["asked_by"] for entry in history)
    return member_store.read_participants(participants)


def with_message_authors(record: RequestRecord, service: RequestService, history: list[dict] | None = None):
    if service.role is None:
        return record
    history = history or []
    profiles = participant_profiles(record, history)

    def author(user_id: str | None):
        profile = profiles.get(user_id, {})
        name, avatar = profile.get("display_name"), profile.get("avatar_version")
        own = user_id == service.user_id
        if name or avatar or own:
            return {"display_name": name or None, "avatar_version": avatar, "is_self": own or None}
        return None

    return {
        **asdict(record),
        "author": author(record.user_id),
        "replies": [{**asdict(reply), "author": author(reply.author_id)} for reply in record.replies or ()] or None,
        "certificate_clarifications": [
            {
                **entry,
                "question_author": author(entry["asked_by"]),
                "answer_author": author(record.user_id) if entry["answer"] else None,
            }
            for entry in history
        ] or None,
    }


def request_avatar_image(request_id: str, version: str, service: RequestService) -> bytes | None:
    if service.role is None:
        raise NotFoundError("Request avatar not found.")
    record = service.get(request_id)
    history = []
    if record.kind == certificates.KIND:
        issuer = certificates.can_issue(service.user_id, service.can_operate_requests)
        if record.user_id != service.user_id and not issuer:
            raise NotFoundError("Request avatar not found.")
        history = certificates.clarifications(request_id, service.user_id, issuer=issuer)
    for user_id, profile in participant_profiles(record, history).items():
        if profile["avatar_version"] == version:
            return member_store.read_avatar(user_id, version)
    raise NotFoundError("Request avatar not found.")
