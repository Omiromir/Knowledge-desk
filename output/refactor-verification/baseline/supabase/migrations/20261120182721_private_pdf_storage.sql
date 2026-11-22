INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES ('knowledge-desk-originals', 'knowledge-desk-originals', false, 10485760, ARRAY['application/pdf']);

CREATE POLICY knowledge_desk_originals_read ON storage.objects FOR SELECT TO authenticated
USING (bucket_id = 'knowledge-desk-originals'
    AND (SELECT auth.jwt() -> 'app_metadata' ->> 'knowledge_desk_storage') = 'originals');
CREATE POLICY knowledge_desk_originals_write ON storage.objects FOR INSERT TO authenticated
WITH CHECK (bucket_id = 'knowledge-desk-originals'
    AND (SELECT auth.jwt() -> 'app_metadata' ->> 'knowledge_desk_storage') = 'originals'
    AND name ~ '^[0-9a-f]{32}[.]pdf$');
CREATE POLICY knowledge_desk_originals_delete ON storage.objects FOR DELETE TO authenticated
USING (bucket_id = 'knowledge-desk-originals'
    AND (SELECT auth.jwt() -> 'app_metadata' ->> 'knowledge_desk_storage') = 'originals');

ALTER TABLE knowledge_desk.document_pdfs
    ALTER COLUMN content DROP NOT NULL,
    ADD COLUMN object_key text UNIQUE,
    ADD COLUMN content_sha256 text,
    ADD CONSTRAINT document_pdfs_exactly_one_original CHECK (
        (content IS NOT NULL AND object_key IS NULL AND content_sha256 IS NULL)
        OR (content IS NULL AND object_key IS NOT NULL AND object_key ~ '^[0-9a-f]{32}[.]pdf$'
            AND content_sha256 IS NOT NULL AND content_sha256 ~ '^[0-9a-f]{64}$')
    );

CREATE TABLE knowledge_desk.original_cleanup (
    object_key text PRIMARY KEY,
    not_before timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX original_cleanup_due ON knowledge_desk.original_cleanup (not_before);
GRANT SELECT, INSERT, DELETE ON knowledge_desk.original_cleanup TO knowledge_desk_runtime;

CREATE FUNCTION knowledge_desk.queue_original_cleanup() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $$
BEGIN
    IF OLD.object_key IS NOT NULL THEN
        INSERT INTO knowledge_desk.original_cleanup(object_key) VALUES (OLD.object_key) ON CONFLICT DO NOTHING;
    END IF;
    RETURN OLD;
END;
$$;
REVOKE EXECUTE ON FUNCTION knowledge_desk.queue_original_cleanup() FROM PUBLIC;
CREATE TRIGGER document_pdfs_queue_cleanup AFTER DELETE ON knowledge_desk.document_pdfs
    FOR EACH ROW EXECUTE FUNCTION knowledge_desk.queue_original_cleanup();
