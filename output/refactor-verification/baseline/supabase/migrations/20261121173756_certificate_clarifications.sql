CREATE TABLE knowledge_desk.certificate_clarifications (
    request_id text NOT NULL REFERENCES knowledge_desk.request_certificates(request_id) ON DELETE CASCADE,
    id text NOT NULL,
    question text NOT NULL CHECK (length(question) BETWEEN 1 AND 4000),
    asked_by text NOT NULL REFERENCES knowledge_desk.members(user_id),
    asked_at text NOT NULL,
    answer text CHECK (length(answer) BETWEEN 1 AND 4000),
    answered_at text,
    CHECK ((answer IS NULL) = (answered_at IS NULL)),
    PRIMARY KEY (request_id, id)
);

CREATE UNIQUE INDEX certificate_clarifications_open
    ON knowledge_desk.certificate_clarifications (request_id) WHERE answered_at IS NULL;

REVOKE ALL ON knowledge_desk.certificate_clarifications FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT (request_id, id, question, asked_by, asked_at), UPDATE (answer, answered_at)
    ON knowledge_desk.certificate_clarifications TO knowledge_desk_runtime;
ALTER TABLE knowledge_desk.certificate_clarifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY certificate_clarification_read ON knowledge_desk.certificate_clarifications
    FOR SELECT TO knowledge_desk_runtime
    USING (current_setting('knowledge_desk.certificate_issuer', true) = '1' OR EXISTS (
        SELECT 1 FROM knowledge_desk.requests AS request
        WHERE request.id = request_id
          AND request.user_id = current_setting('knowledge_desk.certificate_actor', true)
    ));

CREATE POLICY certificate_clarification_ask ON knowledge_desk.certificate_clarifications
    FOR INSERT TO knowledge_desk_runtime
    WITH CHECK (current_setting('knowledge_desk.certificate_issuer', true) = '1'
        AND asked_by = current_setting('knowledge_desk.certificate_actor', true)
        AND answer IS NULL);

CREATE POLICY certificate_clarification_answer ON knowledge_desk.certificate_clarifications
    FOR UPDATE TO knowledge_desk_runtime
    USING (answer IS NULL AND EXISTS (
        SELECT 1 FROM knowledge_desk.requests AS request
        WHERE request.id = request_id
          AND request.user_id = current_setting('knowledge_desk.certificate_actor', true)
    ))
    WITH CHECK (answer IS NOT NULL AND EXISTS (
        SELECT 1 FROM knowledge_desk.requests AS request
        WHERE request.id = request_id
          AND request.user_id = current_setting('knowledge_desk.certificate_actor', true)
    ));
