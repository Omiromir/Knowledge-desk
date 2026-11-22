CREATE TABLE knowledge_desk.member_profiles (
    user_id text PRIMARY KEY REFERENCES knowledge_desk.members(user_id) ON DELETE CASCADE,
    display_name text NOT NULL DEFAULT '' CHECK (char_length(display_name) <= 60),
    avatar bytea CHECK (octet_length(avatar) <= 131072),
    avatar_version text,
    CHECK ((avatar IS NULL) = (avatar_version IS NULL))
);

ALTER TABLE knowledge_desk.member_profiles ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON knowledge_desk.member_profiles FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT, INSERT, UPDATE ON knowledge_desk.member_profiles TO knowledge_desk_runtime;

CREATE POLICY own_profile ON knowledge_desk.member_profiles
    FOR ALL TO knowledge_desk_runtime
    USING (user_id = (SELECT current_setting('knowledge_desk.profile_user_id', true)))
    WITH CHECK (user_id = (SELECT current_setting('knowledge_desk.profile_user_id', true)));
