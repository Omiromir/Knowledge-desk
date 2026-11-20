import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from backend.app import database

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / "data" / "test.sqlite3"
        self.log_path = Path(self.directory.name) / "server.log"
        self.process = None
        self.log = None
        self.addCleanup(self.stop_server)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]

    def start_server(self):
        self.log = self.log_path.open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.app.main:app",
             "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=PROJECT_ROOT,
            env={**os.environ, "KNOWLEDGE_DESK_DB_PATH": str(self.db_path)},
            stdin=subprocess.DEVNULL,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                break
            try:
                if self.api("GET", "/openapi.json")[0] == 200:
                    return
            except (URLError, TimeoutError, ConnectionError):
                time.sleep(0.05)
        self.fail(f"Server did not start:\n{self.log_path.read_text(encoding='utf-8')}")

    def stop_server(self):
        if self.process is not None:
            if self.process.poll() is None:
                if os.name == "nt":
                    # The venv launcher can leave its Python child holding server.log.
                    subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
                else:
                    self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            self.process = None
        if self.log is not None:
            self.log.close()
            self.log = None

    def api(self, method, path, body=None):
        request = Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        try:
            response = urlopen(request, timeout=2)
        except HTTPError as error:
            response = error
        with response:
            content = response.read()
            return response.status, json.loads(content) if content else None

    def restart_server(self):
        self.stop_server()
        with self.assertRaises((URLError, ConnectionError)):
            self.api("GET", "/openapi.json")
        self.start_server()

    def test_crud_survives_server_restarts(self):
        self.start_server()
        text = "Кавычки ' и SQL: '); DROP TABLE documents; --"
        document = {"title": "Учебный документ", "text": text, "source": "Тест"}
        request = {"name": "О'Коннор", "question": text}
        code, saved_document = self.api("POST", "/documents", document)
        self.assertEqual(code, 201)
        code, saved_request = self.api("POST", "/requests", request)
        self.assertEqual(code, 201)
        document_path = f"/documents/{saved_document['id']}"
        request_path = f"/requests/{saved_request['id']}"
        self.assertEqual(saved_document, {**document, "id": saved_document["id"]})
        self.assertEqual(saved_request, {
            **request, "id": saved_request["id"], "status": "new", "created_at": saved_request["created_at"],
        })

        self.restart_server()
        self.assertEqual(self.api("GET", document_path), (200, saved_document))
        self.assertEqual(self.api("GET", request_path), (200, saved_request))
        self.assertEqual(self.api("GET", "/documents"), (200, [saved_document]))

        for method, path, body in [
            ("GET", "/documents/missing", None),
            ("GET", "/requests/missing", None),
            ("PATCH", "/documents/missing", {"title": "x"}),
            ("DELETE", "/documents/missing", None),
            ("GET", "/documents/" + quote("' OR 1=1 --", safe=""), None),
            ("GET", "/requests/" + quote("' OR 1=1 --", safe=""), None),
            ("PATCH", "/documents/" + quote("' OR 1=1 --", safe=""), {"title": "x"}),
            ("DELETE", "/documents/" + quote("' OR 1=1 --", safe=""), None),
        ]:
            with self.subTest(method=method, path=path):
                self.assertEqual(self.api(method, path, body)[0], 404)
        self.assertEqual(self.api("GET", document_path), (200, saved_document))

        for body in ({}, {"title": None}, {"text": "   "}):
            self.assertEqual(self.api("PATCH", document_path, body)[0], 422)
        self.assertEqual(self.api("POST", "/documents", {**document, "text": " "})[0], 422)
        self.assertEqual(self.api("POST", "/requests", {"name": "x"})[0], 422)

        updates = {"title": "Обновлено '); DROP TABLE requests; --", "source": "Новый источник"}
        updated_document = saved_document | updates
        self.assertEqual(self.api("PATCH", document_path, updates), (200, updated_document))
        self.restart_server()
        self.assertEqual(self.api("GET", document_path), (200, updated_document))
        self.assertEqual(self.api("DELETE", document_path), (204, None))
        self.restart_server()
        self.assertEqual(self.api("GET", document_path)[0], 404)
        self.assertEqual(self.api("DELETE", document_path)[0], 404)
        self.assertEqual(self.api("GET", "/documents"), (200, []))
        self.assertEqual(self.api("GET", request_path), (200, saved_request))

    def test_database_failure_is_logged_without_exposing_details(self):
        self.start_server()
        with closing(sqlite3.connect(self.db_path)) as connection, connection:
            connection.execute("DROP TABLE documents")
        self.assertEqual(
            self.api("GET", "/documents"),
            (500, {"detail": "Internal server error."}),
        )
        self.stop_server()
        log = self.log_path.read_text(encoding="utf-8")
        self.assertIn("Unhandled error for GET /documents", log)
        self.assertIn("OperationalError", log)
        self.assertNotIn("Traceback", log)
        self.assertNotIn("no such table: documents", log)

    def test_schema_initialization_and_transaction_rollback(self):
        with patch.object(database, "DB_PATH", self.db_path):
            database.init_db()
            with database.connect() as connection:
                for sql in ("PRAGMA table_info(documents)", "PRAGMA table_info(requests)"):
                    columns = connection.execute(sql).fetchall()
                    self.assertEqual([row["name"] for row in columns if row["pk"]], ["id"])
                    self.assertTrue(all(row["notnull"] for row in columns if row["name"] not in {"document_id", "created_at"}))
            with self.assertRaises(sqlite3.IntegrityError):
                with database.connect() as connection:
                    for _ in range(2):
                        connection.execute(
                            "INSERT INTO documents (id, title, text, source) VALUES (?, ?, ?, ?)",
                            ("same-id", "title", "text", "source"),
                        )
            self.assertEqual(database.list_documents(), [])
            with database.connect() as connection:
                connection.execute(
                    "INSERT INTO documents (id, title, text, source) VALUES (?, ?, ?, ?)",
                    ("kept-id", "title", "text", "source"),
                )
            database.init_db()
            self.assertEqual(database.get_document("kept-id").text, "text")

    def test_existing_requests_survive_schema_upgrade(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with patch.object(database, "DB_PATH", self.db_path):
            with database.connect() as connection:
                connection.execute(
                    """CREATE TABLE requests (
                        id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
                        question TEXT NOT NULL, status TEXT NOT NULL
                    )"""
                )
                connection.execute(
                    "INSERT INTO requests VALUES (?, ?, ?, ?)",
                    ("old-id", "Reader", "Old question", "new"),
                )
            database.init_db()
            database.init_db()
            record = database.get_request("old-id")
            self.assertEqual(record.question, "Old question")
            self.assertIsNone(record.document_id)
            self.assertIsNone(record.created_at)

    def test_status_migration_preserves_legacy_document_links(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with patch.object(database, "DB_PATH", self.db_path):
            with database.connect() as connection:
                connection.execute("CREATE TABLE documents (id TEXT PRIMARY KEY)")
                connection.execute("INSERT INTO documents VALUES ('doc')")
                connection.execute(
                    """CREATE TABLE requests (
                        id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, question TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status = 'new'),
                        document_id TEXT REFERENCES documents(id) ON DELETE SET NULL
                    )"""
                )
                connection.execute("INSERT INTO requests VALUES ('old', 'Reader', 'Old question', 'new', 'doc')")
            database.init_db()
            updated = database.update_request_status("old", "new", "in_progress")
            self.assertEqual(updated.document_id, "doc")
            database.init_db()
            self.assertEqual(database.get_request("old"), updated)
            self.assertTrue(database.delete_document("doc"))
            self.assertIsNone(database.get_request("old").document_id)


if __name__ == "__main__":
    unittest.main()
