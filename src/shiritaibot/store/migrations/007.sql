-- Recover the origin only when a saved routine_create result identifies it.
-- Records created outside chat, or whose history is gone, keep the legacy scope.
WITH tool_results AS (
  SELECT session, json_extract(data, '$.content') AS result
  FROM messages
  WHERE created >= (SELECT min(created) FROM records WHERE kind='routine' AND scope='owner')
    AND json_extract(data, '$.role')='tool'
    AND json_extract(data, '$._tool_name')='routine_create'
    AND session NOT IN ('', 'owner')
), origins AS (
  SELECT session, CASE WHEN json_valid(result) THEN json_extract(result, '$.id') END AS id
  FROM tool_results
), matches AS (
  SELECT id, min(session) AS session FROM origins
  WHERE id IS NOT NULL GROUP BY id HAVING count(DISTINCT session)=1
)
UPDATE records SET scope=matches.session, data=json_set(records.data, '$.scope', matches.session)
FROM matches WHERE records.kind='routine' AND records.scope='owner' AND records.id=matches.id;
