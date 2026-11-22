CREATE TABLE knowledge_desk.request_reply_drafts (
    operator_id text NOT NULL REFERENCES knowledge_desk.members(user_id) ON DELETE CASCADE,
    request_id text NOT NULL REFERENCES knowledge_desk.requests(id) ON DELETE CASCADE,
    revision text NOT NULL,
    text text NOT NULL CHECK (length(text) BETWEEN 1 AND 10000),
    PRIMARY KEY (operator_id, request_id)
);
CREATE INDEX request_reply_drafts_request ON knowledge_desk.request_reply_drafts(request_id);

ALTER TABLE knowledge_desk.request_reply_drafts ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON knowledge_desk.request_reply_drafts FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge_desk.request_reply_drafts TO knowledge_desk_runtime;

CREATE POLICY operator_owns_reply_draft ON knowledge_desk.request_reply_drafts
    FOR ALL TO knowledge_desk_runtime
    USING (
        operator_id = current_setting('knowledge_desk.operator_id', true)
        AND EXISTS (
            SELECT 1 FROM knowledge_desk.members AS member
            WHERE member.user_id = request_reply_drafts.operator_id AND member.role = 'operator'
        )
    )
    WITH CHECK (
        operator_id = current_setting('knowledge_desk.operator_id', true)
        AND EXISTS (
            SELECT 1 FROM knowledge_desk.members AS member
            WHERE member.user_id = request_reply_drafts.operator_id AND member.role = 'operator'
        )
    );
