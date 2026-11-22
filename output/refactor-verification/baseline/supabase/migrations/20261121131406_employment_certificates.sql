ALTER TABLE knowledge_desk.requests
    ADD COLUMN kind text CHECK (kind = 'employment_certificate');

CREATE TABLE knowledge_desk.request_certificates (
    request_id text PRIMARY KEY REFERENCES knowledge_desk.requests(id) ON DELETE CASCADE,
    draft bytea NOT NULL,
    signed_pdf bytea CHECK (octet_length(signed_pdf) BETWEEN 1 AND 5242880),
    issued_by text REFERENCES knowledge_desk.members(user_id),
    issued_at timestamptz,
    CHECK ((issued_at IS NULL AND issued_by IS NULL AND signed_pdf IS NULL)
        OR (issued_at IS NOT NULL AND issued_by IS NOT NULL AND signed_pdf IS NOT NULL))
);

REVOKE ALL ON knowledge_desk.request_certificates FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON knowledge_desk.request_certificates TO knowledge_desk_runtime;
GRANT UPDATE (signed_pdf, issued_by, issued_at) ON knowledge_desk.request_certificates TO knowledge_desk_runtime;
ALTER TABLE knowledge_desk.request_certificates ENABLE ROW LEVEL SECURITY;

CREATE POLICY certificate_read ON knowledge_desk.request_certificates
    FOR SELECT TO knowledge_desk_runtime
    USING (current_setting('knowledge_desk.certificate_issuer', true) = '1' OR EXISTS (
        SELECT 1 FROM knowledge_desk.requests AS request
        WHERE request.id = request_id
          AND request.user_id = current_setting('knowledge_desk.certificate_actor', true)
    ));

CREATE POLICY certificate_create ON knowledge_desk.request_certificates
    FOR INSERT TO knowledge_desk_runtime
    WITH CHECK (issued_at IS NULL AND EXISTS (
        SELECT 1 FROM knowledge_desk.requests AS request
        WHERE request.id = request_id
          AND request.kind = 'employment_certificate'
          AND request.user_id = current_setting('knowledge_desk.certificate_actor', true)
    ));

CREATE POLICY certificate_issue ON knowledge_desk.request_certificates
    FOR UPDATE TO knowledge_desk_runtime
    USING (issued_at IS NULL AND current_setting('knowledge_desk.certificate_issuer', true) = '1')
    WITH CHECK (issued_at IS NOT NULL AND current_setting('knowledge_desk.certificate_issuer', true) = '1'
        AND issued_by = current_setting('knowledge_desk.certificate_actor', true));
