from contextlib import contextmanager
from uuid import uuid4

from . import database, postgres


@contextmanager
def _connection(user_id: str):
    with database.connect() as connection:
        _select_profile(connection, user_id)
        yield connection


def _select_profile(connection, user_id: str):
    if postgres.database_url():
        connection.execute("SELECT set_config('knowledge_desk.profile_user_id', ?, true)", (user_id,))


def read(user_id: str):
    with _connection(user_id) as connection:
        row = connection.execute(
            "SELECT display_name, avatar_version FROM member_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    return dict(row) if row else {"display_name": "", "avatar_version": None}


def read_participants(user_ids: set[str]):
    profiles = {}
    with database.connect() as connection:
        for user_id in user_ids:
            _select_profile(connection, user_id)
            row = connection.execute(
                "SELECT display_name, avatar_version FROM member_profiles WHERE user_id = ?", (user_id,),
            ).fetchone()
            if row:
                profiles[user_id] = dict(row)
    return profiles


def save_name(user_id: str, display_name: str):
    with _connection(user_id) as connection:
        row = connection.execute(
            """INSERT INTO member_profiles (user_id, display_name) VALUES (?, ?)
               ON CONFLICT (user_id) DO UPDATE SET display_name = excluded.display_name
               RETURNING display_name, avatar_version""",
            (user_id, display_name),
        ).fetchone()
    return dict(row)


def read_avatar(user_id: str, version: str | None = None) -> bytes | None:
    with _connection(user_id) as connection:
        row = connection.execute(
            "SELECT avatar FROM member_profiles WHERE user_id = ? AND (CAST(? AS TEXT) IS NULL OR avatar_version = ?)",
            (user_id, version, version),
        ).fetchone()
    return bytes(row["avatar"]) if row and row["avatar"] is not None else None


def save_avatar(user_id: str, avatar: bytes | None):
    with _connection(user_id) as connection:
        row = connection.execute(
            """INSERT INTO member_profiles (user_id, avatar, avatar_version) VALUES (?, ?, ?)
               ON CONFLICT (user_id) DO UPDATE SET avatar = excluded.avatar, avatar_version = excluded.avatar_version
               RETURNING display_name, avatar_version""",
            (user_id, avatar, str(uuid4()) if avatar is not None else None),
        ).fetchone()
    return dict(row)
