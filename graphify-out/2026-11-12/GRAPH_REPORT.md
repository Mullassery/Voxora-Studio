# Graph Report - Voxora-Studio  (2026-11-12)

## Corpus Check
- 104 files · ~94,389 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 10 file(s) not represented in the graph (top: (none) 4, .typed 2, .example 1)

## Summary
- 2229 nodes · 5652 edges · 107 communities (86 shown, 21 thin omitted)
- Extraction: 87% EXTRACTED · 13% INFERRED · 0% AMBIGUOUS · INFERRED: 707 edges (avg confidence: 0.91)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `8a368d24`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Settings
- media.rs
- bridge.rs
- api.py
- test_session_budgets.py
- iso
- settle_voice_session
- CampaignEngine
- Database
- endpointing.rs
- vad.rs
- api.ts
- controller.rs
- semantic.rs
- LiveTurnEngine
- CallRequest
- voice_sessions.py
- create_app
- barge_in.rs
- TurnDetectionController
- ApiError
- test_openrouter.py
- typing
- InMemoryCrmAdapter
- td_events.rs
- openrouter.py
- turn.rs
- test_voice_turn_loop_http.py
- package.json
- App.tsx
- TurnDetectionEngine
- squid.py
- ConsoleApi
- Task 1: Model-profile dispatch + usage rows (P8)
- config.py
- wav.rs
- test_voice_analytics.py
- test_twilio_adapter.py
- dsp.rs
- .process
- VoiceRuntimeTelephonyAdapter
- _error_body
- sync_connection
- test_api.py
- security.py
- SquidCrmAdapter
- test_voice_sessions.py
- apiTypes.ts
- .create_agent
- test_voice_turn_detection.py
- OpenRouterAgent
- PostgresDatabase
- compilerOptions
- TurnEventType
- VoiceBoostConfig
- conftest.py
- AgentBrain
- OpenRouterLLM
- test_twilio_webhooks.py
- Biquad
- SecretBox
- test_crm_contract.py
- voice_turn_detection.py
- test_squid_adapter.py
- TurnDetectionState
- dispatch_pending_webhooks
- pytest
- TwilioTelephonyAdapter
- json
- test_webhook_replay.py
- .new
- test_engine.py
- Implementation Audit (honest verification record)
- Phase 1–3 update (2026-11-01, verified this session)
- _turn_engine_or_404
- CheckpointQueue
- _openrouter_client
- test_audience_import.py
- logging
- voice_session_checkpoints.py
- SemanticVerdict
- Voxora Studio — Architecture
- Risks
- Roadmap
- Candidates
- TurnAgent
- _FakePyReverseETL
- Upsampler3
- Voxora Studio
- What actually runs (executed commands, this session)
- LookAheadLimiter
- FakeClock
- voxora-voice-runtime
- AgentEvent
- TenantPrincipal
- M7 + M8 Completion Plan (Voxora-Studio)
- voxora-studio
- voxora-voice-runtime
- voxora_voice_runtime

## God Nodes (most connected - your core abstractions)
1. `Database` - 191 edges
2. `create_app()` - 152 edges
3. `iso()` - 59 edges
4. `ApiError` - 44 edges
5. `TurnDetectionController` - 42 edges
6. `CampaignEngine` - 36 edges
7. `SquidCrmAdapter` - 36 edges
8. `Settings` - 34 edges
9. `LiveTurnEngine` - 30 edges
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

## Communities (107 total, 21 thin omitted)

### Community 0 - "Settings"
Cohesion: 0.05
Nodes (32): Task 3: STT/TTS routing table exposure (P7), Settings, adapter_from_spec(), adapter_id(), LanguageRoutingTable, normalize_language(), provider_status(), ProviderAdapter (+24 more)

### Community 1 - "media.rs"
Cohesion: 0.06
Nodes (44): assembler_closes_only_after_the_quiet_gap(), ChannelRing, closed_transport_surfaces_honestly(), decision_runs_exactly_once_for_one_utterance(), Encoding, Pcm16, Ulaw, end_to_end_scripted_media_exchange() (+36 more)

### Community 2 - "bridge.rs"
Cohesion: 0.06
Nodes (38): backend(), BridgeError, DialogNeverEnded, EmptyDialog, BridgeEvent, BridgeSummary, DialogBackend, empty_dialog_is_reported_honestly() (+30 more)

