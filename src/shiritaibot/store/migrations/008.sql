CREATE INDEX IF NOT EXISTS records_stats_scope_created ON records(kind,scope,created)
WHERE kind IN ('usage','turn');
CREATE INDEX IF NOT EXISTS messages_session_created ON messages(session,created);
