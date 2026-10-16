# Evaluation (planned)

Phase 20 will introduce synthetic golden leases, expected field values, citation
accuracy, extraction metrics, and confidence calibration. No evaluations or
synthetic AI outputs are presented as real in Phase 1.

`fixtures/synthetic-commercial-lease.txt` is a fictional original for Phase 4 upload
and version testing. It is not a golden extraction dataset and has no generated
AI result. Evaluation/scoring remains planned for Phase 20.

The synthetic original can also exercise Phase 5 parsing and Phase 6 Qdrant
retrieval/citation inspection. Those tests verify mechanics/source correctness,
not legal extraction accuracy or calibrated semantic recall. Default lexical
hashing is not a benchmarked neural embedding model. See [Phase 6](../docs/phase-6.md).

`synthetic-graph-source.txt` is a fictional Phase 7 manual graph/evidence exercise,
not verified legal data or an extraction benchmark. Graph test doubles are confined
to tests; they do not demonstrate real Neo4j server behavior. The opt-in integration
suite is required for server/transaction compatibility.

Phase 9 orchestration tests execute real local LangGraph/SQL/Qdrant with explicit
mock HTTP model responses to test bounded routing, schema/evidence rejection,
tenant guards, replay and tracing suppression. These are contract/security tests,
not measurements of real classification/extraction quality or calibrated semantic
confidence. Title/heading candidates always remain model-proposed and unverified.

Phase 10 adds synthetic twelve-group field fixtures and normalization edge cases
in `backend/tests/test_extraction.py`. Real local orchestration with explicit mock
model responses validates contracts/provenance, not real-model field accuracy. Empty
context observations, conditional/ambiguous values and unknown confidence are not
golden legal answers. Production-quality extraction and calibrated evaluation remain
Phase 20 work; no confidence/whole-document completeness is asserted from these tests.


## Phase 11 deterministic-rule boundary

Versioned local checks now annotate new explicitly rule-enabled lease workflows and
can separately re-evaluate stored eligible immutable extractions through the admin
API/UI. Policy/input hashes, complete rule versions and findings are retained; this
makes no legal-compliance, accuracy, completeness or approval claim. Unobserved
notice on partial input is indeterminate. Rules are off by default and add no model
calls; paid extraction still requires every existing gate. Migration 0010_rules
requires phase11 readiness and refuses destructive rollback when rule evaluations
or rules-enabled runs exist. SQL ledger append-only API is not a privileged-SQL-proof
audit trail. See [Phase 11](../docs/phase-11.md) for the catalog, configuration,
API contracts, verification and limitations. Human review/audit workflows remain future; Phase 12 comparison is described below.


## Phase 12 explicit source comparison

The SQL-only, off-by-default conflict comparator now compares two distinct versions
or a directly linked user-labelled amendment using stored source-backed proposals.
Both values, evidence, source snapshots and fingerprints are retained without
rewriting historical extraction/rule data. Reports flag potential differences only:
partial context, model roles, semantic applicability, completeness and legal
authority remain unverified. Upload chronology is not supersession. No review task,
canonical update, approval or immutable audit service is created. Migration
0011_conflicts advances readiness to phase12 and refuses rollback with retained
comparisons; complete ORM metadata is registered for Alembic CLI checks. Real
infrastructure/paid-provider validation is still gated. See [Phase 12](../docs/phase-12.md).