### Community 3 - "api.py"
Cohesion: 0.06
Nodes (28): AgentCreate, AiDecideRequest, AudienceConsent, AudienceContact, AudienceImport, CampaignCreate, CampaignSyncRequest, ConsentUpdate (+20 more)

### Community 4 - "test_session_budgets.py"
Cohesion: 0.08
Nodes (42): parse_iso(), utcnow(), EligibilityDecision, evaluate(), is_due(), next_backoff(), summarize(), _window_state() (+34 more)

### Community 5 - "iso"
Cohesion: 0.08
Nodes (17): M1 — Make the simulation trustworthy at scale (✅ 2026-11-01), process_squid_event(), iso(), new_id(), assert_transition(), _campaign_with_one_recipient(), test_audit_rows_scoped_and_sorted(), test_campaign_lifecycle_claims_and_completion() (+9 more)

### Community 6 - "settle_voice_session"
Cohesion: 0.07
Nodes (24): Global Constraints, Task 5: Speech-analytics metadata (M8, P10), CheckpointFlusher, flush_checkpoint(), parse_snapshot(), recover_answers(), recover_session(), TranscriptCheckpointError (+16 more)

### Community 7 - "CampaignEngine"
Cohesion: 0.08
Nodes (21): Twilio telephony adapter (Phase 5, verified 2026-11-01), 1. MudFish (only upstream crawler; keep its lane), 2. QuillSift (only document/knowledge processing; keep its lane), 3. Squid CRM (owns contacts, segmentation, audiences, eligibility records; never executes calls), 4. Voxora Studio (owns telephony, voice agents, OpenRouter, call state, campaigns; never interview methodology), 5. PyInterviewBot (owns interview methodology only; consumes Voxora, never telephony/LLM-provider infra), Ecosystem Migration — Capability Inventory (Phase 1), CampaignEngine (+13 more)

### Community 8 - "Database"
Cohesion: 0.12
Nodes (6): Database, test_record_and_list_ai_usage(), test_call_events_and_listing(), test_contact_consent_and_suppression(), test_crm_connection_and_mappings_roundtrip(), test_initialize_and_ping()

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
Cohesion: 0.12
Nodes (9): session_status_for(), LiveTurnEngine, _merge_usage(), _reply_agent_latency(), _reply_ended(), _reply_text(), run_live_turns(), TurnResult (+1 more)

### Community 15 - "CallRequest"
Cohesion: 0.12
Nodes (9): CallRequest, TelephonyAdapter, TelephonyEvent, TelephonyResult, _at(), MockTelephonyAdapter, _transcript(), build_telephony_adapter() (+1 more)

### Community 16 - "voice_sessions.py"
Cohesion: 0.09
Nodes (14): M5 — Make the integration contracts real (P0) — COMPLETE 2026-11-04, build_event(), deliver_webhook(), dispatch_session_events(), dispatch_session_events_via_pyreverseetl(), _event_envelope(), _FakeSource, signed_envelope_body() (+6 more)

### Community 17 - "create_app"
Cohesion: 0.09
Nodes (28): create_app(), _adapter_for(), _campaign_view(), cancel_campaign(), create_campaign(), create_turn_engine(), crm_providers(), default_adapter_factory() (+20 more)

### Community 18 - "barge_in.rs"
Cohesion: 0.11
Nodes (20): acknowledgements_are_backchannels_playback_survives(), BargeInConfig, BargeInGate, below_threshold_frame_resets_consecutive_count(), candidate_speech_after_n_frames_triggers(), corrections_reframe_backchannel_tokens_as_genuine(), custom_lexicon_replaces_default(), DEFAULT_BACKCHANNELS (+12 more)

### Community 20 - "ApiError"
Cohesion: 0.09
Nodes (28): ApiError, add_recipients(), add_suppression(), cancel_call(), create_agent(), create_contact(), delete_suppression(), describe_fields() (+20 more)

### Community 21 - "test_openrouter.py"
Cohesion: 0.16
Nodes (16): build_agent(), _default_env(), make_client(), test_agent_brain_takes_over_turns_and_can_end(), respond(), test_default_transport_is_synchronous_client(), test_end_call_flag_and_local_end_keywords(), test_health_and_key_model_requirements() (+8 more)

### Community 22 - "typing"
Cohesion: 0.10
Nodes (3): builtin_model_profiles(), _percentile(), seed_model_profiles()

