CREATE TABLE IF NOT EXISTS knowledge_desk_test_receipts (
    event_id uuid PRIMARY KEY,
    request_id uuid NOT NULL,
    execution_id text NOT NULL CHECK (execution_id ~ '^[0-9]{1,64}$')
);
