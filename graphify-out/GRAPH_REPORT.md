# Graph Report - Voxora-Studio  (2026-11-12)

## Corpus Check
- 108 files · ~98,484 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 10 file(s) not represented in the graph (top: (none) 4, .typed 2, .example 1)

## Summary
- 2293 nodes · 5868 edges · 101 communities (82 shown, 19 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 765 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `1773219f`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- test_voice_providers.py
- media.rs
- bridge.rs
- api.py
- db.py
- iso
- .get_voice_session
- CampaignEngine
- Database
- endpointing.rs
- vad.rs
- api.ts
- controller.rs
- semantic.rs
- LiveTurnEngine
- typing
- voice_sessions.py
- create_app
- barge_in.rs
- TurnDetectionController
- eligibility.py
- test_openrouter.py
- test_restart_recovery_terminates_voice_session
- test_sync_uses_adapter_and_imports_contacts
- td_events.rs
- openrouter.py
- turn.rs
- LeadConnectorCrmAdapter
- package.json
- App.tsx
- TurnDetectionEngine
- leadconnector.py
- ConsoleApi
- Task 1: Model-profile dispatch + usage rows (P8)
- logging
- wav.rs
- settle_voice_session
- test_twilio_adapter.py
- dsp.rs
- VoiceBoostStage
- pytest
- dispatch_pending_webhooks
- CanonicalContact
- test_api.py
- security.py
- SquidCrmAdapter
- test_voice_sessions.py
- apiTypes.ts
- .create_voice_session
- test_voice_turn_detection.py
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
- SecretBox
- test_ghl_adapter.py
- voice_turn_detection.py
- HttpResponse
- TurnDetectionState
- .initialize
- fastapi_testclient
- Ecosystem Migration — Capability Inventory (Phase 1)
- generate_ts_client.py
- json
- .process
- test_engine.py
- Implementation Audit (honest verification record)
- Phase 1–3 update (2026-11-01, verified this session)
- prometheus_metrics
- CheckpointQueue
- _openrouter_client
- test_audience_import.py
- redact
- SemanticVerdict
- Voxora Studio — Architecture
- Risks
- Roadmap
- Candidates
- _FakePyReverseETL
- downsampler_preserves_bandlimited_signal_rms
- Voxora Studio
- What actually runs (executed commands, this session)
- voxora-voice-runtime
- AgentEvent
- Settings
- voxora-studio
- voxora-voice-runtime
- voxora_voice_runtime

## God Nodes (most connected - your core abstractions)
1. `Database` - 199 edges
2. `create_app()` - 156 edges
3. `iso()` - 59 edges
4. `ApiError` - 44 edges
5. `TurnDetectionController` - 42 edges
6. `CampaignEngine` - 36 edges
7. `SquidCrmAdapter` - 36 edges
8. `Settings` - 35 edges
9. `LiveTurnEngine` - 31 edges
10. `ConsoleApi` - 29 edges

## Surprising Connections (you probably didn't know these)
- `AI runtime (Python)` --references--> `MockAgent`  [INFERRED]
  docs/architecture.md → services/ai-runtime/src/voxora_ai/engine.py
- `CRM integration — Squid (first integration, verified against source)` --references--> `NotSupportedError`  [INFERRED]
  docs/architecture.md → services/orchestrator/src/voxora/crm/base.py
- `Consent/suppression sync honesty (Phase 3)` --references--> `NotSupportedError`  [INFERRED]
  docs/implementation-audit.md → services/orchestrator/src/voxora/crm/base.py
- `Voice-runtime <-> orchestrator seam (Phase 4, verified 2026-11-01)` --references--> `TelephonyResult`  [INFERRED]
  docs/implementation-audit.md → services/orchestrator/src/voxora/telephony/base.py
- `M7 + M8 completion (2026-11-12, verified this session)` --references--> `LanguageRoutingTable`  [INFERRED]
  docs/implementation-audit.md → services/orchestrator/src/voxora/voice_providers.py

## Import Cycles
- None detected.

## Communities (101 total, 19 thin omitted)

### Community 0 - "test_voice_providers.py"
Cohesion: 0.06
Nodes (29): Task 3: STT/TTS routing table exposure (P7), adapter_from_spec(), adapter_id(), LanguageRoutingTable, normalize_language(), provider_status(), ProviderAdapter, routing_table_from_settings() (+21 more)

### Community 1 - "media.rs"
Cohesion: 0.06
Nodes (44): assembler_closes_only_after_the_quiet_gap(), ChannelRing, closed_transport_surfaces_honestly(), decision_runs_exactly_once_for_one_utterance(), Encoding, Pcm16, Ulaw, end_to_end_scripted_media_exchange() (+36 more)

### Community 2 - "bridge.rs"
Cohesion: 0.06
Nodes (38): backend(), BridgeError, DialogNeverEnded, EmptyDialog, BridgeEvent, BridgeSummary, DialogBackend, empty_dialog_is_reported_honestly() (+30 more)

### Community 3 - "api.py"
Cohesion: 0.05
Nodes (32): delete_suppression(), AgentCreate, AiDecideRequest, AudienceConsent, AudienceContact, AudienceImport, CampaignCreate, CampaignSyncRequest (+24 more)

### Community 4 - "db.py"
Cohesion: 0.06
Nodes (34): Item 1 — Multi-worker claiming safety (roadmap M4a), Item 2 — Prometheus metrics endpoint (roadmap M4c), Item 3 — Second CRM adapter (roadmap M2), Near-term production hygiene — design (small), parse_iso(), utcnow(), _pg_url(), _PgConnection (+26 more)

### Community 5 - "iso"
Cohesion: 0.10
Nodes (20): iso(), new_id(), seed_demo(), test_metrics_counters_reflect_domain_rows(), _campaign_with_one_recipient(), test_agent_crud_scoped(), test_audit_rows_scoped_and_sorted(), test_campaign_lifecycle_claims_and_completion() (+12 more)

### Community 6 - ".get_voice_session"
Cohesion: 0.07
Nodes (21): CheckpointFlusher, effective_flush_interval(), flush_checkpoint(), parse_snapshot(), recover_answers(), recover_session(), should_flush(), TranscriptCheckpointError (+13 more)

### Community 7 - "CampaignEngine"
Cohesion: 0.13
Nodes (3): CampaignEngine, decision_code(), CampaignWorker

### Community 8 - "Database"
Cohesion: 0.12
Nodes (4): Database, database_from_settings(), _percentile(), test_crm_connection_and_mappings_roundtrip()

### Community 9 - "endpointing.rs"
Cohesion: 0.08
Nodes (27): AdaptiveEndpointer, confidence_upgrades_with_overshoot(), continuation_withdraws_candidate_and_does_not_close_the_turn(), EndpointConfig, EndpointDecision, Candidate, Continued, Speaking (+19 more)

### Community 10 - "vad.rs"
Cohesion: 0.10
Nodes (21): background_noise_tolerance_is_adaptive_not_cliffed(), energy_vad_separates_speech_from_silence(), EnergyVad, FRAME_20MS_16K, loud_frame(), per_call_reset_clears_floor_and_state(), quiet_noise_frame(), short_noise_is_rejected() (+13 more)

### Community 11 - "api.ts"
Cohesion: 0.07
Nodes (28): CallRow, Campaign, CampaignRow, ContactRow, PageInfo, TurnEngineView, PATH_Agents, PATH_Audit (+20 more)

### Community 12 - "controller.rs"
Cohesion: 0.14
Nodes (23): backchannel_acknowledgement_does_not_cancel_playback(), call_cfg(), code_switching_grants_silence_grace_not_a_boundary(), completed_utterance_reaches_turn_complete(), concurrent_calls_are_isolated(), confidence_between(), ControllerConfig, ControllerSnapshot (+15 more)

### Community 13 - "semantic.rs"
Cohesion: 0.08
Nodes (18): book_appointment_for_is_incomplete(), book_appointment_for_monday_is_complete(), CORRECTION_CUES, eval(), HeuristicSemanticEvaluator, language_is_passenger_not_decider(), punctuated_question_is_classified_as_answerable(), PURE_FILLERS (+10 more)

### Community 14 - "LiveTurnEngine"
Cohesion: 0.06
Nodes (37): M7 — Live turn loop + model routing (P1), Integration test: PyInterviewBot consumes Voxora (DONE), M7 + M8 Completion Plan (Voxora-Studio), Review fix pass (whole-branch review findings C1, I1–I6), Review Focus, Rulings (pre-registered), Session addendum (post-plan execution), Task 2: Live turn loop mounted for the media backend (M7) (+29 more)

### Community 15 - "typing"
Cohesion: 0.08
Nodes (16): overview(), telephony_providers(), CallRequest, TelephonyAdapter, TelephonyEvent, TelephonyResult, _at(), MockTelephonyAdapter (+8 more)

### Community 16 - "voice_sessions.py"
Cohesion: 0.11
Nodes (11): build_event(), deliver_webhook(), dispatch_session_events_via_pyreverseetl(), _event_envelope(), _FakeSource, signed_envelope_body(), VoiceSessionError, _seed_completed_session() (+3 more)

### Community 17 - "create_app"
Cohesion: 0.07
Nodes (64): ApiError, _connection_view(), create_app(), _adapter_for(), add_recipients(), add_suppression(), _campaign_view(), cancel_call() (+56 more)

### Community 18 - "barge_in.rs"
Cohesion: 0.11
Nodes (20): acknowledgements_are_backchannels_playback_survives(), BargeInConfig, BargeInGate, below_threshold_frame_resets_consecutive_count(), candidate_speech_after_n_frames_triggers(), corrections_reframe_backchannel_tokens_as_genuine(), custom_lexicon_replaces_default(), DEFAULT_BACKCHANNELS (+12 more)

### Community 20 - "eligibility.py"
Cohesion: 0.23
Nodes (16): EligibilityDecision, evaluate(), is_due(), next_backoff(), summarize(), _window_state(), _campaign(), _contact() (+8 more)

### Community 21 - "test_openrouter.py"
Cohesion: 0.16
Nodes (16): build_agent(), _default_env(), make_client(), test_agent_brain_takes_over_turns_and_can_end(), respond(), test_default_transport_is_synchronous_client(), test_end_call_flag_and_local_end_keywords(), test_health_and_key_model_requirements() (+8 more)

### Community 22 - "test_restart_recovery_terminates_voice_session"
Cohesion: 0.13
Nodes (7): Orchestrator (FastAPI), assert_transition(), InvalidTransition, test_restart_recovery_terminates_voice_session(), test_campaign_lifecycle_transitions_are_explicit(), test_call_events_and_listing(), test_recover_inflight_marks_stale_calls_failed()

### Community 23 - "test_sync_uses_adapter_and_imports_contacts"
Cohesion: 0.22
Nodes (6): ConnectionTest, test_sync_uses_adapter_and_imports_contacts(), capabilities(), describe_fields(), list_contacts(), test_connection()

### Community 24 - "td_events.rs"
Cohesion: 0.09
Nodes (18): age_never_goes_negative(), DetectionSource, BargeIn, Controller, Endpointing, Semantic, Vad, event_carries_schema_version_and_full_identity() (+10 more)

### Community 25 - "openrouter.py"
Cohesion: 0.12
Nodes (9): AgentReply, AgentTurnError, MockAgent, turn_from_reply(), create_agent_from_env(), _parse_reply(), _safe_float(), _strip_json() (+1 more)

### Community 26 - "turn.rs"
Cohesion: 0.15
Nodes (20): barge_in_allowed_after_guard(), barge_in_guard_blocks_early_interrupt(), full_conversation_lifecycle(), invalid_transitions_rejected(), InvalidSignal, many_turns_counted(), round_trip(), Signal (+12 more)

### Community 27 - "LeadConnectorCrmAdapter"
Cohesion: 0.20
Nodes (9): crm_providers(), CrmError, build_crm_adapter(), provider_catalog(), _http_error(), LeadConnectorCrmAdapter, _string(), test_capabilities_are_honest() (+1 more)

### Community 28 - "package.json"
Cohesion: 0.09
Nodes (22): dependencies, react, react-dom, devDependencies, @types/react, @types/react-dom, typescript, vite (+14 more)

### Community 29 - "App.tsx"
Cohesion: 0.12
Nodes (20): AgentRow, ApiError, Envelope, TurnConfig, TurnEvent, TurnFeedResult, ACTIONS, App() (+12 more)

### Community 31 - "leadconnector.py"
Cohesion: 0.10
Nodes (5): CrmCapabilities, FieldInfo, build_url(), HttpTransport, UrllibTransport

### Community 33 - "Task 1: Model-profile dispatch + usage rows (P8)"
Cohesion: 0.12
Nodes (15): Task 1: Model-profile dispatch + usage rows (P8), builtin_model_profiles(), _configured_profile_names(), resolve_model_profile(), seed_model_profiles(), UnknownModelProfileError, _decide_payload(), _openrouter_client() (+7 more)

### Community 34 - "logging"
Cohesion: 0.17
Nodes (7): main(), configure_logging(), JsonFormatter, _format_with(), test_standard_extra_fields_are_promoted(), test_standard_logrecord_attributes_are_not_leaked(), test_underscore_prefixed_extras_keep_legacy_promotion()

### Community 35 - "wav.rs"
Cohesion: 0.14
Nodes (18): custom_header_goofiness_detected(), duration_at_8khz(), encode_pcm16_mono(), inspect(), le_u16(), le_u32(), rejects_garbage(), rejects_non_pcm16() (+10 more)

### Community 36 - "settle_voice_session"
Cohesion: 0.13
Nodes (13): Global Constraints, Task 5: Speech-analytics metadata (M8, P10), compute_speech_analytics(), _decode_wav(), _rms_per_window(), settle_voice_session(), _FakeSettings, test_no_audio_omits_speech_analytics() (+5 more)

### Community 37 - "test_twilio_adapter.py"
Cohesion: 0.16
Nodes (19): TwilioError, _adapter(), _engine_setup(), FakeClock, FakeTwilioTransport, _request(), test_429_retry_after_then_success(), test_busy_callback_requeues_recipient_for_retry() (+11 more)

### Community 38 - "dsp.rs"
Cohesion: 0.10
Nodes (10): count_clipped(), DE_ESS_HZ, echo_seam_is_honest_by_default(), ENGINE_RATE, EnhancedFrame, LIMITER_LOOKAHEAD, NN_RATE, NullEchoCanceller (+2 more)

### Community 39 - "VoiceBoostStage"
Cohesion: 0.17
Nodes (3): EchoCanceller, LookAheadLimiter, VoiceBoostStage

### Community 40 - "pytest"
Cohesion: 0.14
Nodes (11): _at(), extension(), telephony_result_from_runtime_json(), VoiceRuntimeTelephonyAdapter, _canonical_payload(), _request(), test_adapter_idempotent_on_idempotency_key(), test_live_calls_refused_for_simulation_providers() (+3 more)

### Community 41 - "dispatch_pending_webhooks"
Cohesion: 0.09
Nodes (22): ai_decide(), api_error_handler(), create_voice_session(), get_voice_session(), http_error_handler(), post_session_instruction(), run_voice_session_turn(), squid_webhook() (+14 more)

### Community 42 - "CanonicalContact"
Cohesion: 0.11
Nodes (18): CanonicalContact, CrmAdapter, NotSupportedError, sync_connection(), _contact(), InMemoryCrmAdapter, _records(), test_contract_capabilities_honestly_disable_unsupported_ops() (+10 more)

### Community 43 - "test_api.py"
Cohesion: 0.12
Nodes (9): _create_campaign(), _health(), test_ai_decide_mock_path_is_deterministic_and_audited(), test_ai_decide_rejects_non_writer(), test_audit_log_records_promotions(), test_campaign_lifecycle_runs_dry_run_end_to_end(), test_campaign_recipient_validation_reports_blocks(), test_health_is_public_and_honest_about_simulation() (+1 more)

### Community 44 - "security.py"
Cohesion: 0.24
Nodes (9): parse_squid_webhook(), safe_equal(), squid_signature(), twilio_signature(), utc_token(), VerifiedWebhook, verify_squid_signature(), verify_twilio_signature() (+1 more)

### Community 45 - "SquidCrmAdapter"
Cohesion: 0.15
Nodes (6): ContactPage, WebhookVerification, _path(), SquidCrmAdapter, _string(), process_squid_event()

### Community 46 - "test_voice_sessions.py"
Cohesion: 0.25
Nodes (12): _grant_consent(), _seed_agent(), _session_payload(), test_consented_client_is_callable_after_audience_import_never_widens(), test_instructions_are_recorded_and_rejected_with_provider_capabilities(), test_session_creates_call_completes_simulated_and_records_envelope_events(), test_session_intake_is_idempotent_on_external_id(), test_terminate_is_idempotent_and_sessions_events_stay_ordered() (+4 more)

### Community 47 - "apiTypes.ts"
Cohesion: 0.11
Nodes (15): AgentCreate, CampaignCreate, CampaignSyncRequest, ConsentUpdate, ContactCreate, ContactImport, CrmConnectionCreate, CrmSyncRequest (+7 more)

### Community 49 - "test_voice_turn_detection.py"
Cohesion: 0.23
Nodes (12): extension_version_of(), _feed_silence(), _feed_speak(), test_bad_profile_and_unknown_kind_fail_honestly(), test_config_reports_every_feature(), test_engine_create_and_frame_feats(), test_every_feature_endpoint(), test_registry_eviction_is_bounded() (+4 more)

### Community 51 - "test_campaign_engine.py"
Cohesion: 0.19
Nodes (11): CampaignError, _db(), FakeClock, _result(), _setup(), test_cannot_start_empty_campaign(), test_dry_run_campaign_completes_and_records_simulated_calls(), test_idempotency_keys_are_unique_per_attempt() (+3 more)

### Community 52 - "compilerOptions"
Cohesion: 0.12
Nodes (15): compilerOptions, esModuleInterop, forceConsistentCasingInFileNames, jsx, lib, module, moduleResolution, noEmit (+7 more)

### Community 53 - "TurnEventType"
Cohesion: 0.12
Nodes (14): TurnEventType, EndpointCandidate, InterruptionDetected, ProviderFailed, Recovered, Rejected, SpeechEnded, SpeechStarted (+6 more)

### Community 54 - "VoiceBoostConfig"
Cohesion: 0.16
Nodes (8): config_round_trips_through_serde(), limiter_bounds_peak_under_overload(), media_bridge_with_voice_boost_still_completes_turns(), VoiceBoostConfig, VoiceBoostProfile, Clear, Natural, Studio

### Community 55 - "conftest.py"
Cohesion: 0.19
Nodes (6): client(), database(), fake_pyreverseetl_transport(), _pg_reachable(), settings(), voice_runtime_client()

### Community 56 - "AgentBrain"
Cohesion: 0.12
Nodes (11): A. Squid CRM → Voxora (audience), ADR-0001 — Ownership boundaries (non-negotiable), ADR-0002 — Independence and the no-source-import rule (supersedes any in-process-import idea), ADR-0003 — Integration contracts (v1, all versioned envelopes), ADR-0004 — Migration discipline, ADR-0005 — Voxora internal modules to introduce (Phase 3), B. Voxora → PyInterviewBot (voice sessions), C. Voxora events (versioned webhooks out) (+3 more)

### Community 57 - "OpenRouterLLM"
Cohesion: 0.18
Nodes (4): _env_int(), OpenRouterError, OpenRouterLLM, test_llm_decide_round_trip_pins_route_contract()

### Community 58 - "test_twilio_webhooks.py"
Cohesion: 0.29
Nodes (10): _configured(), _create_conversation(), _signed_form(), test_async_provider_leaves_calls_in_flight_not_fake_completed(), test_recording_callback_for_unknown_call_is_404(), test_recording_callback_stores_pointer_and_emits_recording_ready(), test_status_callback_settles_call_end_to_end(), test_status_callback_unknown_call_sid_is_404() (+2 more)

### Community 59 - "Biquad"
Cohesion: 0.21
Nodes (7): Biquad, clipping_detector_counts_rail_samples(), de_esser_attenuates_sibilance_only(), DeEsser, lowcut_removes_dc_component(), presence_eq_boosts_speech_band_but_not_bass(), sine()

### Community 60 - "SecretBox"
Cohesion: 0.43
Nodes (3): SecretBox, test_encrypts_and_decrypts(), test_wrong_key_rejected()

### Community 61 - "test_ghl_adapter.py"
Cohesion: 0.29
Nodes (8): _adapter(), FakeLeadConnectorTransport, test_connection_success_and_auth_failure(), test_connection_test_reports_bad_location(), test_get_contact_and_suppression_states(), test_list_contacts_maps_and_pages_by_id_cursor(), test_requests_carry_auth_and_version_header(), test_sync_connection_applies_ghl_suppression()

### Community 62 - "voice_turn_detection.py"
Cohesion: 0.20
Nodes (6): Packaging: pip and uv, now_ms(), _raise_value_error(), _require_runtime(), TurnDetectionError, TurnDetectionUnavailable

### Community 63 - "HttpResponse"
Cohesion: 0.23
Nodes (12): HttpResponse, _http_error(), adapter(), FakeSquidTransport, test_capabilities_declared(), test_create_contact_sends_idempotency_key(), test_field_discovery_marks_unsupported_capabilities(), test_list_contacts_maps_verified_person_shape() (+4 more)

### Community 64 - "TurnDetectionState"
Cohesion: 0.14
Nodes (13): TurnDetectionState, AiSpeaking, AiThinking, Ended, Idle, Interrupted, InterruptionCandidate, InTurn (+5 more)

### Community 66 - "fastapi_testclient"
Cohesion: 0.39
Nodes (3): _settings(), test_metrics_exposes_expected_families(), test_metrics_requires_a_token()

### Community 67 - "Ecosystem Migration — Capability Inventory (Phase 1)"
Cohesion: 0.29
Nodes (6): 1. MudFish (only upstream crawler; keep its lane), 2. QuillSift (only document/knowledge processing; keep its lane), 3. Squid CRM (owns contacts, segmentation, audiences, eligibility records; never executes calls), 4. Voxora Studio (owns telephony, voice agents, OpenRouter, call state, campaigns; never interview methodology), 5. PyInterviewBot (owns interview methodology only; consumes Voxora, never telephony/LLM-provider infra), Ecosystem Migration — Capability Inventory (Phase 1)

### Community 68 - "generate_ts_client.py"
Cohesion: 0.35
Nodes (6): _clean_null_anyof(), generate_paths(), main(), path_constant(), ts_object(), ts_type()

### Community 69 - "json"
Cohesion: 0.42
Nodes (11): _contact_names(), _create_connection(), _delivery(), _person_payload(), _post(), _secret(), _sign(), test_duplicate_delivery_of_same_event_is_noop() (+3 more)

### Community 70 - ".process"
Cohesion: 0.29
Nodes (14): Agc, agc_boosts_quiet_speech_to_target_without_clipping(), bypass_preserves_bytes_exactly(), chunk_split_processing_is_deterministic(), empty_input_is_safe(), i16_of(), lcg_noise(), nnnoiseless_preserves_clean_speech_tone() (+6 more)

### Community 72 - "Implementation Audit (honest verification record)"
Cohesion: 0.20
Nodes (6): Assumptions made (not verified), Environment (verified), Honest gaps vs. master prompt, Implementation Audit (honest verification record), M7 + M8 completion (2026-11-12, verified this session), What is NOT verified / NOT present (explicit)

### Community 73 - "Phase 1–3 update (2026-11-01, verified this session)"
Cohesion: 0.17
Nodes (12): CI on GitHub (Phase 1), Consent/suppression sync honesty (Phase 3), Console generated client + postgres compose (Phase 2), Not done / unchanged (still honest), Phase 1–3 update (2026-11-01, verified this session), Postgres storage + compat suite (Phase 2), Request-model tightening (Phase 3), Scheduling (Phase 2) (+4 more)

### Community 76 - "_openrouter_client"
Cohesion: 0.31
Nodes (7): _decide_payload(), _openrouter_client(), test_ai_decide_openrouter_success_records_provider_and_usage(), test_ai_decide_openrouter_unavailable_is_503(), explode(), test_ai_decide_openrouter_unconfigured_is_501(), test_ai_decide_unknown_profile_is_501_with_configured_list()

### Community 77 - "test_audience_import.py"
Cohesion: 0.47
Nodes (8): _import_payload(), _seed(), test_global_suppression_blocks_contact_with_recorded_rejection(), test_import_creates_campaign_with_provenance_and_preserves_squid_ids(), test_invalid_phone_and_garbage_are_rejected_with_validation_errors(), test_missing_consent_is_recorded_unknown_and_not_silently_widened(), test_reimport_is_idempotent_no_duplicate_members(), test_voice_consent_refusal_marks_suppressed_and_reports_reasons()

### Community 80 - "SemanticVerdict"
Cohesion: 0.25
Nodes (8): PendingBoundary, SemanticReport, SemanticVerdict, Complete, Correction, Filler, Incomplete, Question

### Community 81 - "Voxora Studio — Architecture"
Cohesion: 0.25
Nodes (8): AI runtime (Python), Console (React), CRM integration — Squid (first integration, verified against source), Enforcement of honesty, Goal, Layout, Voice runtime (Rust), Voxora Studio — Architecture

### Community 82 - "Risks"
Cohesion: 0.25
Nodes (7): Assumptions made and why, Compliance / legal (highest), Open questions, Project / honest-reporting risks, Risks, Risks, Assumptions, and Open Questions, Technical

### Community 83 - "Roadmap"
Cohesion: 0.20
Nodes (10): Explicit non-goals (patterns rejected from competitor adoption), M0 — Local, simulation-only showcase (current build ✅), M1 — Make the simulation trustworthy at scale (✅ 2026-11-01), M2 — Real CRM breadth (Squid first, then one more), M3 — Real voice (the hard part), M4 — Production hygiene, M6 — Real-time media bridge (P1, extends M3's open media item), M8 — Integrity signals (P2) (+2 more)

### Community 84 - "Candidates"
Cohesion: 0.25
Nodes (7): 1. WebRTC Audio Processing Module (via `webrtc-audio-processing` 2.1.0), 2. RNNoise (via `nnnoiseless` 0.5.2), 3. DeepFilterNet3 (via `deep-filter-rt` 0.1.1), 4. SpeexDSP (`speexdsp` rust-av bindings / `aec-rs` vendored C), Candidates, DSP engine evaluation (Voice Boost, M9), Selection

### Community 86 - "_FakePyReverseETL"
Cohesion: 0.25
Nodes (4): _FakePyReverseETL, BaseConnector, ConnectorError, WebhookConnector

### Community 87 - "downsampler_preserves_bandlimited_signal_rms"
Cohesion: 0.24
Nodes (5): Downsampler3, downsampler_preserves_bandlimited_signal_rms(), hermite(), Upsampler3, upsampler_emits_three_outputs_per_input_after_flush()

### Community 88 - "Voxora Studio"
Cohesion: 0.29
Nodes (7): Development, Not implemented (read this), Quick start (Docker), Quick start (local, no Docker), Repo layout, Voxora Studio, What works today

### Community 89 - "What actually runs (executed commands, this session)"
Cohesion: 0.33
Nodes (6): AI runtime, Console (React + Vite + TS), Docker Compose (executed after the first audit pass), Orchestrator (FastAPI, SQLite), Voice runtime (Rust), What actually runs (executed commands, this session)

### Community 93 - "voxora-voice-runtime"
Cohesion: 0.50
Nodes (3): Contract notes, Modules, voxora-voice-runtime

### Community 94 - "AgentEvent"
Cohesion: 0.50
Nodes (4): AgentEvent, ThinkingStarted, TtsEnded, TtsStarted

### Community 95 - "Settings"
Cohesion: 0.10
Nodes (13): build_app(), create_app_from_env(), lifespan(), _bool(), _float(), _int(), _json_object_env(), Settings (+5 more)

### Community 97 - "voxora-studio"
Cohesion: 0.67
Nodes (3): voxora-ai-runtime, voxora-orchestrator, voxora-studio

## Knowledge Gaps
- **86 isolated node(s):** `@types/react`, `@types/react-dom`, `typescript`, `voxora-voice-runtime`, `voxora_voice_runtime` (+81 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 699 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **19 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Database` connect `Database` to `api.py`, `db.py`, `iso`, `.get_voice_session`, `CampaignEngine`, `LiveTurnEngine`, `voice_sessions.py`, `create_app`, `eligibility.py`, `test_restart_recovery_terminates_voice_session`, `Task 1: Model-profile dispatch + usage rows (P8)`, `settle_voice_session`, `test_twilio_adapter.py`, `dispatch_pending_webhooks`, `CanonicalContact`, `SquidCrmAdapter`, `test_voice_sessions.py`, `.create_voice_session`, `test_campaign_engine.py`, `conftest.py`, `.initialize`, `.mark_session_instruction_spoken`, `Roadmap`, `Voxora Studio`, `Settings`?**
  _High betweenness centrality (0.116) - this node is a cross-community bridge._
- **Are the 88 inferred relationships involving `Database` (e.g. with `M1 — Make the simulation trustworthy at scale (✅ 2026-11-01)` and `Global Constraints`) actually correct?**
  _`Database` has 88 INFERRED edges - model-reasoned connections that need verification._
- **What connects `@types/react`, `@types/react-dom`, `typescript` to the rest of the system?**
  _86 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_voice_providers.py` be split into smaller, more focused modules?**
  _Cohesion score 0.06189640035118525 - nodes in this community are weakly interconnected._
- **Why does `create_app()` connect `create_app` to `test_voice_providers.py`, `api.py`, `iso`, `CampaignEngine`, `Database`, `LiveTurnEngine`, `typing`, `test_restart_recovery_terminates_voice_session`, `LeadConnectorCrmAdapter`, `TurnDetectionEngine`, `Task 1: Model-profile dispatch + usage rows (P8)`, `logging`, `settle_voice_session`, `dispatch_pending_webhooks`, `CanonicalContact`, `security.py`, `SquidCrmAdapter`, `test_campaign_engine.py`, `conftest.py`, `OpenRouterLLM`, `SecretBox`, `voice_turn_detection.py`, `fastapi_testclient`, `prometheus_metrics`, `_openrouter_client`, `Settings`?**
  _High betweenness centrality (0.102) - this node is a cross-community bridge._
- **Are the 42 inferred relationships involving `create_app()` (e.g. with `build_app()` and `OpenRouterError`) actually correct?**
  _`create_app()` has 42 INFERRED edges - model-reasoned connections that need verification._
- **Should `media.rs` be split into smaller, more focused modules?**
  _Cohesion score 0.061458718992965566 - nodes in this community are weakly interconnected._