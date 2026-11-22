import os
from urllib.parse import urlsplit

FRONTEND_ORIGIN = os.environ.get("KNOWLEDGE_DESK_FRONTEND_ORIGIN", "http://127.0.0.1:5173")
LOCAL_USER_ID = "local"
CONSULTATION_MAX_STEPS = 8
CONSULTATION_MAX_TOOL_CALLS = 3
CONSULTATION_ACTIVE_SECONDS = 12


def require_auth() -> bool:
    value = os.getenv("KNOWLEDGE_DESK_REQUIRE_AUTH", "0")
    if value not in {"0", "1"}:
        raise RuntimeError("KNOWLEDGE_DESK_REQUIRE_AUTH must be 0 or 1.")
    return value == "1"


def validate_origin() -> None:
    origin = urlsplit(FRONTEND_ORIGIN)
    origin.port
    if (origin.scheme not in {"http", "https"} or not origin.hostname
            or origin.geturl() != FRONTEND_ORIGIN or origin.username is not None
            or origin.path or origin.query or origin.fragment):
        raise RuntimeError("Configure one exact browser origin without a path.")
    if require_auth() and origin.scheme != "https":
        raise RuntimeError("Deployment requires an HTTPS browser origin.")


def timeout_seconds(name: str, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(maximum)))
    except ValueError:
        raise RuntimeError(f"{name} must be a positive number.") from None
    if not 0 < value <= maximum:
        raise RuntimeError(f"{name} must be between 0 and {maximum}, excluding 0.")
    return value
