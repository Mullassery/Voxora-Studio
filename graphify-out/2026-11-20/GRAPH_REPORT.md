# Graph Report - Voxora-Studio  (2026-11-20)

## Corpus Check
- 136 files · ~135,867 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 10 file(s) not represented in the graph (top: (none) 4, .typed 2, .example 1)

## Summary
- 2971 nodes · 7780 edges · 121 communities (103 shown, 18 thin omitted)
- Extraction: 86% EXTRACTED · 14% INFERRED · 0% AMBIGUOUS · INFERRED: 1122 edges (avg confidence: 0.91)
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
- db.py
- iso
- settle_voice_session
- CampaignEngine
- Database
- endpointing.rs
- vad.rs
- paths.ts
- controller.rs
- semantic.rs
- test_voice_turn_loop_http.py
- CallRequest
- voice_sessions.py
- create_app
- barge_in.rs
- TurnDetectionController
- generate_long_form
- test_openrouter.py
- test_voice_boost_settings.py
- test_sync_uses_adapter_and_imports_contacts
- td_events.rs
- openrouter.py
- super
- LeadConnectorCrmAdapter
- package.json
- App.tsx
- TurnDetectionEngine
- typing
- ConsoleApi
- Task 1: Model-profile dispatch + usage rows (P8)
- logging
- wav.rs
- audio_batches.py
- test_twilio_adapter.py
- dsp.rs
- VoiceBoostStage
- pytest
- _error_body
- CanonicalContact
- test_api.py
- security.py
- SquidCrmAdapter
- test_voice_sessions.py
- apiTypes.ts
- test_engine_usage_persists_across_engines
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
- SecretBox
- test_ghl_adapter.py
- json
- test_squid_adapter.py
- TurnDetectionState
- .initialize
- pathlib
- Ecosystem Migration — Capability Inventory (Phase 1)
- generate_ts_client.py
- test_webhook_replay.py
- .process
- test_engine.py
- Implementation Audit (honest verification record)
- test_audio_batches.py
- prometheus_metrics
- CheckpointQueue
- Settings
- test_audio_batch_api.py
- test_longform.py
- SemanticVerdict
- Roadmap
- Risks
- generate_asset
- Candidates
- AudioGeneration.tsx
- _FakePyReverseETL
- downsampler_preserves_bandlimited_signal_rms
- postprocess.rs
- voice_turn_loop.py
- TTSProviderError
- test_longform_api.py
- voxora-voice-runtime
- AgentEvent
- config.py
- test_session_budgets.py
- voxora-studio
- create_variant
- resample.rs
- LiveTurnEngine
- test_audio_assets.py
- Any
- voxora-voice-runtime
- voxora_voice_runtime
- TwilioTelephonyAdapter
- model_validator
- lifespan
- _voice_session_view
- _campaign_view
- Review Focus
- _page
- Audio Generation Studio (M10 Phase A–B) — design
- Voice Boost settings surface — design (small)
- FieldInfo
- VoiceBoostControlOverrides
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
7. `generate_long_form()` - 45 edges
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
- `Task 4: API routes` --references--> `AudioGenerationError`  [INFERRED]
  docs/superpowers/plans/2026-11-13-audio-generation-ab.md → services/orchestrator/src/voxora/audio_assets.py
- `CRM integration — Squid (first integration, verified against source)` --references--> `NotSupportedError`  [INFERRED]
  docs/architecture.md → services/orchestrator/src/voxora/crm/base.py

## Import Cycles
- None detected.

## Communities (121 total, 18 thin omitted)

### Community 0 - "test_voice_providers.py"
Cohesion: 0.06
Nodes (31): Task 3: STT/TTS routing table exposure (P7), voice_provider_routing_status(), adapter_from_spec(), adapter_id(), LanguageRoutingTable, normalize_language(), provider_status(), ProviderAdapter (+23 more)

### Community 1 - "media.rs"
Cohesion: 0.06
Nodes (44): assembler_closes_only_after_the_quiet_gap(), ChannelRing, closed_transport_surfaces_honestly(), decision_runs_exactly_once_for_one_utterance(), Encoding, Pcm16, Ulaw, end_to_end_scripted_media_exchange() (+36 more)

