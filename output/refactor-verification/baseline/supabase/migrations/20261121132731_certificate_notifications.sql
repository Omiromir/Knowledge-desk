CREATE TABLE knowledge_desk.certificate_notifications (
    request_id text PRIMARY KEY REFERENCES knowledge_desk.request_certificates(request_id) ON DELETE CASCADE,
    event_id text NOT NULL UNIQUE,
    recipient text NOT NULL,
    status text NOT NULL CHECK (status IN ('waiting', 'pending', 'unknown', 'delivered', 'failed'))
);

INSERT INTO knowledge_desk.certificate_notifications (request_id, event_id, recipient, status)
SELECT request.id, gen_random_uuid()::text, account.email, 'waiting'
FROM knowledge_desk.request_certificates AS certificate
JOIN knowledge_desk.requests AS request ON request.id = certificate.request_id
JOIN auth.users AS account ON account.id::text = request.user_id
WHERE certificate.issued_at IS NULL AND account.email_confirmed_at IS NOT NULL AND account.email <> '';

REVOKE ALL ON knowledge_desk.certificate_notifications FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT ON knowledge_desk.certificate_notifications TO knowledge_desk_runtime;
GRANT UPDATE (status) ON knowledge_desk.certificate_notifications TO knowledge_desk_runtime;
ALTER TABLE knowledge_desk.certificate_notifications ENABLE ROW LEVEL SECURITY;

CREATE POLICY backend_notification_scope ON knowledge_desk.certificate_notifications
    TO knowledge_desk_runtime
    USING (request_id = current_setting('knowledge_desk.notification_request_id', true))
    WITH CHECK (request_id = current_setting('knowledge_desk.notification_request_id', true));
