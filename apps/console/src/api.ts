import {
  AgentCreate,
  AudioAssetGenerate,
  CampaignCreate,
  ContactCreate,
  LongFormGenerate,
  ConsentUpdate,
  CrmConnectionCreate,
  MappingItem,
  RecipientAdd,
  SuppressionCreate,
} from "./generated/apiTypes";
import {
  PATH_Agents,
  PATH_AudioAssets,
  PATH_AudioAssetsByAssetId,
  PATH_AudioAssetsByAssetIdDownload,
  PATH_AudioAssetsGenerate,
  PATH_AudioAssetsGenerateLong,
  PATH_AudioBatches,
  PATH_AudioBatchesByBatchId,
  PATH_AudioBatchesByBatchIdReport,
  PATH_AudioBatchesByBatchIdRetryFailed,
  PATH_AudioBatchesPreview,
  PATH_AudioBatchesProcess,
  PATH_AudioProviders,
  PATH_ContactsByContactIdConsent,
  PATH_IntegrationsCrmConnections,
  PATH_IntegrationsCrmConnectionsByConnectionIdMappings,
  PATH_SuppressionByPhone,
  PATH_Campaigns,
  PATH_CampaignsByCampaignId,
  PATH_CampaignsByCampaignIdPause,
  PATH_CampaignsByCampaignIdRecipients,
  PATH_CampaignsByCampaignIdResume,
  PATH_CampaignsByCampaignIdSchedule,
  PATH_CampaignsByCampaignIdStart,
  PATH_CampaignsByCampaignIdValidate,
  PATH_CampaignsByCampaignIdCancel,
  PATH_Contacts,
  PATH_Calls,
  PATH_Overview,
  PATH_Suppression,
  PATH_SystemInfo,
  PATH_TelephonyProviders,
  PATH_VoiceTurnConfig,
  PATH_VoiceTurnEngines,
  PATH_VoiceTurnEnginesByEngineId,
  PATH_VoiceTurnEnginesByEngineIdAgentEvents,
  PATH_VoiceTurnEnginesByEngineIdFrames,
  PATH_VoiceTurnEnginesByEngineIdProviderRecovery,
  PATH_VoiceTurnEnginesByEngineIdTranscript,
} from "./generated/paths";

export type {
  AgentCreate,
  AudioAssetGenerate,
  CampaignCreate,
  ContactCreate,
  ConsentUpdate,
  CrmConnectionCreate,
  LongFormGenerate,
  MappingItem,
  RecipientAdd,
  SuppressionCreate,
};
export interface PageInfo {
  total: number;
  limit: number;
  offset: number;
}

export interface Envelope<T> {
  data: T;
  pageInfo: PageInfo;
}

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/**
 * Thin fetch wrapper over the generated OpenAPI paths.
 *
 * Response payload types stay explicit here because the orchestrator's OpenAPI
 * responses are currently untyped (`additionalProperties: true`); request
 * payloads come straight from the generated schema types.
 */
export class ConsoleApi {
  private token: string;

  constructor(token: string) {
    this.token = token;
  }