### Community 23 - "InMemoryCrmAdapter"
Cohesion: 0.10
Nodes (10): ConnectionTest, ContactPage, CrmCapabilities, FieldInfo, test_sync_uses_adapter_and_imports_contacts(), capabilities(), describe_fields(), list_contacts() (+2 more)

### Community 24 - "td_events.rs"
Cohesion: 0.09
Nodes (18): age_never_goes_negative(), DetectionSource, BargeIn, Controller, Endpointing, Semantic, Vad, event_carries_schema_version_and_full_identity() (+10 more)

### Community 25 - "openrouter.py"
Cohesion: 0.12
Nodes (9): AgentReply, AgentTurnError, MockAgent, turn_from_reply(), create_agent_from_env(), _parse_reply(), _safe_float(), _strip_json() (+1 more)

### Community 26 - "turn.rs"
Cohesion: 0.15
Nodes (20): barge_in_allowed_after_guard(), barge_in_guard_blocks_early_interrupt(), full_conversation_lifecycle(), invalid_transitions_rejected(), InvalidSignal, many_turns_counted(), round_trip(), Signal (+12 more)

### Community 27 - "test_voice_turn_loop_http.py"
Cohesion: 0.22
Nodes (22): M7 — Live turn loop + model routing (P1), Review Focus, Task 2: Live turn loop mounted for the media backend (M7), Task 4: Server-authoritative TTS pinning (P9), Task 6: Roadmap + audit docs, final verification, _create_session(), _grant_consent(), _seed_agent() (+14 more)

### Community 28 - "package.json"
Cohesion: 0.09
Nodes (22): dependencies, react, react-dom, devDependencies, @types/react, @types/react-dom, typescript, vite (+14 more)

### Community 29 - "App.tsx"
Cohesion: 0.12
Nodes (20): AgentRow, ApiError, Envelope, TurnConfig, TurnEvent, TurnFeedResult, ACTIONS, App() (+12 more)

### Community 31 - "squid.py"
Cohesion: 0.13
Nodes (8): WebhookVerification, build_url(), HttpResponse, HttpTransport, UrllibTransport, _http_error(), parse_squid_webhook(), VerifiedWebhook

### Community 33 - "Task 1: Model-profile dispatch + usage rows (P8)"
Cohesion: 0.16
Nodes (12): Task 1: Model-profile dispatch + usage rows (P8), _configured_profile_names(), resolve_model_profile(), UnknownModelProfileError, _decide_payload(), _openrouter_client(), test_ai_decide_dispatches_profile_model(), test_ai_decide_mock_records_zero_usage() (+4 more)

### Community 34 - "config.py"
Cohesion: 0.13
Nodes (8): create_app_from_env(), _bool(), _float(), _int(), _json_object_env(), main(), configure_logging(), JsonFormatter

### Community 35 - "wav.rs"
Cohesion: 0.14
Nodes (18): custom_header_goofiness_detected(), duration_at_8khz(), encode_pcm16_mono(), inspect(), le_u16(), le_u32(), rejects_garbage(), rejects_non_pcm16() (+10 more)

### Community 36 - "test_voice_analytics.py"
Cohesion: 0.16
Nodes (7): compute_speech_analytics(), _decode_wav(), _rms_per_window(), test_no_audio_returns_none(), test_unparseable_audio_returns_none(), test_wav_fixture_deterministic_estimates(), _write_wav()

### Community 37 - "test_twilio_adapter.py"
Cohesion: 0.27
Nodes (17): _adapter(), _engine_setup(), FakeTwilioTransport, _request(), test_429_retry_after_then_success(), test_busy_callback_requeues_recipient_for_retry(), test_callback_result_maps_documented_statuses(), test_callback_result_rejects_unmapped_status() (+9 more)

### Community 38 - "dsp.rs"
Cohesion: 0.11
Nodes (10): clipping_detector_counts_rail_samples(), DE_ESS_HZ, Downsampler3, downsampler_preserves_bandlimited_signal_rms(), ENGINE_RATE, EnhancedFrame, LIMITER_LOOKAHEAD, NN_RATE (+2 more)

### Community 39 - ".process"
Cohesion: 0.14
Nodes (9): Agc, count_clipped(), echo_seam_is_honest_by_default(), EchoCanceller, empty_input_is_safe(), NullEchoCanceller, rms(), rms_i16() (+1 more)

