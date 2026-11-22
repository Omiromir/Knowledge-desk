from contextlib import contextmanager

from backend.app import database
from backend.app.storage import requests


def test_list_batches_replies_in_order_and_keeps_owner_scope(client, monkeypatch):
    with database.connect() as connection:
        connection.executemany(
            "INSERT INTO requests (id, name, question, user_id, created_at) VALUES (?, 'Name', 'Question', ?, ?)",
            [("old", "one", None), ("current", "one", "2026-11-22"), ("other", "two", "2026-11-23")],
        )
        connection.executemany(
            """INSERT INTO request_replies (id, request_id, text, created_at, deleted)
               VALUES (?, ?, ?, ?, ?)""",
            [
                ("reply-z", "current", "First", "2026-11-22", 0),
                ("hidden", "current", "Deleted", "2026-11-22", 1),
                ("reply-a", "current", "Second", "2026-11-22", 0),
                ("foreign", "other", "Other account", "2026-11-23", 0),
            ],
        )

    statements = []

    @contextmanager
    def traced_connection():
        with database.connect() as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(requests, "connect", traced_connection)
    visible = requests.list_requests("one")
    assert [record.id for record in visible] == ["current", "old"]
    assert [reply.id for reply in visible[0].replies] == ["reply-z", "reply-a"]
    assert visible[1].replies is None
    assert sum(statement.lstrip().startswith("SELECT") for statement in statements) == 2

    all_requests = requests.list_requests(None)
    assert [record.id for record in all_requests] == ["other", "current", "old"]
    assert [reply.id for reply in all_requests[0].replies] == ["foreign"]
