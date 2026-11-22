ALTER TABLE knowledge_desk.request_replies
    ADD COLUMN author_id text REFERENCES knowledge_desk.members(user_id) ON DELETE SET NULL;
