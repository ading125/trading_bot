-- DuckDB 1.5 can invalidate the connection while maintaining these optional
-- ART indexes during large ON CONFLICT updates. Primary-key and unique
-- constraints remain intact, and these local tables are small enough that the
-- lookup indexes do not provide a material benefit.
DROP INDEX IF EXISTS company_alias_lookup_idx;
DROP INDEX IF EXISTS candidate_evidence_active_idx;