### Community 2 - "bridge.rs"
Cohesion: 0.05
Nodes (41): backend(), BridgeError, DialogNeverEnded, EmptyDialog, BridgeEvent, BridgeSummary, DialogBackend, empty_dialog_is_reported_honestly() (+33 more)

### Community 3 - "api.py"
Cohesion: 0.05
Nodes (33): AgentCreate, AiDecideRequest, AudienceConsent, AudienceContact, AudienceImport, CampaignCreate, CampaignSyncRequest, ConsentUpdate (+25 more)

### Community 4 - "db.py"
Cohesion: 0.06
Nodes (30): parse_iso(), utcnow(), EligibilityDecision, evaluate(), is_due(), next_backoff(), summarize(), _window_state() (+22 more)

### Community 5 - "iso"
Cohesion: 0.08
Nodes (26): M1 — Make the simulation trustworthy at scale (✅ 2026-11-01), Item 1 — Multi-worker claiming safety (roadmap M4a), iso(), new_id(), seed_demo(), assert_transition(), test_metrics_counters_reflect_domain_rows(), _campaign_with_one_recipient() (+18 more)

### Community 6 - "settle_voice_session"
Cohesion: 0.06
Nodes (29): Global Constraints, Task 5: Speech-analytics metadata (M8, P10), CheckpointFlusher, effective_flush_interval(), flush_checkpoint(), parse_snapshot(), recover_answers(), recover_session() (+21 more)

### Community 7 - "CampaignEngine"
Cohesion: 0.11
Nodes (5): CampaignEngine, CampaignError, decision_code(), CampaignWorker, test_cannot_start_empty_campaign()

### Community 8 - "Database"
Cohesion: 0.08
Nodes (7): Database, _percentile(), test_recover_inflight_requeues_stranded_batch_items(), test_crm_connection_and_mappings_roundtrip(), test_get_unset_tenant_returns_none(), test_seed_is_idempotent(), test_seed_then_get_then_put_roundtrip()

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

### Community 14 - "test_voice_turn_loop_http.py"
Cohesion: 0.22
Nodes (22): M7 — Live turn loop + model routing (P1), Review Focus, Task 2: Live turn loop mounted for the media backend (M7), Task 4: Server-authoritative TTS pinning (P9), Task 6: Roadmap + audit docs, final verification, _create_session(), _grant_consent(), _seed_agent() (+14 more)

### Community 15 - "CallRequest"
Cohesion: 0.10
Nodes (12): overview(), telephony_providers(), CallRequest, TelephonyAdapter, TelephonyResult, _at(), MockTelephonyAdapter, _transcript() (+4 more)

### Community 16 - "voice_sessions.py"
Cohesion: 0.11
Nodes (13): M5 — Make the integration contracts real (P0) — COMPLETE 2026-11-04, build_event(), deliver_webhook(), dispatch_session_events(), dispatch_session_events_via_pyreverseetl(), _event_envelope(), _FakeSource, signed_envelope_body() (+5 more)

### Community 17 - "create_app"
Cohesion: 0.08
Nodes (57): ApiError, create_app(), _adapter_for(), add_recipients(), add_suppression(), _batch_defaults(), cancel_call(), create_agent() (+49 more)

### Community 18 - "barge_in.rs"
Cohesion: 0.11
Nodes (20): acknowledgements_are_backchannels_playback_survives(), BargeInConfig, BargeInGate, below_threshold_frame_resets_consecutive_count(), candidate_speech_after_n_frames_triggers(), corrections_reframe_backchannel_tokens_as_genuine(), custom_lexicon_replaces_default(), DEFAULT_BACKCHANNELS (+12 more)

### Community 20 - "generate_long_form"
Cohesion: 0.09
Nodes (31): M10 Audio Generation, Phase D — long-form generation (2026-11-13, verified this session), Self-review notes, Task 2: Run engine — generate chunks, assemble, state machine (`longform.py` part 2), Task 3: API routes + models + row lifecycle, delete_audio_asset(), download_audio_asset(), AudioFileStore, AudioGenerationError (+23 more)

### Community 21 - "test_openrouter.py"
Cohesion: 0.15
Nodes (16): build_agent(), _default_env(), make_client(), test_agent_brain_takes_over_turns_and_can_end(), respond(), test_default_transport_is_synchronous_client(), test_end_call_flag_and_local_end_keywords(), test_health_and_key_model_requirements() (+8 more)

