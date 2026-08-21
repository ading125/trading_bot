ALTER TABLE ai_assessments
    ADD COLUMN model_id VARCHAR DEFAULT 'unknown';

ALTER TABLE ai_assessments
    ADD COLUMN input_tokens BIGINT;

ALTER TABLE ai_assessments
    ADD COLUMN output_tokens BIGINT;
