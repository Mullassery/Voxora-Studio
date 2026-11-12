# Near-term production hygiene — design (small)

Approves the session order: worker safety → metrics → second CRM adapter.
Blocked items (live Squid, Twilio live, media-backend choice, real-recording
analytics) stay out — they need servers/credentials/legal review Voxora does
not have here.

## Item 1 — Multi-worker claiming safety (roadmap M4a)

**Why:** `PostgresDatabase._write()` runs default READ COMMITTED with no
`BEGIN IMMEDIATE` equivalent, and `Database.claim_recipients` (db.py:1300)
ignores the `UPDATE campaign_recipients … WHERE status='queued'` rowcount —
two concurrent Postgres workers can both insert call rows for the same
recipient (SQLite serializes writers via BEGIN IMMEDIATE, so only PG is
broken; SQLite gets the rowcount guard as defense in depth).

**Approach chosen:** two layers.
1. Shared `Database.claim_recipients`: proceed to insert call/call-event rows
   only when the claim UPDATE reports `rowcount == 1`; otherwise skip the
   recipient (it was claimed by another worker mid-transaction).
2. `PostgresDatabase.claim_recipients` override: same logic with PG-native
   claiming — `pg_advisory_xact_lock(tenant-keyed)` at transaction start to
   serialize slot accounting, `FOR UPDATE SKIP LOCKED` on the running-campaign
   and queued-recipient SELECTs so concurrent workers never block or pick the
   same rows. SQLite keeps `BEGIN IMMEDIATE` (its `_write` is already
   serialized per process; cross-process safety comes from the rowcount guard
   plus SQLite's file lock).

**Anti-goals:** no rewrite of the worker loop; no new queue tables; campaign
event rows keep the existing envelope.

**Tests (RED first):** concurrency test in `test_storage_compat.py` style —
campaign + N queued recipients, two `Database` instances (same backend, own
connections) racing `claim_recipients`; assert disjoint recipients, no
duplicate calls rows, and the total attempt count matches. Parametrized over
SQLite and Postgres (SQLite is the control that must already pass on the
rowcount assertion).

## Item 2 — Prometheus metrics endpoint (roadmap M4c)

`call_latency` rows and domain counters already exist. New `GET /metrics`
returning Prometheus text exposition: process-up gauge, campaign/call/session
status counters (queried live), per-stage latency p50/p95 gauges from
`call_latency`, scraped via the standard bearer-token auth header (no new
secret, no unauthenticated surface). Tests: content-type, expected metric
families, latency aggregation correctness, 401 without token.

## Item 3 — Second CRM adapter (roadmap M2)

Same pattern as the Twilio adapter: documented-REST contract + fake-transport
tests, no live server. Adapter exposes the capability-honest port
(`crm/sync.py` protocol) against a consent/suppression-capable CRM contract
(candidate: HubSpot contacts + subscription/opt-status objects) — proving
adapter portability without pretending a live instance was verified. Capability
matrix stays honest: contract-verified, live-unverified.

Commits after each item, conventional style, full orchestrator suite green
(SQLite + Postgres) before each commit.