### Community 22 - "test_voice_boost_settings.py"
Cohesion: 0.05
Nodes (41): M9 Voice Boost settings surface (2026-11-13, verified this session), Global Constraints, Review Focus, Task 1: Rust Voice Boost settings seam, Task 3: voice_boost.py — models, resolution, session intake plumbing, Task 4: API routes — GET/PUT settings + intake validation, Task 5: Python ↔ Rust seam test (wheel-built path), Task 6: Roadmap + audit doc update (+33 more)

### Community 23 - "test_sync_uses_adapter_and_imports_contacts"
Cohesion: 0.20
Nodes (7): ConnectionTest, ContactPage, test_sync_uses_adapter_and_imports_contacts(), capabilities(), describe_fields(), list_contacts(), test_connection()

### Community 24 - "td_events.rs"
Cohesion: 0.09
Nodes (18): age_never_goes_negative(), DetectionSource, BargeIn, Controller, Endpointing, Semantic, Vad, event_carries_schema_version_and_full_identity() (+10 more)

### Community 25 - "openrouter.py"
Cohesion: 0.14
Nodes (9): AgentReply, AgentTurnError, MockAgent, turn_from_reply(), create_agent_from_env(), _parse_reply(), _safe_float(), _strip_json() (+1 more)

### Community 26 - "super"
Cohesion: 0.14
Nodes (20): barge_in_allowed_after_guard(), barge_in_guard_blocks_early_interrupt(), full_conversation_lifecycle(), invalid_transitions_rejected(), InvalidSignal, many_turns_counted(), round_trip(), Signal (+12 more)

### Community 27 - "LeadConnectorCrmAdapter"
Cohesion: 0.25
Nodes (6): CrmError, build_crm_adapter(), LeadConnectorCrmAdapter, _string(), test_capabilities_are_honest(), test_registry_builds_the_adapter()

### Community 28 - "package.json"
Cohesion: 0.09
Nodes (22): dependencies, react, react-dom, devDependencies, @types/react, @types/react-dom, typescript, vite (+14 more)

### Community 29 - "App.tsx"
Cohesion: 0.10
Nodes (24): AgentRow, CallRow, Campaign, CampaignRow, ContactRow, Envelope, TurnConfig, TurnEngineView (+16 more)

### Community 30 - "TurnDetectionEngine"
Cohesion: 0.05
Nodes (28): Development, Intelligent turn detection (Rust engine), Not implemented (read this), Packaging: pip and uv, Quick start (Docker), Quick start (local, no Docker), Repo layout, Voxora Studio (+20 more)

### Community 31 - "typing"
Cohesion: 0.14
Nodes (6): build_url(), HttpResponse, HttpTransport, UrllibTransport, _http_error(), _http_error()

### Community 33 - "Task 1: Model-profile dispatch + usage rows (P8)"
Cohesion: 0.09
Nodes (16): Task 1: Model-profile dispatch + usage rows (P8), builtin_model_profiles(), _configured_profile_names(), resolve_model_profile(), seed_model_profiles(), UnknownModelProfileError, _decide_payload(), _openrouter_client() (+8 more)

### Community 34 - "logging"
Cohesion: 0.19
Nodes (6): WAV contract, JsonFormatter, _format_with(), test_standard_extra_fields_are_promoted(), test_standard_logrecord_attributes_are_not_leaked(), test_underscore_prefixed_extras_keep_legacy_promotion()

### Community 35 - "wav.rs"
Cohesion: 0.14
Nodes (18): custom_header_goofiness_detected(), duration_at_8khz(), encode_pcm16_mono(), inspect(), le_u16(), le_u32(), rejects_garbage(), rejects_non_pcm16() (+10 more)

### Community 36 - "audio_batches.py"
Cohesion: 0.11
Nodes (12): download_audio_batch_report(), batch_report_csv(), _mock_wav(), compute_speech_analytics(), _decode_wav(), _rms_per_window(), _wav_blob(), test_mock_provider_returns_valid_wav() (+4 more)

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
Nodes (13): M3 — Real voice (the hard part), TelephonyEvent, _at(), extension(), telephony_result_from_runtime_json(), VoiceRuntimeTelephonyAdapter, _canonical_payload(), _request() (+5 more)