  private async request<T>(
    path: string,
    init?: { method?: string; body?: unknown },
  ): Promise<T> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.token}`,
      ...(init?.body !== undefined ? { "Content-Type": "application/json" } : {}),
    };
    const response = await fetch(path, {
      method: init?.method ?? "GET",
      headers,
      ...(init?.body !== undefined ? { body: JSON.stringify(init.body) } : {}),
    });
    if (!response.ok) {
      const { message, code } = await this.problem(response);
      throw new ApiError(message, response.status, code);
    }
    return (await response.json()) as T;
  }

  private async problem(response: Response): Promise<{ message: string; code?: string }> {
    let message = `HTTP ${response.status}`;
    let code: string | undefined;
    try {
      const problem = (await response.json()) as {
        messages?: string[];
        detail?: string;
        error?: { code?: string; message?: string };
      };
      if (problem.error?.message) message = problem.error.message;
      else if (problem.messages?.length) message = problem.messages.join(", ");
      else if (problem.detail) message = problem.detail;
      code = problem.error?.code;
    } catch {
      code = undefined;
    }
    return { message, code };
  }

  private async requestBlob(path: string): Promise<Blob> {
    const response = await fetch(path, {
      headers: { Authorization: `Bearer ${this.token}` },
    });
    if (!response.ok) {
      const { message, code } = await this.problem(response);
      throw new ApiError(message, response.status, code);
    }
    return response.blob();
  }

  agents(values: AgentCreate): Promise<{ id: string; name: string }> {
    return this.request(PATH_Agents, { method: "POST", body: values });
  }

  listAgents(): Promise<Envelope<AgentRow[]>> {
    return this.request(`${PATH_Agents}?limit=20`);
  }

  campaigns(): Promise<Envelope<CampaignRow[]>> {
    return this.request(`${PATH_Campaigns}?limit=50`);
  }

  createCampaign(values: CampaignCreate): Promise<Campaign> {
    return this.request(PATH_Campaigns, { method: "POST", body: values });
  }

  campaign(id: string): Promise<Campaign> {
    return this.request(PATH_CampaignsByCampaignId({ campaign_id: id }));
  }

  campaignAction(
    id: string,
    action: "start" | "pause" | "resume" | "cancel" | "schedule",
  ): Promise<Campaign> {
    const paths = {
      start: PATH_CampaignsByCampaignIdStart,
      pause: PATH_CampaignsByCampaignIdPause,
      resume: PATH_CampaignsByCampaignIdResume,
      cancel: PATH_CampaignsByCampaignIdCancel,
      schedule: PATH_CampaignsByCampaignIdSchedule,
    } as const;
    return this.request(paths[action]({ campaign_id: id }), { method: "POST" });
  }

  addRecipients(id: string, values: RecipientAdd): Promise<{ added: number }> {
    return this.request(PATH_CampaignsByCampaignIdRecipients({ campaign_id: id }), {
      method: "POST",
      body: values,
    });
  }

  validateCampaign(id: string): Promise<unknown> {
    return this.request(PATH_CampaignsByCampaignIdValidate({ campaign_id: id }));
  }

  contacts(limit = 25): Promise<Envelope<ContactRow[]>> {
    return this.request(`${PATH_Contacts}?limit=${limit}`);
  }

  importContacts(values: ContactCreate[]): Promise<unknown> {
    return this.request(`${PATH_Contacts}/import`, { method: "POST", body: { contacts: values } });
  }

  updateConsent(contactId: string, values: ConsentUpdate): Promise<unknown> {
    return this.request(PATH_ContactsByContactIdConsent({ contact_id: contactId }), {
      method: "PATCH",
      body: values,
    });
  }

  calls(campaignId?: string): Promise<Envelope<CallRow[]>> {
    return this.request(
      campaignId
        ? `${PATH_Calls}?campaign_id=${encodeURIComponent(campaignId)}&limit=25`
        : `${PATH_Calls}?limit=25`,
    );
  }

  addSuppression(values: SuppressionCreate): Promise<unknown> {
    return this.request(PATH_Suppression, { method: "POST", body: values });
  }

  deleteSuppression(phone: string): Promise<unknown> {
    return this.request(PATH_SuppressionByPhone({ phone }), { method: "DELETE" });
  }

  providers(): Promise<unknown> {
    return this.request(PATH_TelephonyProviders);
  }

  overview(): Promise<unknown> {
    return this.request(PATH_Overview);
  }

  systemInfo(): Promise<Record<string, unknown>> {
    return this.request(PATH_SystemInfo);
  }

  createConnection(values: CrmConnectionCreate): Promise<unknown> {
    return this.request(PATH_IntegrationsCrmConnections, { method: "POST", body: values });
  }

  replaceMappings(connectionId: string, mappings: MappingItem[]): Promise<unknown> {
    return this.request(PATH_IntegrationsCrmConnectionsByConnectionIdMappings({
      connection_id: connectionId,
    }), { method: "PUT", body: { mappings } });
  }

  turnConfig(): Promise<TurnConfig> {
    return this.request(PATH_VoiceTurnConfig);
  }

  createTurnEngine(callId: string): Promise<TurnEngineView> {
    return this.request(PATH_VoiceTurnEngines, { method: "POST", body: { call_id: callId } });
  }

  listTurnEngines(): Promise<Envelope<TurnEngineView[]>> {
    return this.request(PATH_VoiceTurnEngines);
  }

  engineAction(engineId: string, action: string, body: Record<string, unknown> = {}): Promise<TurnFeedResult> {
    const paths = {
      speak: PATH_VoiceTurnEnginesByEngineIdFrames,
      silence: PATH_VoiceTurnEnginesByEngineIdFrames,
      transcript: PATH_VoiceTurnEnginesByEngineIdTranscript,
      tts: PATH_VoiceTurnEnginesByEngineIdAgentEvents,
      recovery: PATH_VoiceTurnEnginesByEngineIdProviderRecovery,
    } as const;
    return this.request(paths[action as keyof typeof paths]({ engine_id: engineId }), {
      method: "POST",
      body,
    });
  }

  deleteTurnEngine(engineId: string): Promise<unknown> {
    return this.request(PATH_VoiceTurnEnginesByEngineId({ engine_id: engineId }), { method: "DELETE" });
  }

  audioProviders(): Promise<AudioProviderStatus> {
    return this.request(PATH_AudioProviders);
  }

  generateAudio(values: AudioAssetGenerate): Promise<AudioAsset> {
    return this.request(PATH_AudioAssetsGenerate, { method: "POST", body: values });
  }

  generateAudioLong(values: LongFormGenerate): Promise<AudioAsset> {
    return this.request(PATH_AudioAssetsGenerateLong, { method: "POST", body: values });
  }

  listAudioAssets(params: { search?: string; status?: string; limit?: number; offset?: number } = {}): Promise<Envelope<AudioAsset[]>> {
    const query = new URLSearchParams();
    if (params.search) query.set("search", params.search);
    if (params.status) query.set("status", params.status);
    query.set("limit", String(params.limit ?? 50));
    query.set("offset", String(params.offset ?? 0));
    return this.request(`${PATH_AudioAssets}?${query}`);
  }

  deleteAudioAsset(id: string): Promise<unknown> {
    return this.request(PATH_AudioAssetsByAssetId({ asset_id: id }), { method: "DELETE" });
  }

  downloadAudioAsset(id: string): Promise<Blob> {
    return this.requestBlob(PATH_AudioAssetsByAssetIdDownload({ asset_id: id }));
  }

  previewAudioBatch(file: File, defaults: AudioBatchDefaults): Promise<AudioBatchPreview> {
    return this.uploadAudioBatch(PATH_AudioBatchesPreview, file, defaults);
  }

  createAudioBatch(file: File, defaults: AudioBatchDefaults): Promise<AudioBatchJob> {
    return this.uploadAudioBatch(PATH_AudioBatches, file, defaults);
  }

  listAudioBatches(limit = 50, offset = 0): Promise<Envelope<AudioBatchJob[]>> {
    return this.request(`${PATH_AudioBatches}?limit=${limit}&offset=${offset}`);
  }

  audioBatch(id: string): Promise<AudioBatchJob> {
    return this.request(PATH_AudioBatchesByBatchId({ batch_id: id }));
  }

  processAudioBatchNow(): Promise<{ processed: number; claimSize: number }> {
    return this.request(PATH_AudioBatchesProcess, { method: "POST" });
  }

  retryAudioBatch(id: string): Promise<AudioBatchJob> {
    return this.request(PATH_AudioBatchesByBatchIdRetryFailed({ batch_id: id }), { method: "POST" });
  }

  downloadBatchReport(id: string): Promise<Blob> {
    return this.requestBlob(PATH_AudioBatchesByBatchIdReport({ batch_id: id }));
  }

  private async uploadAudioBatch<T>(path: string, file: File, defaults: AudioBatchDefaults): Promise<T> {
    const form = new FormData();
    form.append("file", file);
    form.append("defaults", JSON.stringify(defaults));
    const response = await fetch(path, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.token}` },
      body: form,
    });
    if (!response.ok) {
      const { message, code } = await this.problem(response);
      throw new ApiError(message, response.status, code);
    }
    return (await response.json()) as T;
  }
}

