CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

CREATE TABLE knowledge_desk.rag_chunks (
    chunk_id text PRIMARY KEY,
    document_id text REFERENCES knowledge_desk.documents(id) ON DELETE CASCADE,
    doc_id text NOT NULL,
    profile smallint NOT NULL CHECK (profile IN (0, 300, 400, 600)),
    index_key text,
    source text NOT NULL,
    version_date text NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    start_offset integer NOT NULL CHECK (start_offset >= 0),
    end_offset integer NOT NULL,
    page_number integer CHECK (page_number > 0),
    text text NOT NULL CHECK (char_length(text) > 0),
    embedding extensions.vector(384) NOT NULL,
    CHECK (end_offset - start_offset = char_length(text)),
    CHECK ((profile = 0 AND index_key IS NOT NULL AND document_id IS NOT NULL)
        OR (profile > 0 AND index_key IS NULL AND page_number IS NULL))
);
CREATE INDEX rag_chunks_document ON knowledge_desk.rag_chunks (document_id);
GRANT USAGE ON SCHEMA extensions TO knowledge_desk_runtime;
GRANT SELECT, INSERT, DELETE ON knowledge_desk.rag_chunks TO knowledge_desk_runtime;
ALTER TABLE knowledge_desk.rag_chunks ENABLE ROW LEVEL SECURITY;
CREATE POLICY rag_chunks_runtime ON knowledge_desk.rag_chunks
    TO knowledge_desk_runtime USING (true) WITH CHECK (true);