### Community 40 - "VoiceRuntimeTelephonyAdapter"
Cohesion: 0.16
Nodes (7): M3 — Real voice (the hard part), _at(), extension(), telephony_result_from_runtime_json(), VoiceRuntimeTelephonyAdapter, test_adapter_idempotent_on_idempotency_key(), test_missing_extension_error_is_actionable()

### Community 41 - "_error_body"
Cohesion: 0.10
Nodes (17): _connection_view(), ai_decide(), api_error_handler(), create_connection(), get_connection(), http_error_handler(), list_connections(), squid_webhook() (+9 more)

### Community 42 - "sync_connection"
Cohesion: 0.18
Nodes (4): CrmAdapter, NotSupportedError, sync_connection(), test_sync_skips_unsupported_consent_honestly()

### Community 43 - "test_api.py"
Cohesion: 0.12
Nodes (9): _create_campaign(), _health(), test_ai_decide_mock_path_is_deterministic_and_audited(), test_ai_decide_rejects_non_writer(), test_audit_log_records_promotions(), test_campaign_lifecycle_runs_dry_run_end_to_end(), test_campaign_recipient_validation_reports_blocks(), test_health_is_public_and_honest_about_simulation() (+1 more)

### Community 44 - "security.py"
Cohesion: 0.16
Nodes (8): redact(), safe_equal(), squid_signature(), twilio_signature(), utc_token(), verify_squid_signature(), verify_twilio_signature(), test_twilio_signature_roundtrip_and_rejection()

### Community 45 - "SquidCrmAdapter"
Cohesion: 0.28
Nodes (5): CanonicalContact, CrmError, _path(), SquidCrmAdapter, _string()

### Community 46 - "test_voice_sessions.py"
Cohesion: 0.25
Nodes (12): _grant_consent(), _seed_agent(), _session_payload(), test_consented_client_is_callable_after_audience_import_never_widens(), test_instructions_are_recorded_and_rejected_with_provider_capabilities(), test_session_creates_call_completes_simulated_and_records_envelope_events(), test_session_intake_is_idempotent_on_external_id(), test_terminate_is_idempotent_and_sessions_events_stay_ordered() (+4 more)

### Community 47 - "apiTypes.ts"
Cohesion: 0.11
Nodes (15): AgentCreate, CampaignCreate, CampaignSyncRequest, ConsentUpdate, ContactCreate, ContactImport, CrmConnectionCreate, CrmSyncRequest (+7 more)

### Community 48 - ".create_agent"
Cohesion: 0.14
Nodes (4): seed_demo(), test_engine_records_turn_usage_rows(), test_engine_usage_persists_across_engines(), test_agent_crud_scoped()

### Community 49 - "test_voice_turn_detection.py"
Cohesion: 0.21
Nodes (12): extension_version_of(), _feed_silence(), _feed_speak(), test_bad_profile_and_unknown_kind_fail_honestly(), test_config_reports_every_feature(), test_engine_create_and_frame_feats(), test_every_feature_endpoint(), test_registry_eviction_is_bounded() (+4 more)

### Community 51 - "PostgresDatabase"
Cohesion: 0.18
Nodes (5): database_from_settings(), _pg_url(), _PgConnection, PostgresDatabase, _translate_sqlite_sql()

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
Cohesion: 0.24
Nodes (5): Biquad, de_esser_attenuates_sibilance_only(), DeEsser, lowcut_removes_dc_component(), presence_eq_boosts_speech_band_but_not_bass()

### Community 60 - "SecretBox"
Cohesion: 0.20
Nodes (6): Orchestrator (FastAPI), build_crm_adapter(), SecretBox, InvalidTransition, test_encrypts_and_decrypts(), test_wrong_key_rejected()

### Community 61 - "test_crm_contract.py"
Cohesion: 0.22
Nodes (12): _contact(), _records(), test_contract_capabilities_honestly_disable_unsupported_ops(), test_contract_connection_test_and_capabilities(), test_contract_create_then_get_round_trip(), test_contract_field_discovery_includes_required_canonical_fields(), test_contract_list_contacts_returns_page(), test_contract_unsupported_operations_raise() (+4 more)

### Community 62 - "voice_turn_detection.py"
Cohesion: 0.19
Nodes (6): Packaging: pip and uv, now_ms(), _raise_value_error(), _require_runtime(), TurnDetectionError, TurnDetectionUnavailable

