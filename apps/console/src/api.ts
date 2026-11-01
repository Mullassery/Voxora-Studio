import {
  AgentCreate,
  CampaignCreate,
  ContactCreate,
  ConsentUpdate,
  CrmConnectionCreate,
  MappingItem,
  RecipientAdd,
  SuppressionCreate,
} from "./generated/apiTypes";
import {
  PATH_Agents,
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
} from "./generated/paths";

export type {
  AgentCreate,
  CampaignCreate,
  ContactCreate,
  ConsentUpdate,
  CrmConnectionCreate,
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

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
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
      let message = `HTTP ${response.status}`;
      try {
        const problem = (await response.json()) as { messages?: string[]; detail?: string };
        if (problem.messages?.length) message = problem.messages.join(", ");
        else if (problem.detail) message = problem.detail;
      } catch {
        /* non-JSON error body keeps the status message */
      }
      throw new ApiError(message, response.status);
    }
    return (await response.json()) as T;
  }

  agents(values: AgentCreate): Promise<{ id: string; name: string }> {
    return this.request(PATH_Agents, { method: "POST", body: values });
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