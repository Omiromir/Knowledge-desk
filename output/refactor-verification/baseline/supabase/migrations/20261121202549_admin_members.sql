SET LOCAL lock_timeout = '5s';

ALTER TABLE knowledge_desk.members
    ADD COLUMN is_admin boolean NOT NULL DEFAULT false,
    ADD COLUMN certificate_issuer boolean;

CREATE FUNCTION knowledge_desk.admin_member_directory(actor_id text, target_id uuid DEFAULT NULL)
RETURNS TABLE (
    user_id text, email text, role text, can_operate_requests boolean,
    is_admin boolean, certificate_issuer boolean
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM knowledge_desk.members m WHERE m.user_id = actor_id AND m.is_admin
    ) THEN
        RAISE EXCEPTION 'Administrator access required' USING ERRCODE = '42501';
    END IF;
    RETURN QUERY
        SELECT u.id::text, u.email::text, m.role, coalesce(m.can_operate_requests, false),
               coalesce(m.is_admin, false), m.certificate_issuer
        FROM auth.users u LEFT JOIN knowledge_desk.members m ON m.user_id = u.id::text
        WHERE u.email IS NOT NULL AND u.deleted_at IS NULL
          AND u.raw_app_meta_data->>'knowledge_desk_storage' IS NULL
          AND (target_id IS NULL OR u.id = target_id)
        ORDER BY lower(u.email), u.id;
END;
$$;

CREATE FUNCTION knowledge_desk.admin_manage_member(
    actor_id text, target_id uuid, access_preset text, admin_access boolean, issuer_access boolean
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
        SELECT 1 FROM knowledge_desk.members m WHERE m.user_id = actor_id AND m.is_admin
    ) THEN
        RAISE EXCEPTION 'Administrator access required' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM auth.users u WHERE u.id = target_id AND u.email IS NOT NULL
          AND u.deleted_at IS NULL AND u.raw_app_meta_data->>'knowledge_desk_storage' IS NULL
    ) THEN
        RAISE EXCEPTION 'Member not found' USING ERRCODE = 'P0002';
    END IF;
    IF issuer_access IS NULL AND access_preset IS NOT NULL AND admin_access IS NOT NULL THEN
        IF actor_id = target_id::text AND NOT admin_access THEN
            RAISE EXCEPTION 'Cannot remove own administrator access' USING ERRCODE = 'KD001';
        END IF;
        PERFORM knowledge_desk.set_member_access(target_id, access_preset);
        UPDATE knowledge_desk.members SET is_admin = admin_access WHERE user_id = target_id::text;
    ELSIF issuer_access IS NOT NULL AND access_preset IS NULL AND admin_access IS NULL THEN
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

REVOKE ALL ON FUNCTION knowledge_desk.admin_member_directory(text, uuid),
    knowledge_desk.admin_manage_member(text, uuid, text, boolean, boolean)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION knowledge_desk.admin_member_directory(text, uuid),
    knowledge_desk.admin_manage_member(text, uuid, text, boolean, boolean)
    TO knowledge_desk_runtime;