### Community 41 - "_error_body"
Cohesion: 0.12
Nodes (17): _connection_view(), ai_decide(), api_error_handler(), create_connection(), get_connection(), http_error_handler(), list_connections(), squid_webhook() (+9 more)

### Community 42 - "CanonicalContact"
Cohesion: 0.09
Nodes (20): CanonicalContact, CrmAdapter, CrmCapabilities, NotSupportedError, WebhookVerification, sync_connection(), _contact(), InMemoryCrmAdapter (+12 more)

### Community 43 - "test_api.py"
Cohesion: 0.07
Nodes (26): CI on GitHub (Phase 1), Consent/suppression sync honesty (Phase 3), Console generated client + postgres compose (Phase 2), Not done / unchanged (still honest), Phase 1–3 update (2026-11-01, verified this session), Request-model tightening (Phase 3), Scheduling (Phase 2), Twilio telephony adapter (Phase 5, verified 2026-11-01) (+18 more)

### Community 44 - "security.py"
Cohesion: 0.18
Nodes (9): parse_squid_webhook(), redact(), safe_equal(), squid_signature(), twilio_signature(), utc_token(), VerifiedWebhook, verify_squid_signature() (+1 more)

### Community 45 - "SquidCrmAdapter"
Cohesion: 0.19
Nodes (6): crm_providers(), provider_catalog(), _path(), SquidCrmAdapter, _string(), process_squid_event()

### Community 46 - "test_voice_sessions.py"
Cohesion: 0.25
Nodes (12): _grant_consent(), _seed_agent(), _session_payload(), test_consented_client_is_callable_after_audience_import_never_widens(), test_instructions_are_recorded_and_rejected_with_provider_capabilities(), test_session_creates_call_completes_simulated_and_records_envelope_events(), test_session_intake_is_idempotent_on_external_id(), test_terminate_is_idempotent_and_sessions_events_stay_ordered() (+4 more)

### Community 47 - "apiTypes.ts"
Cohesion: 0.05
Nodes (35): AgentCreate, AiDecideRequest, AudienceConsent, AudienceContact, AudienceImport, Body_create_audio_batch_api_v1_audio_batches_post, Body_preview_audio_batch_api_v1_audio_batches_preview_post, CampaignCreate (+27 more)

### Community 49 - "build_tts_provider"
Cohesion: 0.10
Nodes (20): Task 2: TTS provider abstraction — mock + OpenAI, audio_provider_status(), generate_audio_asset_long(), retry_audio_asset_failed(), build_tts_provider(), OpenAITTSProvider, TTSProvider, TTSRequest (+12 more)

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
Cohesion: 0.19
Nodes (8): Biquad, clipping_detector_counts_rail_samples(), de_esser_attenuates_sibilance_only(), DeEsser, lowcut_removes_dc_component(), media_bridge_with_voice_boost_still_completes_turns(), presence_eq_boosts_speech_band_but_not_bass(), sine()

### Community 60 - "SecretBox"
Cohesion: 0.43
Nodes (3): SecretBox, test_encrypts_and_decrypts(), test_wrong_key_rejected()

### Community 61 - "test_ghl_adapter.py"
Cohesion: 0.29
Nodes (8): _adapter(), FakeLeadConnectorTransport, test_connection_success_and_auth_failure(), test_connection_test_reports_bad_location(), test_get_contact_and_suppression_states(), test_list_contacts_maps_and_pages_by_id_cursor(), test_requests_carry_auth_and_version_header(), test_sync_connection_applies_ghl_suppression()

### Community 62 - "json"
Cohesion: 0.57
Nodes (3): test_disabled_settings_map_to_rust_bypass(), test_resolved_settings_drive_rust_config(), test_seam_rejects_the_same_bad_input_the_api_rejects()

### Community 63 - "test_squid_adapter.py"
Cohesion: 0.29
Nodes (10): adapter(), FakeSquidTransport, test_capabilities_declared(), test_create_contact_sends_idempotency_key(), test_field_discovery_marks_unsupported_capabilities(), test_list_contacts_maps_verified_person_shape(), test_rate_limit_respects_retry_after_header(), test_retry_exhaustion_raises_crm_error() (+2 more)

