-- Only outbox rows need this index for the delivery worker's status poll.
CREATE INDEX IF NOT EXISTS records_outbox_status_created
  ON records(kind, json_extract(data,'$.status'), created)
  WHERE kind='outbox';
