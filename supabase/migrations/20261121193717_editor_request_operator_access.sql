SET LOCAL lock_timeout = '5s';

ALTER TABLE knowledge_desk.members
    ADD COLUMN can_operate_requests boolean NOT NULL DEFAULT false;

ALTER POLICY operator_owns_reply_draft ON knowledge_desk.request_reply_drafts
    USING (
        operator_id = current_setting('knowledge_desk.operator_id', true)
        AND EXISTS (
            SELECT 1 FROM knowledge_desk.members AS member
            WHERE member.user_id = request_reply_drafts.operator_id
              AND (member.role = 'operator' OR member.can_operate_requests)
        )
    )
    WITH CHECK (
        operator_id = current_setting('knowledge_desk.operator_id', true)
        AND EXISTS (
            SELECT 1 FROM knowledge_desk.members AS member
            WHERE member.user_id = request_reply_drafts.operator_id
              AND (member.role = 'operator' OR member.can_operate_requests)
        )
    );
