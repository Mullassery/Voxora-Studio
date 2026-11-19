# Graph Report - Voxora-Studio  (2026-11-20)

## Corpus Check
- 136 files · ~136,700 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 10 file(s) not represented in the graph (top: (none) 4, .typed 2, .example 1)

## Summary
- 2974 nodes · 7799 edges · 116 communities (98 shown, 18 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 1137 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `64c9d892`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_voice_providers.py
- media.rs
- bridge.rs
- api.py
- test_session_budgets.py
- iso
- .get_voice_session
- CampaignEngine
- Database
- endpointing.rs
- vad.rs
- paths.ts
- controller.rs
- semantic.rs
- LiveTurnEngine
- CallRequest
- settle_voice_session
- create_app
- barge_in.rs
- TurnDetectionController
- generate_long_form
- test_openrouter.py
- test_voice_boost_settings.py
- PostgresDatabase
- td_events.rs
- openrouter.py
- turn.rs
- LeadConnectorCrmAdapter
- package.json
- App.tsx
- TurnDetectionEngine
- HttpResponse
- ConsoleApi
- Task 1: Model-profile dispatch + usage rows (P8)
- typing
- wav.rs
- tts_generation.py
- test_twilio_adapter.py
- dsp.rs
- VoiceBoostStage
- pytest
- _error_body
- CanonicalContact
- test_api.py
- json
- SquidCrmAdapter
- test_voice_sessions.py
- apiTypes.ts
- .create_agent
- build_tts_provider
- OpenRouterAgent
- test_campaign_engine.py
- compilerOptions
- TurnEventType
- VoiceBoostConfig
- conftest.py
- AgentBrain
- OpenRouterLLM
- test_twilio_webhooks.py
- Biquad
- AudioFileStore
- test_ghl_adapter.py
- AudioGenerationError
- test_squid_adapter.py
- TurnDetectionState
- Phase 1–3 update (2026-11-01, verified this session)
- fastapi_testclient
- Ecosystem Migration — Capability Inventory (Phase 1)
- Roadmap
- _turn_engine_or_404
- .process
- test_engine.py
- Implementation Audit (honest verification record)
- audio_batches.py
- prometheus_metrics
- CheckpointQueue
- Settings
- test_variants_api.py
- test_longform.py
- test_audience_import.py
- SemanticVerdict
- Voxora Studio — Architecture
- Risks
- generate_asset
- Candidates
- AudioGeneration.tsx
- What actually runs (executed commands, this session)
- downsampler_preserves_bandlimited_signal_rms
- postprocess.rs
- TTSProviderError
- test_longform_api.py
- voxora-voice-runtime
- AgentEvent
- config.py
- sync.py
- voxora-studio
- variants.py
- .ensure_tenant
- voxora-voice-runtime
- voxora_voice_runtime
- model_validator
- _campaign_view
- Review Focus
- _page
- Audio Generation Studio (M10 Phase A–B) — design
- Voice Boost settings surface — design (small)
- FieldInfo
- NullEchoCanceller
- Near-term production hygiene — design (small)
- .addRecipients

## God Nodes (most connected - your core abstractions)
1. `Database` - 274 edges
2. `create_app()` - 194 edges
3. `iso()` - 71 edges
4. `Settings` - 65 edges
5. `ApiError` - 59 edges
6. `ConsoleApi` - 47 edges
7. `generate_long_form()` - 46 edges
8. `TurnDetectionController` - 42 edges
9. `create_variant()` - 41 edges
10. `_require_writer()` - 39 edges

## Surprising Connections (you probably didn't know these)
- `AI runtime (Python)` --references--> `MockAgent`  [INFERRED]
  docs/architecture.md → services/ai-runtime/src/voxora_ai/engine.py
- `M10 Audio Generation, Phase C — non-destructive variants (2026-11-13, verified this session)` --references--> `_page()`  [INFERRED]
  docs/implementation-audit.md → services/orchestrator/src/voxora/api.py
- `Assembly` --references--> `validate_audio_blob()`  [INFERRED]
  docs/superpowers/specs/2026-11-13-longform-generation-design.md → services/orchestrator/src/voxora/audio_assets.py
- `CRM integration — Squid (first integration, verified against source)` --references--> `NotSupportedError`  [INFERRED]
  docs/architecture.md → services/orchestrator/src/voxora/crm/base.py
- `Consent/suppression sync honesty (Phase 3)` --references--> `NotSupportedError`  [INFERRED]
  docs/implementation-audit.md → services/orchestrator/src/voxora/crm/base.py

## Import Cycles
- None detected.

## Communities (116 total, 18 thin omitted)

### Community 0 - "test_voice_providers.py"
Cohesion: 0.07
Nodes (26): Task 3: STT/TTS routing table exposure (P7), voice_provider_routing_status(), adapter_from_spec(), adapter_id(), LanguageRoutingTable, normalize_language(), provider_status(), ProviderAdapter (+18 more)

### Community 1 - "media.rs"
Cohesion: 0.06
Nodes (44): assembler_closes_only_after_the_quiet_gap(), ChannelRing, closed_transport_surfaces_honestly(), decision_runs_exactly_once_for_one_utterance(), Encoding, Pcm16, Ulaw, end_to_end_scripted_media_exchange() (+36 more)

### Community 2 - "bridge.rs"
Cohesion: 0.05
Nodes (41): backend(), BridgeError, DialogNeverEnded, EmptyDialog, BridgeEvent, BridgeSummary, DialogBackend, empty_dialog_is_reported_honestly() (+33 more)

### Community 3 - "api.py"
Cohesion: 0.06
Nodes (32): AgentCreate, AiDecideRequest, AudienceConsent, AudienceContact, AudienceImport, AudioAssetGenerate, CampaignCreate, CampaignSyncRequest (+24 more)

### Community 4 - "test_session_budgets.py"
Cohesion: 0.08
Nodes (42): parse_iso(), utcnow(), EligibilityDecision, evaluate(), is_due(), next_backoff(), summarize(), _window_state() (+34 more)

### Community 5 - "iso"
Cohesion: 0.08
Nodes (11): M10 Audio Generation, Phases E-G (2026-11-20, verified this session), M1 — Make the simulation trustworthy at scale (✅ 2026-11-01), Item 1 — Multi-worker claiming safety (roadmap M4a), iso(), new_id(), assert_transition(), test_call_events_and_listing(), test_claim_skips_running_and_requeues_retryable() (+3 more)

### Community 6 - ".get_voice_session"
Cohesion: 0.07
Nodes (26): CheckpointFlusher, effective_flush_interval(), flush_checkpoint(), parse_snapshot(), recover_answers(), recover_session(), should_flush(), TranscriptCheckpointError (+18 more)

### Community 7 - "CampaignEngine"
Cohesion: 0.12
Nodes (5): CampaignEngine, CampaignError, decision_code(), CampaignWorker, test_cannot_start_empty_campaign()

### Community 8 - "Database"
Cohesion: 0.10
Nodes (7): Database, database_from_settings(), _percentile(), test_recover_inflight_requeues_stranded_batch_items(), test_crm_connection_and_mappings_roundtrip(), test_get_unset_tenant_returns_none(), test_seed_is_idempotent()

### Community 9 - "endpointing.rs"
Cohesion: 0.08
Nodes (27): AdaptiveEndpointer, confidence_upgrades_with_overshoot(), continuation_withdraws_candidate_and_does_not_close_the_turn(), EndpointConfig, EndpointDecision, Candidate, Continued, Speaking (+19 more)

### Community 10 - "vad.rs"
Cohesion: 0.10
Nodes (21): background_noise_tolerance_is_adaptive_not_cliffed(), energy_vad_separates_speech_from_silence(), EnergyVad, FRAME_20MS_16K, loud_frame(), per_call_reset_clears_floor_and_state(), quiet_noise_frame(), short_noise_is_rejected() (+13 more)

### Community 11 - "paths.ts"
Cohesion: 0.04
Nodes (50): AudioBatchItem, PageInfo, PATH_Agents, PATH_AiDecide, PATH_AudioAssets, PATH_AudioAssetsByAssetId(), PATH_AudioAssetsByAssetIdDownload(), PATH_AudioAssetsGenerate (+42 more)

### Community 12 - "controller.rs"
Cohesion: 0.14
Nodes (23): backchannel_acknowledgement_does_not_cancel_playback(), call_cfg(), code_switching_grants_silence_grace_not_a_boundary(), completed_utterance_reaches_turn_complete(), concurrent_calls_are_isolated(), confidence_between(), ControllerConfig, ControllerSnapshot (+15 more)

### Community 13 - "semantic.rs"
Cohesion: 0.08
Nodes (18): book_appointment_for_is_incomplete(), book_appointment_for_monday_is_complete(), CORRECTION_CUES, eval(), HeuristicSemanticEvaluator, language_is_passenger_not_decider(), punctuated_question_is_classified_as_answerable(), PURE_FILLERS (+10 more)

### Community 14 - "LiveTurnEngine"
Cohesion: 0.05
Nodes (41): M7 — Live turn loop + model routing (P1), Integration test: PyInterviewBot consumes Voxora (DONE), M7 + M8 Completion Plan (Voxora-Studio), Review fix pass (whole-branch review findings C1, I1–I6), Review Focus, Rulings (pre-registered), Session addendum (post-plan execution), Task 2: Live turn loop mounted for the media backend (M7) (+33 more)

### Community 15 - "CallRequest"
Cohesion: 0.08
Nodes (16): overview(), telephony_providers(), CallRequest, TelephonyAdapter, TelephonyEvent, TelephonyResult, _at(), MockTelephonyAdapter (+8 more)

### Community 16 - "settle_voice_session"
Cohesion: 0.07
Nodes (17): M5 — Make the integration contracts real (P0) — COMPLETE 2026-11-04, Global Constraints, Task 2: DB storage — voice_boost_settings table + session override column, build_event(), deliver_webhook(), dispatch_pending_webhooks(), dispatch_session_events(), dispatch_session_events_via_pyreverseetl() (+9 more)

### Community 17 - "create_app"
Cohesion: 0.07
Nodes (58): ApiError, create_app(), _adapter_for(), add_recipients(), add_suppression(), _batch_defaults(), cancel_call(), create_agent() (+50 more)

### Community 18 - "barge_in.rs"
Cohesion: 0.11
Nodes (20): acknowledgements_are_backchannels_playback_survives(), BargeInConfig, BargeInGate, below_threshold_frame_resets_consecutive_count(), candidate_speech_after_n_frames_triggers(), corrections_reframe_backchannel_tokens_as_genuine(), custom_lexicon_replaces_default(), DEFAULT_BACKCHANNELS (+12 more)

### Community 20 - "generate_long_form"
Cohesion: 0.15
Nodes (21): M10 Audio Generation, Phase D — long-form generation (2026-11-13, verified this session), Self-review notes, _acquire_run_lock(), _assemble_wav(), _check_runnable(), _chunk_entry(), _chunk_path(), _chunk_request() (+13 more)

### Community 21 - "test_openrouter.py"
Cohesion: 0.16
Nodes (16): build_agent(), _default_env(), make_client(), test_agent_brain_takes_over_turns_and_can_end(), respond(), test_default_transport_is_synchronous_client(), test_end_call_flag_and_local_end_keywords(), test_health_and_key_model_requirements() (+8 more)

### Community 22 - "test_voice_boost_settings.py"
Cohesion: 0.05
Nodes (41): Global Constraints, Review Focus, Task 1: Rust Voice Boost settings seam, Task 3: voice_boost.py — models, resolution, session intake plumbing, Task 4: API routes — GET/PUT settings + intake validation, Task 5: Python ↔ Rust seam test (wheel-built path), Task 6: Roadmap + audit doc update, Voice Boost settings surface Implementation Plan (+33 more)

### Community 23 - "PostgresDatabase"
Cohesion: 0.16
Nodes (4): _pg_url(), _PgConnection, PostgresDatabase, _translate_sqlite_sql()

### Community 24 - "td_events.rs"
Cohesion: 0.09
Nodes (18): age_never_goes_negative(), DetectionSource, BargeIn, Controller, Endpointing, Semantic, Vad, event_carries_schema_version_and_full_identity() (+10 more)

### Community 25 - "openrouter.py"
Cohesion: 0.12
Nodes (9): AgentReply, AgentTurnError, MockAgent, turn_from_reply(), create_agent_from_env(), _parse_reply(), _safe_float(), _strip_json() (+1 more)

### Community 26 - "turn.rs"
Cohesion: 0.14
Nodes (20): barge_in_allowed_after_guard(), barge_in_guard_blocks_early_interrupt(), full_conversation_lifecycle(), invalid_transitions_rejected(), InvalidSignal, many_turns_counted(), round_trip(), Signal (+12 more)

### Community 27 - "LeadConnectorCrmAdapter"
Cohesion: 0.21
Nodes (8): crm_providers(), CrmError, build_crm_adapter(), provider_catalog(), LeadConnectorCrmAdapter, _string(), test_capabilities_are_honest(), test_registry_builds_the_adapter()

### Community 28 - "package.json"
Cohesion: 0.09
Nodes (22): dependencies, react, react-dom, devDependencies, @types/react, @types/react-dom, typescript, vite (+14 more)

### Community 29 - "App.tsx"
Cohesion: 0.10
Nodes (24): AgentRow, CallRow, Campaign, CampaignRow, ContactRow, Envelope, TurnConfig, TurnEngineView (+16 more)

### Community 30 - "TurnDetectionEngine"
Cohesion: 0.05
Nodes (30): Development, Intelligent turn detection (Rust engine), Not implemented (read this), Packaging: pip and uv, Quick start (Docker), Quick start (local, no Docker), Repo layout, Voxora Studio (+22 more)

### Community 31 - "HttpResponse"
Cohesion: 0.14
Nodes (6): build_url(), HttpResponse, HttpTransport, UrllibTransport, _http_error(), _http_error()

### Community 33 - "Task 1: Model-profile dispatch + usage rows (P8)"
Cohesion: 0.10
Nodes (16): Task 1: Model-profile dispatch + usage rows (P8), builtin_model_profiles(), _configured_profile_names(), resolve_model_profile(), seed_model_profiles(), UnknownModelProfileError, _decide_payload(), _openrouter_client() (+8 more)

### Community 34 - "typing"
Cohesion: 0.16
Nodes (6): _seed(), test_agent_script_advances_across_requests(), test_instruction_spoken_once_across_requests(), test_openrouter_agent_state_carries_history(), test_voice_runtime_501_leaves_no_session(), _turn()

### Community 35 - "wav.rs"
Cohesion: 0.14
Nodes (18): custom_header_goofiness_detected(), duration_at_8khz(), encode_pcm16_mono(), inspect(), le_u16(), le_u32(), rejects_garbage(), rejects_non_pcm16() (+10 more)

### Community 36 - "tts_generation.py"
Cohesion: 0.11
Nodes (15): Task 5: Speech-analytics metadata (M8, P10), validate_audio_blob(), _mock_wav(), _read_parent_wav(), compute_speech_analytics(), _decode_wav(), _rms_per_window(), test_validate_compressed_blob_honestly_reports_no_duration() (+7 more)

### Community 37 - "test_twilio_adapter.py"
Cohesion: 0.16
Nodes (19): TwilioError, _adapter(), _engine_setup(), FakeClock, FakeTwilioTransport, _request(), test_429_retry_after_then_success(), test_busy_callback_requeues_recipient_for_retry() (+11 more)

### Community 38 - "dsp.rs"
Cohesion: 0.12
Nodes (14): bool_of(), config_from_settings_json(), DE_ESS_HZ, disabled_maps_to_bypass(), ENGINE_RATE, EnhancedFrame, LIMITER_LOOKAHEAD, NN_RATE (+6 more)

### Community 39 - "VoiceBoostStage"
Cohesion: 0.19
Nodes (3): EchoCanceller, LookAheadLimiter, VoiceBoostStage

### Community 40 - "pytest"
Cohesion: 0.13
Nodes (13): M3 — Real voice (the hard part), _at(), extension(), telephony_result_from_runtime_json(), VoiceRuntimeTelephonyAdapter, test_campaign_lifecycle_transitions_are_explicit(), _canonical_payload(), _request() (+5 more)

### Community 41 - "_error_body"
Cohesion: 0.12
Nodes (16): _connection_view(), ai_decide(), api_error_handler(), create_connection(), get_connection(), http_error_handler(), list_connections(), squid_webhook() (+8 more)

### Community 42 - "CanonicalContact"
Cohesion: 0.10
Nodes (19): CanonicalContact, CrmAdapter, NotSupportedError, WebhookVerification, sync_connection(), _contact(), InMemoryCrmAdapter, _records() (+11 more)

### Community 43 - "test_api.py"
Cohesion: 0.11
Nodes (15): _create_campaign(), _decide_payload(), _health(), _openrouter_client(), test_ai_decide_mock_path_is_deterministic_and_audited(), test_ai_decide_openrouter_success_records_provider_and_usage(), test_ai_decide_openrouter_unavailable_is_503(), test_ai_decide_openrouter_unconfigured_is_501() (+7 more)

### Community 44 - "json"
Cohesion: 0.06
Nodes (39): build_app(), _clean_null_anyof(), generate_paths(), main(), path_constant(), ts_object(), ts_type(), JsonFormatter (+31 more)

### Community 45 - "SquidCrmAdapter"
Cohesion: 0.11
Nodes (12): ConnectionTest, ContactPage, CrmCapabilities, _path(), SquidCrmAdapter, _string(), process_squid_event(), test_sync_uses_adapter_and_imports_contacts() (+4 more)

### Community 46 - "test_voice_sessions.py"
Cohesion: 0.25
Nodes (12): _grant_consent(), _seed_agent(), _session_payload(), test_consented_client_is_callable_after_audience_import_never_widens(), test_instructions_are_recorded_and_rejected_with_provider_capabilities(), test_session_creates_call_completes_simulated_and_records_envelope_events(), test_session_intake_is_idempotent_on_external_id(), test_terminate_is_idempotent_and_sessions_events_stay_ordered() (+4 more)

### Community 47 - "apiTypes.ts"
Cohesion: 0.05
Nodes (35): AgentCreate, AiDecideRequest, AudienceConsent, AudienceContact, AudienceImport, Body_create_audio_batch_api_v1_audio_batches_post, Body_preview_audio_batch_api_v1_audio_batches_preview_post, CampaignCreate (+27 more)

### Community 48 - ".create_agent"
Cohesion: 0.11
Nodes (13): seed_demo(), test_metrics_counters_reflect_domain_rows(), test_engine_usage_persists_across_engines(), _campaign_with_one_recipient(), test_campaign_lifecycle_claims_and_completion(), test_concurrent_workers_claim_disjoint_recipients(), _second_worker(), _worker() (+5 more)

### Community 49 - "build_tts_provider"
Cohesion: 0.10
Nodes (19): Task 2: TTS provider abstraction — mock + OpenAI, audio_provider_status(), build_tts_provider(), OpenAITTSProvider, TTSProvider, TTSRequest, TTSResult, _FakeResponse (+11 more)

### Community 51 - "test_campaign_engine.py"
Cohesion: 0.27
Nodes (9): _db(), FakeClock, _result(), _setup(), test_dry_run_campaign_completes_and_records_simulated_calls(), test_idempotency_keys_are_unique_per_attempt(), test_policy_blocked_recipients_never_dial(), test_restart_recovery_requeues_inflight() (+1 more)

### Community 52 - "compilerOptions"
Cohesion: 0.12
Nodes (15): compilerOptions, esModuleInterop, forceConsistentCasingInFileNames, jsx, lib, module, moduleResolution, noEmit (+7 more)

### Community 53 - "TurnEventType"
Cohesion: 0.12
Nodes (14): TurnEventType, EndpointCandidate, InterruptionDetected, ProviderFailed, Recovered, Rejected, SpeechEnded, SpeechStarted (+6 more)

### Community 54 - "VoiceBoostConfig"
Cohesion: 0.17
Nodes (8): config_round_trips_through_serde(), limiter_bounds_peak_under_overload(), settings_json_applies_profile_and_overrides(), VoiceBoostConfig, VoiceBoostProfile, Clear, Natural, Studio

### Community 55 - "conftest.py"
Cohesion: 0.16
Nodes (7): Postgres storage + compat suite (Phase 2), client(), database(), fake_pyreverseetl_transport(), _pg_reachable(), settings(), voice_runtime_client()

### Community 56 - "AgentBrain"
Cohesion: 0.12
Nodes (11): A. Squid CRM → Voxora (audience), ADR-0001 — Ownership boundaries (non-negotiable), ADR-0002 — Independence and the no-source-import rule (supersedes any in-process-import idea), ADR-0003 — Integration contracts (v1, all versioned envelopes), ADR-0004 — Migration discipline, ADR-0005 — Voxora internal modules to introduce (Phase 3), B. Voxora → PyInterviewBot (voice sessions), C. Voxora events (versioned webhooks out) (+3 more)

### Community 57 - "OpenRouterLLM"
Cohesion: 0.19
Nodes (5): _env_int(), OpenRouterError, OpenRouterLLM, test_llm_decide_round_trip_pins_route_contract(), explode()

### Community 58 - "test_twilio_webhooks.py"
Cohesion: 0.29
Nodes (10): _configured(), _create_conversation(), _signed_form(), test_async_provider_leaves_calls_in_flight_not_fake_completed(), test_recording_callback_for_unknown_call_is_404(), test_recording_callback_stores_pointer_and_emits_recording_ready(), test_status_callback_settles_call_end_to_end(), test_status_callback_unknown_call_sid_is_404() (+2 more)

### Community 59 - "Biquad"
Cohesion: 0.19
Nodes (8): Biquad, clipping_detector_counts_rail_samples(), de_esser_attenuates_sibilance_only(), DeEsser, lowcut_removes_dc_component(), media_bridge_with_voice_boost_still_completes_turns(), presence_eq_boosts_speech_band_but_not_bass(), sine()

### Community 60 - "AudioFileStore"
Cohesion: 0.25
Nodes (6): delete_audio_asset(), download_audio_asset(), AudioFileStore, test_download_headers_sanitize_hostile_titles(), test_store_delete_refuses_escape_from_root(), test_store_paths_stay_under_root()

### Community 61 - "test_ghl_adapter.py"
Cohesion: 0.29
Nodes (8): _adapter(), FakeLeadConnectorTransport, test_connection_success_and_auth_failure(), test_connection_test_reports_bad_location(), test_get_contact_and_suppression_states(), test_list_contacts_maps_and_pages_by_id_cursor(), test_requests_carry_auth_and_version_header(), test_sync_connection_applies_ghl_suppression()

### Community 62 - "AudioGenerationError"
Cohesion: 0.17
Nodes (8): Audio Generation Phase A–B Implementation Plan, Global Constraints, Review Focus, Task 1: audio_assets table + Settings storage path, Task 3: audio_assets.py — file store + validation, Task 4: API routes, Task 5: roadmap + audit docs, AudioGenerationError

### Community 63 - "test_squid_adapter.py"
Cohesion: 0.29
Nodes (10): adapter(), FakeSquidTransport, test_capabilities_declared(), test_create_contact_sends_idempotency_key(), test_field_discovery_marks_unsupported_capabilities(), test_list_contacts_maps_verified_person_shape(), test_rate_limit_respects_retry_after_header(), test_retry_exhaustion_raises_crm_error() (+2 more)

### Community 64 - "TurnDetectionState"
Cohesion: 0.14
Nodes (13): TurnDetectionState, AiSpeaking, AiThinking, Ended, Idle, Interrupted, InterruptionCandidate, InTurn (+5 more)

### Community 65 - "Phase 1–3 update (2026-11-01, verified this session)"
Cohesion: 0.18
Nodes (11): CI on GitHub (Phase 1), Consent/suppression sync honesty (Phase 3), Console generated client + postgres compose (Phase 2), Not done / unchanged (still honest), Phase 1–3 update (2026-11-01, verified this session), Request-model tightening (Phase 3), Scheduling (Phase 2), Twilio telephony adapter (Phase 5, verified 2026-11-01) (+3 more)

### Community 66 - "fastapi_testclient"
Cohesion: 0.20
Nodes (9): test_delete_removes_asset_and_file(), test_generate_and_download_asset_roundtrip(), test_generation_rejects_bad_script_and_speed(), test_list_audio_assets_empty(), test_provider_endpoint_is_capability_honest(), test_say_route_stays_unimplemented_p9_boundary(), _settings(), test_metrics_exposes_expected_families() (+1 more)

### Community 67 - "Ecosystem Migration — Capability Inventory (Phase 1)"
Cohesion: 0.29
Nodes (6): 1. MudFish (only upstream crawler; keep its lane), 2. QuillSift (only document/knowledge processing; keep its lane), 3. Squid CRM (owns contacts, segmentation, audiences, eligibility records; never executes calls), 4. Voxora Studio (owns telephony, voice agents, OpenRouter, call state, campaigns; never interview methodology), 5. PyInterviewBot (owns interview methodology only; consumes Voxora, never telephony/LLM-provider infra), Ecosystem Migration — Capability Inventory (Phase 1)

### Community 68 - "Roadmap"
Cohesion: 0.20
Nodes (10): Explicit non-goals (patterns rejected from competitor adoption), M0 — Local, simulation-only showcase (current build ✅), M10 — Audio Generation Studio (the PRODUCE half), M2 — Real CRM breadth (Squid first, then one more), M4 — Production hygiene, M6 — Real-time media bridge (P1, extends M3's open media item), M8 — Integrity signals (P2), M9 + M10 — one mission: the complete voice pipeline (+2 more)

### Community 69 - "_turn_engine_or_404"
Cohesion: 0.47
Nodes (10): feed_turn_agent_event(), feed_turn_frame(), feed_turn_lid(), feed_turn_provider_failure(), feed_turn_provider_recovery(), feed_turn_semantic(), feed_turn_transcript(), _turn_engine_or_404() (+2 more)

### Community 70 - ".process"
Cohesion: 0.29
Nodes (14): Agc, agc_boosts_quiet_speech_to_target_without_clipping(), bypass_preserves_bytes_exactly(), chunk_split_processing_is_deterministic(), empty_input_is_safe(), i16_of(), lcg_noise(), nnnoiseless_preserves_clean_speech_tone() (+6 more)

### Community 72 - "Implementation Audit (honest verification record)"
Cohesion: 0.25
Nodes (8): Assumptions made (not verified), Environment (verified), Honest gaps vs. master prompt, Implementation Audit (honest verification record), M10 Audio Generation, Phase C — non-destructive variants (2026-11-13, verified this session), M7 + M8 completion (2026-11-12, verified this session), M9 Voice Boost settings surface (2026-11-13, verified this session), What is NOT verified / NOT present (explicit)

### Community 73 - "audio_batches.py"
Cohesion: 0.07
Nodes (32): Audio Generation Studio (M10), AudioBatchEngine, AudioBatchWorker, batch_report_csv(), BatchCsvError, BatchRateLimiter, parse_batch_csv(), _stub_engine() (+24 more)

### Community 76 - "Settings"
Cohesion: 0.18
Nodes (24): Settings, create_variant(), test_settings_defaults_keep_constructor_compatible(), _make_parent(), _segments_blob(), settings(), _sine_blob(), test_compressed_parent_refused() (+16 more)

### Community 77 - "test_variants_api.py"
Cohesion: 0.28
Nodes (9): _parent_asset(), test_variant_engine_unavailable_is_503(), test_variant_out_of_range_target_is_422(), test_variant_requires_writer_role(), test_variant_roundtrip_lists_and_preserves_parent(), test_variant_seven_ops_is_422(), test_variant_stereo_and_voice_boost_combo_is_422(), test_variant_unknown_op_is_422() (+1 more)

### Community 78 - "test_longform.py"
Cohesion: 0.14
Nodes (29): chunk_script(), MockTTSProvider, actor_db(), _chunk_entries(), FailOnceProvider, _mock_frames(), _para(), _payload() (+21 more)

### Community 79 - "test_audience_import.py"
Cohesion: 0.47
Nodes (8): _import_payload(), _seed(), test_global_suppression_blocks_contact_with_recorded_rejection(), test_import_creates_campaign_with_provenance_and_preserves_squid_ids(), test_invalid_phone_and_garbage_are_rejected_with_validation_errors(), test_missing_consent_is_recorded_unknown_and_not_silently_widened(), test_reimport_is_idempotent_no_duplicate_members(), test_voice_consent_refusal_marks_suppressed_and_reports_reasons()

### Community 80 - "SemanticVerdict"
Cohesion: 0.25
Nodes (8): PendingBoundary, SemanticReport, SemanticVerdict, Complete, Correction, Filler, Incomplete, Question

### Community 81 - "Voxora Studio — Architecture"
Cohesion: 0.18
Nodes (10): AI runtime (Python), Console (React), CRM integration — Squid (first integration, verified against source), Enforcement of honesty, Goal, Layout, Orchestrator (FastAPI), Voice runtime (Rust) (+2 more)

### Community 82 - "Risks"
Cohesion: 0.25
Nodes (7): Assumptions made and why, Compliance / legal (highest), Open questions, Project / honest-reporting risks, Risks, Risks, Assumptions, and Open Questions, Technical

### Community 83 - "generate_asset"
Cohesion: 0.09
Nodes (28): Audio Generation Phase D (Long-form) Implementation Plan, Global Constraints, Review Focus, Task 1: Chunker (`longform.py` part 1), Task 2: Run engine — generate chunks, assemble, state machine (`longform.py` part 2), Task 3: API routes + models + row lifecycle, Task 4: Docs + roadmap, A. Chunk manifest on the asset row + chunk files + assembly (CHOSEN) (+20 more)

### Community 84 - "Candidates"
Cohesion: 0.25
Nodes (7): 1. WebRTC Audio Processing Module (via `webrtc-audio-processing` 2.1.0), 2. RNNoise (via `nnnoiseless` 0.5.2), 3. DeepFilterNet3 (via `deep-filter-rt` 0.1.1), 4. SpeexDSP (`speexdsp` rust-av bindings / `aec-rs` vendored C), Candidates, DSP engine evaluation (Voice Boost, M9), Selection

### Community 85 - "AudioGeneration.tsx"
Cohesion: 0.11
Nodes (26): AudioAsset, AudioBatchDefaults, AudioBatchJob, AudioBatchPreview, AudioProviderStatus, AudioGeneration(), AudioView, batchDefaults() (+18 more)

### Community 86 - "What actually runs (executed commands, this session)"
Cohesion: 0.33
Nodes (6): AI runtime, Console (React + Vite + TS), Docker Compose (executed after the first audit pass), Orchestrator (FastAPI, SQLite), Voice runtime (Rust), What actually runs (executed commands, this session)

### Community 87 - "downsampler_preserves_bandlimited_signal_rms"
Cohesion: 0.24
Nodes (5): Downsampler3, downsampler_preserves_bandlimited_signal_rms(), hermite(), Upsampler3, upsampler_emits_three_outputs_per_input_after_flush()

### Community 88 - "postprocess.rs"
Cohesion: 0.12
Nodes (35): count_clipped(), audio_postprocess_impl(), empty_ops_returns_input_with_diagnostics(), envelope(), normalize_op(), normalize_raises_quiet_signal_to_target_without_new_clipping(), normalize_reduces_gain_when_target_would_clip(), normalize_within_api_range_never_clips() (+27 more)

### Community 90 - "TTSProviderError"
Cohesion: 0.17
Nodes (4): TTSProviderError, test_generate_asset_provider_failure_leaves_no_partial_state(), generate(), StubFailOnce

### Community 92 - "test_longform_api.py"
Cohesion: 0.22
Nodes (15): APIFailOnceProvider, _fail_payload(), _generate_long(), test_delete_longform_cleans_chunk_files(), test_download_processing_asset_is_409_still_processing(), test_generate_long_caps(), test_generate_long_compressed_format_422(), test_generate_long_happy_path_ready_and_downloadable() (+7 more)

### Community 93 - "voxora-voice-runtime"
Cohesion: 0.50
Nodes (3): Contract notes, Modules, voxora-voice-runtime

### Community 94 - "AgentEvent"
Cohesion: 0.50
Nodes (4): AgentEvent, ThinkingStarted, TtsEnded, TtsStarted

### Community 95 - "config.py"
Cohesion: 0.18
Nodes (8): create_app_from_env(), _bool(), _float(), _int(), _json_object_env(), TenantPrincipal, main(), configure_logging()

### Community 97 - "voxora-studio"
Cohesion: 0.67
Nodes (3): voxora-ai-runtime, voxora-orchestrator, voxora-studio

### Community 98 - "variants.py"
Cohesion: 0.18
Nodes (7): _apply_rust_op(), _convert_channels(), _frames_to_i16(), _i16_to_frames(), load_extension(), _trim_silence(), _voice_boost_settings_json()

### Community 101 - ".ensure_tenant"
Cohesion: 0.18
Nodes (13): _asset_values(), test_assets_are_tenant_scoped(), test_count_by_status(), test_create_get_roundtrip_decodes_history(), test_delete_returns_true_then_missing(), test_list_paging_and_title_search(), test_update_title_and_status(), test_agent_crud_scoped() (+5 more)

### Community 108 - "model_validator"
Cohesion: 0.14
Nodes (4): AudioBatchDefaults, AudioBatchRecord, LongFormGenerate, VariantOpsRequest

### Community 111 - "_campaign_view"
Cohesion: 0.33
Nodes (9): _campaign_view(), cancel_campaign(), create_campaign(), get_campaign(), pause_campaign(), resume_campaign(), schedule_campaign(), start_campaign() (+1 more)

### Community 112 - "Review Focus"
Cohesion: 0.22
Nodes (8): Audio Generation Phase C (Variants) Implementation Plan, Global Constraints, Review Focus, Self-review notes, Task 1: Rust offline windowed-sinc resampler, Task 2: Rust `audio_postprocess` pyfunction (normalize, voice_boost, dispatch), Task 3: Python `variants.py` — WAV I/O, trim/channels, variant creation, Task 5: Docs + roadmap + ledger

### Community 113 - "_page"
Cohesion: 0.18
Nodes (10): M10 Audio Generation, Phases A-B (2026-11-13, verified this session), Task 4: Models + API routes + DB listing, list_audio_assets(), list_audio_batches(), list_audio_variants(), list_calls(), list_campaigns(), list_contacts() (+2 more)

### Community 114 - "Audio Generation Studio (M10 Phase A–B) — design"
Cohesion: 0.33
Nodes (5): Approaches considered, Audio Generation Studio (M10 Phase A–B) — design, Binding decisions, Intent, Left to the builder

### Community 115 - "Voice Boost settings surface — design (small)"
Cohesion: 0.33
Nodes (5): Approaches considered, Binding decisions, Intent, Left to the builder, Voice Boost settings surface — design (small)

### Community 119 - "Near-term production hygiene — design (small)"
Cohesion: 0.50
Nodes (3): Item 2 — Prometheus metrics endpoint (roadmap M4c), Item 3 — Second CRM adapter (roadmap M2), Near-term production hygiene — design (small)

## Knowledge Gaps
- **86 isolated node(s):** `@types/react`, `@types/react-dom`, `typescript`, `voxora-voice-runtime`, `voxora_voice_runtime` (+81 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 896 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **18 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `M9 Voice Boost settings surface (2026-11-13, verified this session)` connect `Implementation Audit (honest verification record)` to `dsp.rs`, `test_voice_boost_settings.py`?**
  _High betweenness centrality (0.306) - this node is a cross-community bridge._
- **Are the 139 inferred relationships involving `Database` (e.g. with `M1 — Make the simulation trustworthy at scale (✅ 2026-11-01)` and `Global Constraints`) actually correct?**
  _`Database` has 139 INFERRED edges - model-reasoned connections that need verification._
- **What connects `@types/react`, `@types/react-dom`, `typescript` to the rest of the system?**
  _86 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_voice_providers.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06656426011264721 - nodes in this community are weakly interconnected._
- **Why does `config_from_settings_json()` connect `dsp.rs` to `postprocess.rs`, `Implementation Audit (honest verification record)`, `VoiceBoostConfig`?**
  _High betweenness centrality (0.306) - this node is a cross-community bridge._
- **Are the 50 inferred relationships involving `create_app()` (e.g. with `build_app()` and `OpenRouterError`) actually correct?**
  _`create_app()` has 50 INFERRED edges - model-reasoned connections that need verification._
- **Should `media.rs` be split into smaller, more focused modules?**
  _Cohesion score 0.05894736842105263 - nodes in this community are weakly interconnected._