export interface AudioProviderStatus {
  configured_provider: string;
  configured: boolean;
  live: boolean;
  simulated: boolean;
  detail?: string | null;
}

export interface AudioAsset {
  id: string;
  title: string;
  script: string;
  provider: string;
  model: string;
  voice: string;
  language: string | null;
  speed: number;
  format: string;
  duration_seconds: number | null;
  file_size: number | null;
  status: "ready" | "processing" | "failed";
  parent_asset_id: string | null;
  processing_history: Record<string, unknown>[];
  created_at: string;
  updated_at: string;
}

export interface AudioBatchDefaults {
  voice?: string;
  model?: "tts-1" | "tts-1-hd" | "gpt-4o-mini-tts";
  speed?: number;
  response_format?: "mp3" | "opus" | "aac" | "flac" | "wav" | "pcm";
  instructions?: string;
  language?: string;
  title?: string;
}

export interface AudioBatchPreview {
  filename: string;
  columns: string[];
  rowCount: number;
  validCount: number;
  errors: string[];
  preview: Record<string, unknown>[];
}

export interface AudioBatchItem {
  id: string;
  batch_id: string;
  row_number: number;
  title: string;
  script: string;
  voice: string;
  language: string | null;
  status: "queued" | "processing" | "ready" | "failed";
  asset_id: string | null;
  error: string | null;
  attempts: number;
  rate_limited: number;
  next_attempt_at: string | null;
  settings: Record<string, unknown>;
}

