CREATE FUNCTION knowledge_desk.set_member_access(target_user_id uuid, access_preset text)
RETURNS text
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
BEGIN
    IF access_preset IS NULL OR access_preset NOT IN ('reader', 'editor', 'operator', 'editor_operator') THEN
        RAISE EXCEPTION 'Unknown Knowledge Desk access preset' USING ERRCODE = '22023';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM auth.users WHERE id = target_user_id) THEN
        RAISE EXCEPTION 'Supabase Auth user not found' USING ERRCODE = '23503';
    END IF;

    INSERT INTO knowledge_desk.members (user_id, role, can_operate_requests)
    VALUES (
        target_user_id::text,
        CASE WHEN access_preset = 'editor_operator' THEN 'editor' ELSE access_preset END,
        access_preset = 'editor_operator'
    )
    ON CONFLICT (user_id) DO UPDATE SET
        role = EXCLUDED.role,
        can_operate_requests = EXCLUDED.can_operate_requests;

    RETURN access_preset;
END;
$$;

REVOKE ALL ON FUNCTION knowledge_desk.set_member_access(uuid, text)
    FROM PUBLIC, anon, authenticated, service_role, knowledge_desk_runtime;