### Community 63 - "test_squid_adapter.py"
Cohesion: 0.29
Nodes (10): adapter(), FakeSquidTransport, test_capabilities_declared(), test_create_contact_sends_idempotency_key(), test_field_discovery_marks_unsupported_capabilities(), test_list_contacts_maps_verified_person_shape(), test_rate_limit_respects_retry_after_header(), test_retry_exhaustion_raises_crm_error() (+2 more)

### Community 64 - "TurnDetectionState"
Cohesion: 0.14
Nodes (13): TurnDetectionState, AiSpeaking, AiThinking, Ended, Idle, Interrupted, InterruptionCandidate, InTurn (+5 more)

### Community 65 - "dispatch_pending_webhooks"
Cohesion: 0.18
Nodes (10): create_voice_session(), get_voice_session(), post_session_instruction(), run_voice_session_turn(), terminate_voice_session(), lifespan(), _telephony_capabilities(), _voice_session_view() (+2 more)

### Community 66 - "pytest"
Cohesion: 0.22
Nodes (10): overview(), telephony_providers(), telephony_provider_status(), test_campaign_lifecycle_transitions_are_explicit(), _canonical_payload(), _request(), test_live_calls_refused_for_simulation_providers(), test_mapping_rejects_wrong_schema_version() (+2 more)

### Community 67 - "TwilioTelephonyAdapter"
Cohesion: 0.21
Nodes (4): _basic(), _iso(), TwilioError, TwilioTelephonyAdapter

### Community 68 - "json"
Cohesion: 0.32
Nodes (7): build_app(), _clean_null_anyof(), generate_paths(), main(), path_constant(), ts_object(), ts_type()

### Community 69 - "test_webhook_replay.py"
Cohesion: 0.44
Nodes (11): _contact_names(), _create_connection(), _delivery(), _person_payload(), _post(), _secret(), _sign(), test_duplicate_delivery_of_same_event_is_noop() (+3 more)

### Community 70 - ".new"
Cohesion: 0.42
Nodes (11): agc_boosts_quiet_speech_to_target_without_clipping(), bypass_preserves_bytes_exactly(), chunk_split_processing_is_deterministic(), i16_of(), lcg_noise(), nnnoiseless_preserves_clean_speech_tone(), nnnoiseless_suppresses_stationary_noise(), per_call_stages_do_not_leak_state() (+3 more)

### Community 72 - "Implementation Audit (honest verification record)"
Cohesion: 0.20
Nodes (6): Assumptions made (not verified), Environment (verified), Honest gaps vs. master prompt, Implementation Audit (honest verification record), M7 + M8 completion (2026-11-12, verified this session), What is NOT verified / NOT present (explicit)

### Community 73 - "Phase 1–3 update (2026-11-01, verified this session)"
Cohesion: 0.20
Nodes (10): CI on GitHub (Phase 1), Consent/suppression sync honesty (Phase 3), Console generated client + postgres compose (Phase 2), Not done / unchanged (still honest), Phase 1–3 update (2026-11-01, verified this session), Request-model tightening (Phase 3), Scheduling (Phase 2), Voice-runtime <-> orchestrator seam (Phase 4, verified 2026-11-01) (+2 more)

### Community 74 - "_turn_engine_or_404"
Cohesion: 0.47
Nodes (10): feed_turn_agent_event(), feed_turn_frame(), feed_turn_lid(), feed_turn_provider_failure(), feed_turn_provider_recovery(), feed_turn_semantic(), feed_turn_transcript(), _turn_engine_or_404() (+2 more)

### Community 76 - "_openrouter_client"
Cohesion: 0.36
Nodes (6): _decide_payload(), _openrouter_client(), test_ai_decide_openrouter_success_records_provider_and_usage(), test_ai_decide_openrouter_unavailable_is_503(), test_ai_decide_openrouter_unconfigured_is_501(), test_ai_decide_unknown_profile_is_501_with_configured_list()

### Community 77 - "test_audience_import.py"
Cohesion: 0.47
Nodes (8): _import_payload(), _seed(), test_global_suppression_blocks_contact_with_recorded_rejection(), test_import_creates_campaign_with_provenance_and_preserves_squid_ids(), test_invalid_phone_and_garbage_are_rejected_with_validation_errors(), test_missing_consent_is_recorded_unknown_and_not_silently_widened(), test_reimport_is_idempotent_no_duplicate_members(), test_voice_consent_refusal_marks_suppressed_and_reports_reasons()

