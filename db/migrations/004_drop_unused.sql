-- Nothing read these: the eval set lives in eval/questions/*.jsonl, no job queue was built (add one in
-- a migration when a phase needs it), and an embedding's dimension follows from its model.
DROP TABLE IF EXISTS jobs;
DROP TABLE IF EXISTS questions;
ALTER TABLE runs DROP COLUMN IF EXISTS data_snapshot;
ALTER TABLE embeddings DROP COLUMN IF EXISTS dim;
