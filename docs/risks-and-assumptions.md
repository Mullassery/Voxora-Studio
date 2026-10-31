# Risks, Assumptions, and Open Questions

## Assumptions made and why

1. **Squid contract is stable.** The adapter follows verified contracts from the Squid-CRM client source (REST envelopes `data.people`/`totalCount`, `Bearer api_…` auth, HMAC-SHA256 raw-body webhook signing, person field names). *Risk:* a live Squid server with schema drift would surface mismatches at request time. *Mitigation:* adapter errors are typed (`CrmError` with retryable flag), and `describe_fields()` reports what Squid actually exposes.
2. **SQLite is sufficient for M0.** WAL mode + explicit transitions + single-process worker. *Risk:* campaign claims (`claim_recipients`) rely on transactions with `BEGIN IMMEDIATE`-style isolation; concurrent multi-process orchestration is explicitly out of scope until M4 (Postgres).
3. **`zoneinfo` availability.** Python 3.9.6 on macOS has system tzdata; Linux CI images may need `tzdata` at runtime. *Untested assumption* flagged here.
4. **Simulation is a feature, not a stopgap.** The product contract in this build is "simulated calls only". Every consumer must see `simulated: true` on calls; UI and audit repeat it.

## Risks

### Compliance / legal (highest)
- Outbound calling carries real regulatory exposure (consent records, do-not-call lists, calling windows, recording disclosure). The eligibility gate implements the mechanical parts (consent state, suppression, calling window, retries) but **no legal review has happened**. Live calling must not be enabled until that review occurs — which is why `allow_live_calls` defaults false and is hard-rejected with the mock provider.

### Technical
- **Thread-based engine + SQLite.** Fine for M0; contention risk under high concurrency. Watch for `database is locked` if the worker and API contend heavily; mitigations exist (WAL) but scaling out is M4.
- **HMAC webhook verification is only as good as the raw-body capture.** FastAPI reads the body once; the endpoint currently reads `await request.body()` raw bytes before any JSON parse. Any future middleware that pre-consumes the body silently breaks signature checks. Guarded by tests, but worth a code comment on change.
- **Credential sealing:** Fernet keys derive from `VOXORA_SECRET_KEY`; rotating that key orphans stored connection secrets (no re-encryption path yet).
- **Timestamps in percentiles:** latency percentiles are computed in Python, not SQL; correct at current scale.
- **Console trust boundary:** the browser app bears the API token in `localStorage`. Acceptable for an operator console; a session-based flow would be safer if it becomes multi-user permissive.

### Project / honest-reporting risks
- **Unverified scaffolding:** compose stack and CI workflow are written but not executed this session. They should not be described as green until run.
- **Session version drift:** CI pins `fastapi==0.115.*` while the dev venv used 0.128.8; behavior parity unverified.
- **Squid repo Rust test suite** exceeded the time budget earlier (`cargo test -p squid-core` >180 s); if that repo is considered part of delivery, budget for the full run.

## Open questions

1. Which real telephony provider (Twilio vs. carrier SIP trunk vs. platform media streams) — decision gated on the compliance review; the adapter registry is ready for exactly one implementation.
2. Is a consent object required in M2's second CRM, or is "consent managed in Voxora only" the supported story for CRMs that lack it?
3. Retention: how long do call events/transcripts persist before pruning? Current schema retains everything indefinitely.
4. Should `dry_run` be a per-campaign blast radius guard (e.g. hard cap of e.g. 20 recipients) until live calling exists?
5. Does the Squid webhook payload ever omit `x-squid-signature` deliberately (e.g. internal replay)? Current code rejects unsigned events — confirm that's intended.
