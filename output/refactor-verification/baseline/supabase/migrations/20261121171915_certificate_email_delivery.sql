ALTER TABLE knowledge_desk.certificate_notifications
    ADD COLUMN provider_email_id text,
    ADD COLUMN email_status text CHECK (email_status IN ('delayed', 'delivered', 'not_delivered')),
    ADD COLUMN email_status_at text;

CREATE UNIQUE INDEX certificate_notifications_email_id
    ON knowledge_desk.certificate_notifications (provider_email_id) WHERE provider_email_id IS NOT NULL;

GRANT UPDATE (provider_email_id, email_status, email_status_at)
    ON knowledge_desk.certificate_notifications TO knowledge_desk_runtime;

CREATE POLICY backend_notification_webhook_read ON knowledge_desk.certificate_notifications
    FOR SELECT TO knowledge_desk_runtime
    USING (provider_email_id = current_setting('knowledge_desk.notification_email_id', true)
        OR (provider_email_id IS NULL AND event_id = current_setting('knowledge_desk.notification_event_id', true)));

CREATE POLICY backend_notification_webhook_update ON knowledge_desk.certificate_notifications
    FOR UPDATE TO knowledge_desk_runtime
    USING (status IN ('unknown', 'delivered')
        AND (provider_email_id = current_setting('knowledge_desk.notification_email_id', true)
            OR (provider_email_id IS NULL AND event_id = current_setting('knowledge_desk.notification_event_id', true))))
    WITH CHECK (status IN ('unknown', 'delivered')
        AND provider_email_id = current_setting('knowledge_desk.notification_email_id', true));
