"""Copy existing inline PDFs to the private bucket and verify their bytes before switching references."""

import argparse

from .app import database, originals, postgres
from .app.storage import documents as document_store


def migrate() -> int:
    if not postgres.database_url():
        raise ValueError("Set KNOWLEDGE_DESK_DATABASE_URL")
    with database.connect() as connection:
        ids = connection.execute("SELECT document_id FROM document_pdfs WHERE content IS NOT NULL ORDER BY document_id").fetchall()
    transferred = 0
    for row in ids:
        document_id = row["document_id"]
        pdf = document_store.get_pdf(document_id)
        if pdf is None or pdf["content"] is None:
            continue
        content = pdf["content"]
        key, digest = originals.stage(content)
        remote = originals.pdf_content({"object_key": key, "content_sha256": digest})
        if remote != content:
            raise ValueError("Uploaded original differs from the database copy")
        with database.connect() as connection:
            originals.publish(connection, key)
            result = connection.execute("""UPDATE document_pdfs SET content = NULL, object_key = ?, content_sha256 = ?
                WHERE document_id = ? AND content = ?""", (key, digest, document_id, content))
            if result.rowcount != 1:
                raise ValueError("The inline original changed during migration")
        transferred += 1
    return transferred


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cleanup-only", action="store_true", help="Remove due unreferenced objects")
    if parser.parse_args().cleanup_only:
        print(f"cleaned: {originals.cleanup()}")
    else:
        print(f"transferred: {migrate()}")


if __name__ == "__main__":
    main()