### Community 64 - "TurnDetectionState"
Cohesion: 0.14
Nodes (13): TurnDetectionState, AiSpeaking, AiThinking, Ended, Idle, Interrupted, InterruptionCandidate, InTurn (+5 more)

### Community 66 - "pathlib"
Cohesion: 0.48
Nodes (3): _settings(), test_metrics_exposes_expected_families(), test_metrics_requires_a_token()

### Community 67 - "Ecosystem Migration — Capability Inventory (Phase 1)"
Cohesion: 0.29
Nodes (6): 1. MudFish (only upstream crawler; keep its lane), 2. QuillSift (only document/knowledge processing; keep its lane), 3. Squid CRM (owns contacts, segmentation, audiences, eligibility records; never executes calls), 4. Voxora Studio (owns telephony, voice agents, OpenRouter, call state, campaigns; never interview methodology), 5. PyInterviewBot (owns interview methodology only; consumes Voxora, never telephony/LLM-provider infra), Ecosystem Migration — Capability Inventory (Phase 1)

### Community 68 - "generate_ts_client.py"
Cohesion: 0.32
Nodes (7): build_app(), _clean_null_anyof(), generate_paths(), main(), path_constant(), ts_object(), ts_type()

### Community 69 - "test_webhook_replay.py"
Cohesion: 0.44
Nodes (11): _contact_names(), _create_connection(), _delivery(), _person_payload(), _post(), _secret(), _sign(), test_duplicate_delivery_of_same_event_is_noop() (+3 more)

### Community 70 - ".process"
Cohesion: 0.29
Nodes (14): Agc, agc_boosts_quiet_speech_to_target_without_clipping(), bypass_preserves_bytes_exactly(), chunk_split_processing_is_deterministic(), empty_input_is_safe(), i16_of(), lcg_noise(), nnnoiseless_preserves_clean_speech_tone() (+6 more)

### Community 72 - "Implementation Audit (honest verification record)"
Cohesion: 0.14
Nodes (14): AI runtime, Assumptions made (not verified), Console (React + Vite + TS), Docker Compose (executed after the first audit pass), Environment (verified), Honest gaps vs. master prompt, Implementation Audit (honest verification record), M10 Audio Generation, Phase C — non-destructive variants (2026-11-13, verified this session) (+6 more)

### Community 73 - "test_audio_batches.py"
Cohesion: 0.11
Nodes (19): AudioBatchEngine, BatchCsvError, BatchRateLimiter, parse_batch_csv(), _csv(), _engine(), FailOnceProvider, test_batch_rate_limiter_spaces_calls_with_injected_clock() (+11 more)

### Community 76 - "Settings"
Cohesion: 0.17
Nodes (23): Settings, test_settings_defaults_keep_constructor_compatible(), _make_parent(), _segments_blob(), settings(), _sine_blob(), test_compressed_parent_refused(), test_missing_extension_raises_runtime_error() (+15 more)

### Community 77 - "test_audio_batch_api.py"
Cohesion: 0.08
Nodes (32): _import_payload(), _seed(), test_global_suppression_blocks_contact_with_recorded_rejection(), test_import_creates_campaign_with_provenance_and_preserves_squid_ids(), test_invalid_phone_and_garbage_are_rejected_with_validation_errors(), test_missing_consent_is_recorded_unknown_and_not_silently_widened(), test_reimport_is_idempotent_no_duplicate_members(), test_voice_consent_refusal_marks_suppressed_and_reports_reasons() (+24 more)

### Community 78 - "test_longform.py"
Cohesion: 0.14
Nodes (29): chunk_script(), MockTTSProvider, actor_db(), _chunk_entries(), FailOnceProvider, _mock_frames(), _para(), _payload() (+21 more)

### Community 80 - "SemanticVerdict"
Cohesion: 0.25
Nodes (8): PendingBoundary, SemanticReport, SemanticVerdict, Complete, Correction, Filler, Incomplete, Question

### Community 81 - "Roadmap"
Cohesion: 0.07
Nodes (21): AI runtime (Python), Console (React), CRM integration — Squid (first integration, verified against source), Enforcement of honesty, Goal, Layout, Orchestrator (FastAPI), Voice runtime (Rust) (+13 more)

