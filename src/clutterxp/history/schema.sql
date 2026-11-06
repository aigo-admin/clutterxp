CREATE TABLE batches (
  id          INTEGER PRIMARY KEY,
  created_at  TEXT NOT NULL,
  command     TEXT NOT NULL,
  folder      TEXT,
  note        TEXT
);

CREATE TABLE operations (
  id            INTEGER PRIMARY KEY,
  batch_id      INTEGER NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
  status        TEXT NOT NULL CHECK (status IN ('pending','done','undone','failed','skipped')),
  src_path      TEXT NOT NULL,
  dst_path      TEXT,
  rule_name     TEXT,
  strategy      TEXT,
  size          INTEGER,
  mtime_ns      INTEGER,
  inode         INTEGER,
  device        INTEGER,
  sha256        TEXT,
  created_dirs  TEXT,  -- JSON array
  created_at    TEXT NOT NULL,
  finished_at   TEXT,
  undone_at     TEXT,
  error         TEXT
);
CREATE INDEX idx_ops_batch  ON operations(batch_id);
CREATE INDEX idx_ops_status ON operations(status);
CREATE INDEX idx_ops_dst    ON operations(dst_path);
CREATE INDEX idx_ops_rule   ON operations(rule_name);

CREATE TABLE events (
  id        INTEGER PRIMARY KEY,
  ts        TEXT NOT NULL,
  level     TEXT NOT NULL,
  kind      TEXT NOT NULL,
  batch_id  INTEGER,
  op_id     INTEGER,
  path      TEXT,
  message   TEXT NOT NULL
);
CREATE INDEX idx_events_ts ON events(ts);
