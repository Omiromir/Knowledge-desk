"""PostgreSQL connection and transaction support for the application stores."""

import asyncio
import os
from functools import lru_cache


def database_url() -> str:
    return os.environ.get("KNOWLEDGE_DESK_DATABASE_URL", "").strip()


@lru_cache(maxsize=1)
def pool(url: str):
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    return ConnectionPool(url, min_size=0, max_size=8, timeout=8, open=True,
                          kwargs={"row_factory": dict_row, "connect_timeout": 5})


class Connection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, params=()):
        return self.connection.execute(statement.replace("?", "%s"), params)

    def executemany(self, statement, params):
        with self.connection.cursor() as cursor:
            cursor.executemany(statement.replace("?", "%s"), params)


def lock_row() -> str:
    return " FOR UPDATE" if database_url() else ""


def selector_loop() -> asyncio.AbstractEventLoop:
    return asyncio.SelectorEventLoop()
