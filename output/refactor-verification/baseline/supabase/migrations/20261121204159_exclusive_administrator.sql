SET LOCAL lock_timeout = '5s';

DROP FUNCTION knowledge_desk.admin_member_directory(text, uuid);
DROP FUNCTION knowledge_desk.admin_manage_member(text, uuid, text, boolean, boolean);

ALTER TABLE knowledge_desk.members
    DROP COLUMN is_admin,
    DROP CONSTRAINT members_role_check,
    ADD CONSTRAINT members_role_check CHECK (role IN ('reader', 'editor', 'operator', 'admin')),
    ADD CONSTRAINT members_admin_is_exclusive CHECK (
        role <> 'admin' OR (NOT can_operate_requests AND certificate_issuer IS DISTINCT FROM true)
    );

CREATE UNIQUE INDEX members_one_admin ON knowledge_desk.members (role) WHERE role = 'admin';

CREATE FUNCTION knowledge_desk.install_administrator(target_id uuid)
RETURNS uuid
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM auth.users u WHERE u.id = target_id AND u.email IS NOT NULL
          AND u.email_confirmed_at IS NOT NULL AND u.deleted_at IS NULL
          AND u.raw_app_meta_data->>'knowledge_desk_storage' IS NULL
    ) THEN
        RAISE EXCEPTION 'Create and confirm the separate Supabase Auth account first' USING ERRCODE = '23503';
    END IF;
    INSERT INTO knowledge_desk.members (user_id, role) VALUES (target_id::text, 'admin')
    ON CONFLICT (user_id) DO NOTHING;
    IF NOT EXISTS (
        SELECT 1 FROM knowledge_desk.members m WHERE m.user_id = target_id::text AND m.role = 'admin'
    ) THEN
        RAISE EXCEPTION 'Use a separate account without a work role' USING ERRCODE = 'KD001';
    END IF;
    RETURN target_id;
END;
$$;

CREATE OR REPLACE FUNCTION knowledge_desk.set_member_access(target_user_id uuid, access_preset text)
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
    INSERT INTO knowledge_desk.members AS m (user_id, role, can_operate_requests)
    VALUES (
        target_user_id::text,
        CASE WHEN access_preset = 'editor_operator' THEN 'editor' ELSE access_preset END,
        access_preset = 'editor_operator'
    )
    ON CONFLICT (user_id) DO UPDATE SET
        role = EXCLUDED.role,
        can_operate_requests = EXCLUDED.can_operate_requests
    WHERE m.role <> 'admin';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'The administrator account is separate' USING ERRCODE = 'KD001';
    END IF;
    RETURN access_preset;
END;
$$;

CREATE FUNCTION knowledge_desk.admin_member_directory(actor_id text, target_id uuid DEFAULT NULL)
RETURNS TABLE (
    user_id text, email text, role text, can_operate_requests boolean, certificate_issuer boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM knowledge_desk.members m WHERE m.user_id = actor_id AND m.role = 'admin'
    ) THEN
        RAISE EXCEPTION 'Administrator access required' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT u.id::text, u.email::text, m.role, coalesce(m.can_operate_requests, false), m.certificate_issuer
        FROM auth.users u LEFT JOIN knowledge_desk.members m ON m.user_id = u.id::text
        WHERE u.email IS NOT NULL AND u.deleted_at IS NULL
          AND u.raw_app_meta_data->>'knowledge_desk_storage' IS NULL
          AND m.role IS DISTINCT FROM 'admin'
          AND (target_id IS NULL OR u.id = target_id)
        ORDER BY lower(u.email), u.id;
END;
$$;

CREATE FUNCTION knowledge_desk.admin_manage_member(
    actor_id text, target_id uuid, access_preset text, issuer_access boolean
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
SET lock_timeout = '3s'
AS $$
BEGIN
    PERFORM 1 FROM knowledge_desk.members m
    WHERE m.user_id IN (actor_id, target_id::text) ORDER BY m.user_id FOR UPDATE;
    IF NOT EXISTS (
        SELECT 1 FROM knowledge_desk.members m WHERE m.user_id = actor_id AND m.role = 'admin'
    ) THEN
        RAISE EXCEPTION 'Administrator access required' USING ERRCODE = '42501';
    END IF;
    IF actor_id = target_id::text THEN
        RAISE EXCEPTION 'The administrator account is separate' USING ERRCODE = 'KD001';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM auth.users u WHERE u.id = target_id AND u.email IS NOT NULL
          AND u.deleted_at IS NULL AND u.raw_app_meta_data->>'knowledge_desk_storage' IS NULL
    ) THEN
        RAISE EXCEPTION 'Member not found' USING ERRCODE = 'P0002';
    END IF;
    IF issuer_access IS NULL AND access_preset IS NOT NULL THEN
        PERFORM knowledge_desk.set_member_access(target_id, access_preset);
    ELSIF issuer_access IS NOT NULL AND access_preset IS NULL THEN
        IF issuer_access AND NOT EXISTS (
            SELECT 1 FROM knowledge_desk.members m
            WHERE m.user_id = target_id::text AND (m.role = 'operator' OR m.can_operate_requests)
        ) THEN
            RAISE EXCEPTION 'Request queue access required' USING ERRCODE = 'KD002';
        END IF;
        UPDATE knowledge_desk.members SET certificate_issuer = issuer_access WHERE user_id = target_id::text;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'Member not found' USING ERRCODE = 'P0002';
        END IF;
    ELSE
        RAISE EXCEPTION 'Choose a single access operation' USING ERRCODE = '22023';
    END IF;
END;
$$;

REVOKE ALL ON FUNCTION knowledge_desk.install_administrator(uuid),
    knowledge_desk.set_member_access(uuid, text)
    FROM PUBLIC, anon, authenticated, service_role, knowledge_desk_runtime;
REVOKE ALL ON FUNCTION knowledge_desk.admin_member_directory(text, uuid),
    knowledge_desk.admin_manage_member(text, uuid, text, boolean)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION knowledge_desk.admin_member_directory(text, uuid),
    knowledge_desk.admin_manage_member(text, uuid, text, boolean)
    TO knowledge_desk_runtime;