### Community 78 - "logging"
Cohesion: 0.44
Nodes (4): _format_with(), test_standard_extra_fields_are_promoted(), test_standard_logrecord_attributes_are_not_leaked(), test_underscore_prefixed_extras_keep_legacy_promotion()

### Community 79 - "voice_session_checkpoints.py"
Cohesion: 0.32
Nodes (3): effective_flush_interval(), should_flush(), test_dialing_effective_interval_is_boundary_exactly_2_5x_heartbeat()

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
Cohesion: 0.25
Nodes (8): Explicit non-goals (patterns rejected from competitor adoption), M0 — Local, simulation-only showcase (current build ✅), M2 — Real CRM breadth (Squid first, then one more), M4 — Production hygiene, M6 — Real-time media bridge (P1, extends M3's open media item), M8 — Integrity signals (P2), M9 — Voice Intelligence Suite (in progress, branch `feature/voice-intelligence`), Roadmap

### Community 84 - "Candidates"
Cohesion: 0.25
Nodes (7): 1. WebRTC Audio Processing Module (via `webrtc-audio-processing` 2.1.0), 2. RNNoise (via `nnnoiseless` 0.5.2), 3. DeepFilterNet3 (via `deep-filter-rt` 0.1.1), 4. SpeexDSP (`speexdsp` rust-av bindings / `aec-rs` vendored C), Candidates, DSP engine evaluation (Voice Boost, M9), Selection

### Community 86 - "_FakePyReverseETL"
Cohesion: 0.25
Nodes (4): _FakePyReverseETL, BaseConnector, ConnectorError, WebhookConnector

### Community 87 - "Upsampler3"
Cohesion: 0.43
Nodes (3): hermite(), Upsampler3, upsampler_emits_three_outputs_per_input_after_flush()

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

### Community 97 - "voxora-studio"
Cohesion: 0.67
Nodes (3): voxora-ai-runtime, voxora-orchestrator, voxora-studio

## Knowledge Gaps
- **86 isolated node(s):** `@types/react`, `@types/react-dom`, `typescript`, `voxora-voice-runtime`, `voxora_voice_runtime` (+81 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 683 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **21 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Database` connect `Database` to `api.py`, `test_session_budgets.py`, `iso`, `settle_voice_session`, `CampaignEngine`, `LiveTurnEngine`, `voice_sessions.py`, `create_app`, `typing`, `Task 1: Model-profile dispatch + usage rows (P8)`, `test_twilio_adapter.py`, `sync_connection`, `test_voice_sessions.py`, `.create_agent`, `PostgresDatabase`, `conftest.py`, `dispatch_pending_webhooks`, `voice_session_checkpoints.py`, `TurnAgent`, `Voxora Studio`?**
  _High betweenness centrality (0.112) - this node is a cross-community bridge._
- **Are the 85 inferred relationships involving `Database` (e.g. with `M1 — Make the simulation trustworthy at scale (✅ 2026-11-01)` and `Global Constraints`) actually correct?**
  _`Database` has 85 INFERRED edges - model-reasoned connections that need verification._
- **What connects `@types/react`, `@types/react-dom`, `typescript` to the rest of the system?**
  _86 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Settings` be split into smaller, more focused modules?**
  _Cohesion score 0.052947052947052944 - nodes in this community are weakly interconnected._
- **Why does `create_app()` connect `create_app` to `Settings`, `api.py`, `iso`, `settle_voice_session`, `CampaignEngine`, `Database`, `LiveTurnEngine`, `CallRequest`, `voice_sessions.py`, `ApiError`, `TurnDetectionEngine`, `Task 1: Model-profile dispatch + usage rows (P8)`, `config.py`, `_error_body`, `sync_connection`, `security.py`, `PostgresDatabase`, `conftest.py`, `OpenRouterLLM`, `SecretBox`, `voice_turn_detection.py`, `dispatch_pending_webhooks`, `pytest`, `json`, `_turn_engine_or_404`, `_openrouter_client`, `TenantPrincipal`?**
  _High betweenness centrality (0.095) - this node is a cross-community bridge._
- **Are the 39 inferred relationships involving `create_app()` (e.g. with `build_app()` and `OpenRouterError`) actually correct?**
  _`create_app()` has 39 INFERRED edges - model-reasoned connections that need verification._
- **Should `media.rs` be split into smaller, more focused modules?**
  _Cohesion score 0.05894736842105263 - nodes in this community are weakly interconnected._