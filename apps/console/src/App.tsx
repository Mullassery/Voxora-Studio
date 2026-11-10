import { useCallback, useEffect, useState } from "react";
import {
  AgentRow,
  ApiError,
  Campaign,
  CallRow,
  ConsoleApi,
  ContactRow,
  Envelope,
  TurnConfig,
  TurnEngineView,
  TurnEvent,
  TurnFeedResult,
} from "./api";

interface CampaignTemplate {
  label: string;
  name: string;
  maxConcurrency: number;
  maxAttempts: number;
  retryBackoffSeconds: number;
  description: string;
}

// Prefill presets; every campaign still defaults to dry run for safety.
const TEMPLATES: CampaignTemplate[] = [
  {
    label: "Welcome outreach",
    name: "Welcome calls",
    maxConcurrency: 2,
    maxAttempts: 2,
    retryBackoffSeconds: 180,
    description: "First-contact greet call, low touch.",
  },
  {
    label: "Winback",
    name: "Winback follow-ups",
    maxConcurrency: 3,
    maxAttempts: 3,
    retryBackoffSeconds: 3600,
    description: "Re-engage dormant contacts with retries.",
  },
  {
    label: "Reminder",
    name: "Appointment reminders",
    maxConcurrency: 4,
    maxAttempts: 1,
    retryBackoffSeconds: 60,
    description: "No retries; same-week reminders.",
  },
];

type Tab = "overview" | "campaigns" | "contacts" | "calls" | "voice";

const TOKEN_KEY = "voxora-token";
const ACTIONS = ["start", "pause", "resume", "cancel"] as const;

export function App() {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY) ?? "");
  const [api, setApi] = useState<ConsoleApi | null>(() => {
    const stored = localStorage.getItem(TOKEN_KEY);
    return stored ? new ConsoleApi(stored) : null;
  });
  const [tab, setTab] = useState<Tab>("overview");
  const [error, setError] = useState<string | null>(null);
  const [overview, setOverview] = useState<string | null>(null);

  const login = useCallback(() => {
    if (!token.trim()) {
      setError("Enter an API token (config: VOXORA_API_TOKENS).");
      return;
    }
    localStorage.setItem(TOKEN_KEY, token.trim());
    setApi(new ConsoleApi(token.trim()));
    setError(null);
  }, [token]);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    setApi(null);
    setOverview(null);
    setError(null);
  }, []);

  const refreshOverview = useCallback(
    async (client: ConsoleApi) => {
      try {
        const info = (await client.systemInfo()) as Record<string, unknown>;
        const settings = info.settings as Record<string, unknown> | undefined;
        const summary = JSON.stringify(
          { version: info.version, seeded: info.seeded, ...settings },
          null,
          2,
        );
        setOverview(summary);
        setError(null);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : String(err));
      }
    },
    [],
  );

  useEffect(() => {
    if (api) void refreshOverview(api);
  }, [api, refreshOverview]);

  if (!api) {
    return (
      <main className="login">
        <h1>Voxora Studio</h1>
        <p className="muted">
          Operator console for simulated outbound calling. Calls are always simulated in this
          build; no live telephony is wired.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            login();
          }}
        >
          <input
            type="password"
            placeholder="API token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoFocus
          />
          <button type="submit">Connect</button>
        </form>
        {error && <p className="error">{error}</p>}
      </main>
    );
  }

  return (
    <main>
      <header>
        <h1>Voxora Studio</h1>
        <nav>
          {(["overview", "campaigns", "contacts", "calls", "voice"] as Tab[]).map((name) => (
            <button key={name} className={tab === name ? "active" : ""} onClick={() => setTab(name)}>
              {name}
            </button>
          ))}
        </nav>
        <button className="ghost" onClick={logout}>
          Sign out
        </button>
      </header>
      {error && <p className="error">{error}</p>}
      {tab === "overview" && <Overview summary={overview} onRefresh={() => void refreshOverview(api)} />}
      {tab === "campaigns" && <Campaigns api={api} onError={setError} />}
      {tab === "contacts" && <Contacts api={api} onError={setError} />}
      {tab === "calls" && <Calls api={api} onError={setError} />}
      {tab === "voice" && <VoiceTurn api={api} onError={setError} />}
    </main>
  );
}

