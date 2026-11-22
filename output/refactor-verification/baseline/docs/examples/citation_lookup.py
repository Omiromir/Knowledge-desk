FRAGMENTS = {"login-001": "Пароли в заявку в поддержку не прикладывают."}


def resolve_citation(chunk_id: str) -> dict[str, str]:
    text = FRAGMENTS.get(chunk_id)
    if text is None:
        return {"error": "unknown_chunk_id"}
    return {"chunk_id": chunk_id, "text": text}


if __name__ == "__main__":
    found = resolve_citation("login-001")
    missing = resolve_citation("login-999")
    assert found == {"chunk_id": "login-001", "text": FRAGMENTS["login-001"]}
    assert missing == {"error": "unknown_chunk_id"}
    print(found)
    print(missing)