### Community 82 - "Risks"
Cohesion: 0.25
Nodes (7): Assumptions made and why, Compliance / legal (highest), Open questions, Project / honest-reporting risks, Risks, Risks, Assumptions, and Open Questions, Technical

### Community 83 - "generate_asset"
Cohesion: 0.09
Nodes (26): Audio Generation Phase D (Long-form) Implementation Plan, Global Constraints, Review Focus, Task 1: Chunker (`longform.py` part 1), Task 4: Docs + roadmap, A. Chunk manifest on the asset row + chunk files + assembly (CHOSEN), Anti-goals (failure even if it technically works), API (+18 more)

### Community 84 - "Candidates"
Cohesion: 0.25
Nodes (7): 1. WebRTC Audio Processing Module (via `webrtc-audio-processing` 2.1.0), 2. RNNoise (via `nnnoiseless` 0.5.2), 3. DeepFilterNet3 (via `deep-filter-rt` 0.1.1), 4. SpeexDSP (`speexdsp` rust-av bindings / `aec-rs` vendored C), Candidates, DSP engine evaluation (Voice Boost, M9), Selection

### Community 85 - "AudioGeneration.tsx"
Cohesion: 0.11
Nodes (26): AudioAsset, AudioBatchDefaults, AudioBatchJob, AudioBatchPreview, AudioProviderStatus, AudioGeneration(), AudioView, batchDefaults() (+18 more)

### Community 86 - "_FakePyReverseETL"
Cohesion: 0.25
Nodes (4): _FakePyReverseETL, BaseConnector, ConnectorError, WebhookConnector

### Community 87 - "downsampler_preserves_bandlimited_signal_rms"
Cohesion: 0.24
Nodes (5): Downsampler3, downsampler_preserves_bandlimited_signal_rms(), hermite(), Upsampler3, upsampler_emits_three_outputs_per_input_after_flush()

### Community 88 - "postprocess.rs"
Cohesion: 0.21
Nodes (20): count_clipped(), audio_postprocess_impl(), empty_ops_returns_input_with_diagnostics(), envelope(), normalize_op(), normalize_raises_quiet_signal_to_target_without_new_clipping(), normalize_reduces_gain_when_target_would_clip(), normalize_within_api_range_never_clips() (+12 more)

### Community 89 - "voice_turn_loop.py"
Cohesion: 0.14
Nodes (6): session_status_for(), _reply_ended(), _reply_text(), run_live_turns(), TurnResult, VoiceTurnLoopError

### Community 90 - "TTSProviderError"
Cohesion: 0.10
Nodes (11): Audio Generation Phase A–B Implementation Plan, Global Constraints, Review Focus, Task 1: audio_assets table + Settings storage path, Task 3: audio_assets.py — file store + validation, Task 4: API routes, Task 5: roadmap + audit docs, TTSProviderError (+3 more)

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
Nodes (7): _bool(), _float(), _int(), _json_object_env(), TenantPrincipal, main(), test_voice_runtime_501_leaves_no_session()

### Community 96 - "test_session_budgets.py"
Cohesion: 0.21
Nodes (16): check_budget_exhausted(), enforce_session_budgets(), _prior_budget_termination(), _mk_session(), _terminations(), test_already_terminal_session_is_untouched(), test_breach_terminates_orderly_and_records_usage_idempotently(), test_duration_ceiling_fires_at_threshold() (+8 more)

### Community 97 - "voxora-studio"
Cohesion: 0.67
Nodes (3): voxora-ai-runtime, voxora-orchestrator, voxora-studio

### Community 98 - "create_variant"
Cohesion: 0.19
Nodes (9): _apply_rust_op(), _convert_channels(), create_variant(), _frames_to_i16(), _i16_to_frames(), load_extension(), _read_parent_wav(), _trim_silence() (+1 more)

### Community 99 - "resample.rs"
Cohesion: 0.26
Nodes (15): arbitrary_ratio_upsample_keeps_sine_clean(), downsample_frame_count_matches_ratio(), i16_output_is_clamped_in_range(), identity_rate_returns_input(), kernel(), KERNEL_HALF_WIDTH_ZC, midband_tone_survives_flagship_24k_to_16k_within_half_db(), peak() (+7 more)

