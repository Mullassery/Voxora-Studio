import { useCallback, useEffect, useState } from "react";
import { ApiError, Campaign, CallRow, ConsoleApi, ContactRow, Envelope } from "./api";

type Tab = "overview" | "campaigns" | "contacts" | "calls";

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
          {(["overview", "campaigns", "contacts", "calls"] as Tab[]).map((name) => (
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

function Campaigns({ api, onError }: { api: ConsoleApi; onError: (message: string | null) => void }) {
  const [campaigns, setCampaigns] = useState<Campaign[]>([]);

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

  return (
    <section>
      <button onClick={() => void load()}>Refresh</button>
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
