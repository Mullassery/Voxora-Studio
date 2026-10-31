export interface ApiEnvelope<T> {
  data: T;
}

export interface PageInfo {
  total: number;
  limit: number;
  offset: number;
}

export interface Envelope<T> extends ApiEnvelope<T> {
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

export class ConsoleApi {
  private token: string;

  constructor(token: string) {
    this.token = token;
  }

  private async request<T>(path: string, init?: RequestInit): Promise<T> {
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.token}`,
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    };
    const response = await fetch(path, { ...init, headers });
    if (!response.ok) {
      let message = `HTTP ${response.status}`;
      try {
        const problem = (await response.json()) as problemBody;
        if (problem.messages?.length) message = problem.messages.join(", ");
      } catch {
        /* non-JSON error body keeps the status message */
      }
      throw new ApiError(message, response.status);
    }
    return (await response.json()) as T;
  }

  overview(): Promise<unknown> {
    return this.request("/api/v1/overview");
  }

  campaigns(): Promise<Envelope<CampaignRow[]>> {
    return this.request("/api/v1/campaigns?limit=50");
  }

  campaignAction(id: string, action: "start" | "pause" | "resume" | "cancel"): Promise<Campaign> {
    return this.request(`/api/v1/campaigns/${id}/${action}`, { method: "POST" });
  }

  contacts(): Promise<Envelope<ContactRow[]>> {
    return this.request("/api/v1/contacts?limit=25");
  }

  calls(campaignId?: string): Promise<Envelope<CallRow[]>> {
    const query = campaignId ? `?campaign_id=${encodeURIComponent(campaignId)}&limit=25` : "?limit=25";
    return this.request(`/api/v1/calls${query}`);
  }

  systemInfo(): Promise<Record<string, unknown>> {
    return this.request("/api/v1/system/info");
  }
}

interface problemBody {
  messages?: string[];
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