### Community 100 - "LiveTurnEngine"
Cohesion: 0.16
Nodes (6): Integration test: PyInterviewBot consumes Voxora (DONE), M7 + M8 Completion Plan (Voxora-Studio), Review fix pass (whole-branch review findings C1, I1–I6), Rulings (pre-registered), Session addendum (post-plan execution), LiveTurnEngine

### Community 101 - "test_audio_assets.py"
Cohesion: 0.24
Nodes (11): validate_audio_blob(), _asset_values(), test_assets_are_tenant_scoped(), test_count_by_status(), test_create_get_roundtrip_decodes_history(), test_delete_returns_true_then_missing(), test_list_paging_and_title_search(), test_update_title_and_status() (+3 more)

### Community 102 - "Any"
Cohesion: 0.17
Nodes (3): _merge_usage(), _reply_agent_latency(), TurnAgent

### Community 107 - "TwilioTelephonyAdapter"
Cohesion: 0.20
Nodes (3): _basic(), _iso(), TwilioTelephonyAdapter

### Community 108 - "model_validator"
Cohesion: 0.14
Nodes (4): AudioAssetGenerate, AudioBatchDefaults, AudioBatchRecord, VariantOpsRequest

### Community 109 - "lifespan"
Cohesion: 0.15
Nodes (5): create_app_from_env(), lifespan(), AudioBatchWorker, database_from_settings(), configure_logging()

### Community 110 - "_voice_session_view"
Cohesion: 0.21
Nodes (9): create_voice_session(), get_voice_session(), post_session_instruction(), run_voice_session_turn(), terminate_voice_session(), _telephony_capabilities(), _voice_session_view(), dispatch_pending_webhooks() (+1 more)

### Community 111 - "_campaign_view"
Cohesion: 0.29
Nodes (10): _campaign_view(), cancel_campaign(), create_campaign(), get_campaign(), list_campaigns(), pause_campaign(), resume_campaign(), schedule_campaign() (+2 more)

### Community 112 - "Review Focus"
Cohesion: 0.22
Nodes (8): Audio Generation Phase C (Variants) Implementation Plan, Global Constraints, Review Focus, Self-review notes, Task 1: Rust offline windowed-sinc resampler, Task 2: Rust `audio_postprocess` pyfunction (normalize, voice_boost, dispatch), Task 3: Python `variants.py` — WAV I/O, trim/channels, variant creation, Task 5: Docs + roadmap + ledger

### Community 113 - "_page"
Cohesion: 0.22
Nodes (8): Task 4: Models + API routes + DB listing, list_audio_assets(), list_audio_batches(), list_audio_variants(), list_calls(), list_contacts(), _page(), _principal_from_request()

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

- **Why does `M9 Voice Boost settings surface (2026-11-13, verified this session)` connect `test_voice_boost_settings.py` to `Implementation Audit (honest verification record)`, `dsp.rs`?**
  _High betweenness centrality (0.306) - this node is a cross-community bridge._
- **Are the 139 inferred relationships involving `Database` (e.g. with `M1 — Make the simulation trustworthy at scale (✅ 2026-11-01)` and `Global Constraints`) actually correct?**
  _`Database` has 139 INFERRED edges - model-reasoned connections that need verification._
- **What connects `@types/react`, `@types/react-dom`, `typescript` to the rest of the system?**
  _86 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `test_voice_providers.py` be split into smaller, more focused modules?**
  _Cohesion score 0.055534987041836355 - nodes in this community are weakly interconnected._
- **Why does `config_from_settings_json()` connect `dsp.rs` to `postprocess.rs`, `VoiceBoostConfig`, `test_voice_boost_settings.py`?**
  _High betweenness centrality (0.306) - this node is a cross-community bridge._
- **Are the 50 inferred relationships involving `create_app()` (e.g. with `build_app()` and `OpenRouterError`) actually correct?**
  _`create_app()` has 50 INFERRED edges - model-reasoned connections that need verification._
- **Should `media.rs` be split into smaller, more focused modules?**
  _Cohesion score 0.05894736842105263 - nodes in this community are weakly interconnected._