function Overview({ summary, onRefresh }: { summary: string | null; onRefresh: () => void }) {
  return (
    <section>
      <button onClick={onRefresh}>Refresh</button>
      <pre>{summary ?? "Loading…"}</pre>
    </section>
  );
}

function CreateCampaign({
  api,
  busy,
  setBusy,
  onCreated,
  onError,
}: {
  api: ConsoleApi;
  busy: boolean;
  setBusy: (busy: boolean) => void;
  onCreated: () => void;
  onError: (message: string | null) => void;
}) {
  const [templateIndex, setTemplateIndex] = useState(0);
  const [agents, setAgents] = useState<AgentRow[]>([]);
  const [agentId, setAgentId] = useState("");
  const template = TEMPLATES[templateIndex];
  const [name, setName] = useState(template.name);
  const [maxConcurrency, setMaxConcurrency] = useState(template.maxConcurrency);
  const [maxAttempts, setMaxAttempts] = useState(template.maxAttempts);
  const [retryBackoff, setRetryBackoff] = useState(template.retryBackoffSeconds);
  const [scheduleAt, setScheduleAt] = useState("");

  const pickTemplate = (index: number) => {
    setTemplateIndex(index);
    const chosen = TEMPLATES[index];
    setName(chosen.name);
    setMaxConcurrency(chosen.maxConcurrency);
    setMaxAttempts(chosen.maxAttempts);
    setRetryBackoff(chosen.retryBackoffSeconds);
  };

  useEffect(() => {
    void (async () => {
      try {
        const page = (await api.listAgents()) as Envelope<AgentRow[]>;
        setAgents(page.data);
        setAgentId(page.data[page.data.length - 1]?.id ?? "");
      } catch {
        onError("No agents available yet — create one from the orchestrator first.");
      }
    })();
  }, [api, onError]);

  const submit = async () => {
    if (!agentId) {
      onError("Select an agent.");
      return;
    }
    setBusy(true);
    try {
      await api.createCampaign({
        name: name.trim() || template.name,
        agent_id: agentId,
        dry_run: true,
        max_concurrency: maxConcurrency,
        max_attempts: maxAttempts,
        retry_backoff_seconds: retryBackoff,
        ...(scheduleAt ? { schedule_at: new Date(scheduleAt).toISOString() } : {}),
      });
      onError(null);
      onCreated();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="create-campaign">
      <label>Template</label>
      <select value={templateIndex} onChange={(e) => pickTemplate(Number(e.target.value))}>
        {TEMPLATES.map((t, index) => (
          <option key={t.label} value={index}>
            {t.label} — {t.description}
          </option>
        ))}
      </select>
      <label>Campaign name</label>
      <input value={name} onChange={(e) => setName(e.target.value)} placeholder={template.name} />
      <label>Agent</label>
      <select value={agentId} onChange={(e) => setAgentId(e.target.value)}>
        {!agents.length && <option value="">(no agents)</option>}
        {agents.map((agent) => (
          <option key={agent.id} value={agent.id}>
            {agent.name}
          </option>
        ))}
      </select>
      <label>Max concurrency</label>
      <input type="number" min={1} value={maxConcurrency} onChange={(e) => setMaxConcurrency(Number(e.target.value))} />
      <label>Max attempts</label>
      <input type="number" min={1} value={maxAttempts} onChange={(e) => setMaxAttempts(Number(e.target.value))} />
      <label>Retry backoff (seconds)</label>
      <input type="number" min={1} value={retryBackoff} onChange={(e) => setRetryBackoff(Number(e.target.value))} />
      <label>Schedule for (empty = manual start)</label>
      <input type="datetime-local" value={scheduleAt} onChange={(e) => setScheduleAt(e.target.value)} />
      <button onClick={() => void submit()} disabled={busy}>
        {busy ? "Creating…" : "Create dry-run campaign"}
      </button>
      <p className="muted">New campaigns always start as dry run; recipients are added after creation.</p>
    </div>
  );
}

function Campaigns({ api, onError }: { api: ConsoleApi; onError: (message: string | null) => void }) {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [creating, setCreating] = useState(false);

  const load = useCallback(async () => {
    try {
      const page = (await api.campaigns()) as Envelope<Campaign[]>;
      setCampaigns(page.data);
      onError(null);
    } catch (err) {
      onError(err instanceof ApiError ? err.message : String(err));
    }
  }, [api, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const act = async (id: string, action: (typeof ACTIONS)[number]) => {
    try {
      await api.campaignAction(id, action);
      await load();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const created = useCallback(() => {
    setShowCreate(false);
    void load();
  }, [load]);

  return (
    <section>
      <button onClick={() => void load()}>Refresh</button>
      <button onClick={() => setShowCreate(!showCreate)}>New campaign</button>
      {showCreate && <CreateCampaign api={api} busy={creating} setBusy={setCreating} onCreated={created} onError={onError} />}
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Status</th>
            <th>Dry run</th>
            <th>Recipients</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {campaigns.map((c) => {
            const counts = c.recipients;
            return (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td>{c.status}</td>
                <td>{c.dryRun ? "yes" : "no"}</td>
                <td>
                  {counts.completed ?? 0}/{counts.total ?? 0} done, {counts.queued ?? 0} queued
                </td>
                <td>
                  {ACTIONS.map((action) => (
                    <button key={action} onClick={() => void act(c.id, action)}>
                      {action}
                    </button>
                  ))}
                </td>
              </tr>
            );
          })}
          {!campaigns.length && (
            <tr>
              <td colSpan={5}>No campaigns yet.</td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function Contacts({ api, onError }: { api: ConsoleApi; onError: (message: string | null) => void }) {
  const [contacts, setContacts] = useState<ContactRow[]>([]);

  useEffect(() => {
    void (async () => {
      try {
        const page = (await api.contacts()) as Envelope<ContactRow[]>;
        setContacts(page.data);
        onError(null);
      } catch (err) {
        onError(err instanceof ApiError ? err.message : String(err));
      }
    })();
  }, [api, onError]);

  return (
    <table>
      <thead>
        <tr>
          <th>Name</th>
          <th>Phone</th>
          <th>Consent</th>
        </tr>
      </thead>
      <tbody>
        {contacts.map((c) => (
          <tr key={c.id}>
            <td>
              {c.firstName} {c.lastName}
            </td>
            <td>{c.phone}</td>
            <td>{c.consentState}</td>
          </tr>
        ))}
        {!contacts.length && (
          <tr>
            <td colSpan={3}>No contacts.</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function Calls({ api, onError }: { api: ConsoleApi; onError: (message: string | null) => void }) {
  const [calls, setCalls] = useState<CallRow[]>([]);

  useEffect(() => {
    void (async () => {
      try {
        const page = (await api.calls()) as Envelope<CallRow[]>;
        setCalls(page.data);
        onError(null);
      } catch (err) {
        onError(err instanceof ApiError ? err.message : String(err));
      }
    })();
  }, [api, onError]);

  return (
    <table>
      <thead>
        <tr>
          <th>Call</th>
          <th>Campaign</th>
          <th>Status</th>
          <th>Disposition</th>
          <th>Simulated</th>
        </tr>
      </thead>
      <tbody>
        {calls.map((c) => (
          <tr key={c.id}>
            <td>{c.id.slice(0, 16)}…</td>
            <td>{c.campaignId.slice(0, 16)}…</td>
            <td>{c.status}</td>
            <td>{c.disposition ?? "—"}</td>
            <td>{c.simulated ? "yes" : "no"}</td>
          </tr>
        ))}
        {!calls.length && (
          <tr>
            <td colSpan={5}>No calls yet.</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function VoiceTurn({ api, onError }: { api: ConsoleApi; onError: (message: string | null) => void }) {
  const [config, setConfig] = useState<TurnConfig | null>(null);
  const [engine, setEngine] = useState<TurnEngineView | null>(null);
  const [events, setEvents] = useState<TurnEvent[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setConfig(await api.turnConfig());
      const page = (await api.listTurnEngines()) as Envelope<TurnEngineView[]>;
      setEngine(page.data[page.data.length - 1] ?? null);
      onError(null);
    } catch (err) {
      onError(err instanceof ApiError ? err.message : String(err));
    }
  }, [api, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    setBusy(true);
    try {
      setEngine(await api.createTurnEngine(`probe-${Date.now()}`));
      setEvents([]);
      await load();
      onError(null);
    } catch (err) {
      onError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  // One scripted turn: speak 10 frames, go silent, endpoint confirms the
  // boundary exactly as the Rust controller unit tests drive it.
  const speakTurn = async () => {
    if (!engine) return;
    setBusy(true);
    try {
      await api.engineAction(engine.id, "transcript", {
        text: "I need an appointment for Tuesday",
        is_final: false,
      });
      const collected: TurnEvent[] = [...events];
      for (let i = 0; i < 10; i++) {
        const result: TurnFeedResult = await api.engineAction(engine.id, "speak", { rms: 0.4 });
        collected.push(...result.events);
      }
      for (let i = 0; i < 45; i++) {
        const result: TurnFeedResult = await api.engineAction(engine.id, "silence", { rms: 0.005 });
        collected.push(...result.events);
      }
      setEvents(collected.slice(-200));
      onError(null);
      await load();
    } catch (err) {
      onError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const endEngine = async () => {
    if (!engine) return;
    setBusy(true);
    try {
      await api.deleteTurnEngine(engine.id);
      setEngine(null);
      await load();
      onError(null);
    } catch (err) {
      onError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section>
      <button onClick={() => void load()}>Refresh</button>
      {config && (
        <dl>
          <dt>Engine: {config.engineAvailable ? `rust ${config.engineVersion}` : "not installed (make py-voice)"}</dt>
          <dt>Profile: {config.profile} · semantic: {String(config.semanticEnabled)} · word gate: {config.wordGateMinWords}</dt>
          <dt>Engines: {config.activeEngines}/{config.engineCapacity}</dt>
        </dl>
      )}
      <div>
        <button onClick={() => void create()} disabled={busy}>New probe engine</button>
        {engine && (
          <>
            <button onClick={() => void speakTurn()} disabled={busy}>Scripted turn</button>
            <button onClick={() => void endEngine()} disabled={busy}>End engine</button>
          </>
        )}
      </div>
      {engine && (
        <table>
          <thead>
            <tr><th>Call</th><th>State</th><th>Turn</th><th>Completed</th><th>Interrupts</th></tr>
          </thead>
          <tbody>
            <tr key={engine.id}>
              <td>{engine.id.slice(0, 16)}…</td>
              <td>{String(engine.state)}</td>
              <td>{String(engine.turn_id)}</td>
              <td>{String(engine.turns_completed)}</td>
              <td>{String(engine.interruptions)}</td>
            </tr>
          </tbody>
        </table>
      )}
      {events.length > 0 && (
        <table>
          <thead>
            <tr><th>t (ms)</th><th>Event</th><th>State</th><th>Source</th></tr>
          </thead>
          <tbody>
            {events.map((event, index) => (
              <tr key={`${event.event_type}-${event.timestamp_ms}-${index}`}>
                <td>{event.timestamp_ms}</td>
                <td>{event.event_type}{event.confidence != null ? ` (${event.confidence.toFixed(2)})` : ""}</td>
                <td>{event.state}</td>
                <td>{event.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {engine && events.length === 0 && <p className="muted">Run a scripted turn to see the detection events.</p>}
    </section>
  );
}