export interface AudioBatchJob {
  id: string;
  filename: string;
  status: "queued" | "processing" | "completed";
  defaults: AudioBatchDefaults;
  total: number;
  completed: number;
  failed: number;
  rate_limited: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  completed_at: string | null;
  items?: AudioBatchItem[];
}

export interface TurnConfig {
  engineAvailable: boolean;
  engineVersion: string | null;
  profile: string;
  semanticEnabled: boolean;
  wordGateMinWords: number;
  schemaVersion: number;
  activeEngines: number;
  engineCapacity: number;
}

export interface TurnEvent {
  schema_version: number;
  turn_id: number;
  generation: number;
  timestamp_ms: number;
  event_type: string;
  state: string;
  source: string;
  confidence?: number | null;
  metadata: Record<string, unknown>;
}

export interface TurnEngineView extends Record<string, unknown> {
  id: string;
  state: string;
  turn_id: number;
  turns_completed: number;
  interruptions: number;
}

export interface TurnFeedResult {
  events: TurnEvent[];
  snapshot: Record<string, unknown>;
}

export interface Campaign {
  id: string;
  name: string;
  status: string;
  dryRun: boolean;
  recipients: Record<string, number>;
}

export interface CampaignRow extends Campaign {
  agentId: string;
}

export interface AgentRow {
  id: string;
  name: string;
  systemPrompt: string;
  model: string;
}

export interface ContactRow {
  id: string;
  firstName: string;
  lastName: string;
  phone: string;
  consentState: string;
}

export interface CallRow {
  id: string;
  campaignId: string;
  status: string;
  disposition: string | null;
  simulated: boolean;
  createdAt: string;
}