create role knowledge_desk_n8n nologin;
grant usage on schema knowledge_desk to knowledge_desk_n8n;

alter table knowledge_desk.n8n_receipts enable row level security;

create function knowledge_desk.record_n8n_receipt(
    p_event_id uuid, p_request_id uuid, p_execution_id text
)
returns table (event_id uuid, request_id uuid, execution_id text, matches_request boolean)
language sql
security definer
set search_path = ''
as $$
    insert into knowledge_desk.n8n_receipts as stored (event_id, request_id, execution_id)
    values (p_event_id, p_request_id, p_execution_id)
    on conflict (event_id) do update set event_id = stored.event_id
    returning stored.event_id, stored.request_id, stored.execution_id, stored.request_id = p_request_id;
$$;

revoke all on function knowledge_desk.record_n8n_receipt(uuid, uuid, text)
    from public, anon, authenticated, service_role;
grant execute on function knowledge_desk.record_n8n_receipt(uuid, uuid, text)
    to knowledge_desk_n8n;
