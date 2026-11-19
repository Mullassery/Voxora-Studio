import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  ApiError,
  AudioAsset,
  AudioAssetGenerate,
  AudioBatchDefaults,
  AudioBatchJob,
  AudioBatchPreview,
  AudioProviderStatus,
  ConsoleApi,
  Envelope,
  LongFormGenerate,
} from "./api";

type AudioView = "generate" | "batch" | "library" | "processing" | "settings";
type OnError = (message: string | null) => void;
type Model = "tts-1" | "tts-1-hd" | "gpt-4o-mini-tts";
type Format = "mp3" | "opus" | "aac" | "flac" | "wav" | "pcm";

interface Props {
  api: ConsoleApi;
  onError: OnError;
}

interface ProviderProps extends Props {
  provider: AudioProviderStatus | null;
}

const VIEWS: AudioView[] = ["generate", "batch", "library", "processing", "settings"];
const MODELS: Model[] = ["tts-1", "tts-1-hd", "gpt-4o-mini-tts"];
const FORMATS: Format[] = ["mp3", "opus", "aac", "flac", "wav", "pcm"];
const CSV_COLUMNS = "script,title,voice,language,speed,model,response_format,instructions";

function describe(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

function saveBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function batchDefaults(values: {
  voice: string;
  model: Model;
  speed: number;
  response_format: Format;
  language: string;
  title: string;
  instructions: string;
}): AudioBatchDefaults {
  return {
    voice: values.voice,
    model: values.model,
    speed: values.speed,
    response_format: values.response_format,
    ...(values.language ? { language: values.language } : {}),
    ...(values.title ? { title: values.title } : {}),
    ...(values.model === "gpt-4o-mini-tts" && values.instructions
      ? { instructions: values.instructions }
      : {}),
  };
}

export function AudioGeneration({ api, onError }: Props) {
  const [view, setView] = useState<AudioView>("generate");
  const [provider, setProvider] = useState<AudioProviderStatus | null>(null);

  const loadProvider = useCallback(async () => {
    try {
      setProvider(await api.audioProviders());
      onError(null);
    } catch (err) {
      onError(describe(err));
    }
  }, [api, onError]);

  useEffect(() => {
    void loadProvider();
  }, [loadProvider]);

  return (
    <section>
      <div className="audio-tabs">
        {VIEWS.map((name) => (
          <button key={name} className={view === name ? "active" : ""} onClick={() => setView(name)}>
            {name}
          </button>
        ))}
      </div>
      <ProviderBanner provider={provider} />
      {view === "generate" && <GenerateView api={api} provider={provider} onError={onError} />}
      {view === "batch" && <BatchView api={api} onError={onError} />}
      {view === "library" && <LibraryView api={api} onError={onError} />}
      {view === "processing" && <ProcessingView api={api} onError={onError} />}
      {view === "settings" && (
        <SettingsView api={api} provider={provider} onRefreshProvider={() => void loadProvider()} onError={onError} />
      )}
    </section>
  );
}

function ProviderBanner({ provider }: { provider: AudioProviderStatus | null }) {
  if (!provider) return <p className="muted">Checking audio provider…</p>;
  if (!provider.configured) {
    return (
      <p className="audio-banner warn">
        No TTS provider configured. Generation returns 501 until one is configured.
      </p>
    );
  }
  if (provider.simulated) {
    return (
      <p className="audio-banner">
        Mock provider: simulated audio. Safe for demos and tests; no paid API calls.
      </p>
    );
  }
  if (provider.live) {
    return (
      <p className="audio-banner warn">
        Live provider configured for {provider.configured_provider}. Generated assets may incur cost.
      </p>
    );
  }
  return <p className="audio-banner">Configured provider: {provider.configured_provider}.</p>;
}

function GenerateView({ api, provider, onError }: ProviderProps) {
  const [script, setScript] = useState("");
  const [title, setTitle] = useState("");
  const [voice, setVoice] = useState("alloy");
  const [model, setModel] = useState<Model>("tts-1");
  const [speed, setSpeed] = useState(1);
  const [format, setFormat] = useState<Format>("wav");
  const [instructions, setInstructions] = useState("");
  const [language, setLanguage] = useState("");
  const [longForm, setLongForm] = useState(false);
  const [asset, setAsset] = useState<AudioAsset | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const common = {
        script,
        voice,
        model,
        speed,
        response_format: format,
        ...(title ? { title } : {}),
        ...(language ? { language } : {}),
        ...(model === "gpt-4o-mini-tts" && instructions ? { instructions } : {}),
      };
      const created = longForm
        ? await api.generateAudioLong(common as LongFormGenerate)
        : await api.generateAudio(common as AudioAssetGenerate);
      setAsset(created);
      onError(null);
    } catch (err) {
      onError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  const download = async () => {
    if (!asset) return;
    try {
      saveBlob(await api.downloadAudioAsset(asset.id), `${asset.title}.${asset.format}`);
      onError(null);
    } catch (err) {
      onError(describe(err));
    }
  };

  return (
    <div className="audio-view">
      <form className="audio-form" onSubmit={(event) => void submit(event)}>
        <label>
          Script
          <textarea value={script} onChange={(event) => setScript(event.target.value)} rows={5} required />
        </label>
        <div className="audio-grid">
          <label>
            Title
            <input value={title} onChange={(event) => setTitle(event.target.value)} maxLength={120} />
          </label>
          <label>
            Voice
            <input value={voice} onChange={(event) => setVoice(event.target.value)} required />
          </label>
          <label>
            Model
            <select value={model} onChange={(event) => setModel(event.target.value as Model)}>
              {MODELS.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            Format
            <select value={format} onChange={(event) => setFormat(event.target.value as Format)}>
              {FORMATS.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            Speed
            <input
              type="number"
              min={0.25}
              max={4}
              step={0.25}
              value={speed}
              onChange={(event) => setSpeed(Number(event.target.value))}
            />
          </label>
          <label>
            Language
            <input value={language} onChange={(event) => setLanguage(event.target.value)} maxLength={40} />
          </label>
        </div>
        <label>
          Instructions
          <textarea
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            disabled={model !== "gpt-4o-mini-tts"}
            rows={2}
            maxLength={4096}
          />
        </label>
        <p className="muted">
          Instructions are accepted only by gpt-4o-mini-tts. Long scripts use paragraph-preserving
          chunking and may need a retry for failed chunks.
        </p>
        <div className="audio-actions">
          <label className="inline">
            <input type="checkbox" checked={longForm} onChange={(event) => setLongForm(event.target.checked)} />
            Long-form
          </label>
          <button type="submit" disabled={busy || !provider?.configured}>
            Generate
          </button>
        </div>
      </form>
      {asset && (
        <div className="audio-result">
          <h3>{asset.title}</h3>
          <pre>{JSON.stringify({
            id: asset.id,
            status: asset.status,
            provider: asset.provider,
            model: asset.model,
            voice: asset.voice,
            format: asset.format,
            duration_seconds: asset.duration_seconds,
            file_size: asset.file_size,
          }, null, 2)}</pre>
          <button onClick={() => void download()} disabled={asset.status !== "ready"}>
            Download
          </button>
          {asset.status === "processing" && (
            <span className="muted">Chunked run still processing; retry failed chunks from Processing.</span>
          )}
        </div>
      )}
    </div>
  );
}

function BatchView({ api, onError }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [voice, setVoice] = useState("alloy");
  const [model, setModel] = useState<Model>("tts-1");
  const [speed, setSpeed] = useState(1);
  const [format, setFormat] = useState<Format>("wav");
  const [language, setLanguage] = useState("");
  const [title, setTitle] = useState("");
  const [instructions, setInstructions] = useState("");
  const [preview, setPreview] = useState<AudioBatchPreview | null>(null);
  const [job, setJob] = useState<AudioBatchJob | null>(null);
  const [busy, setBusy] = useState(false);

  const defaults = (): AudioBatchDefaults =>
    batchDefaults({ voice, model, speed, response_format: format, language, title, instructions });

  const run = async (kind: "preview" | "create") => {
    if (!file) {
      onError("Choose a CSV file first.");
      return;
    }
    setBusy(true);
    try {
      if (kind === "preview") {
        setPreview(await api.previewAudioBatch(file, defaults()));
        setJob(null);
      } else {
        setJob(await api.createAudioBatch(file, defaults()));
        setPreview(null);
      }
      onError(null);
    } catch (err) {
      onError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="audio-view">
      <form className="audio-form" onSubmit={(event) => { event.preventDefault(); void run("preview"); }}>
        <label>
          CSV file
          <input type="file" accept=".csv,text/csv" onChange={(event) => setFile(event.target.files?.[0] ?? null)} />
        </label>
        <p className="muted">Columns: <code>{CSV_COLUMNS}</code>. Only <code>script</code> is required.</p>
        <div className="audio-grid">
          <label>Voice<input value={voice} onChange={(event) => setVoice(event.target.value)} /></label>
          <label>
            Model
            <select value={model} onChange={(event) => setModel(event.target.value as Model)}>
              {MODELS.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>
            Format
            <select value={format} onChange={(event) => setFormat(event.target.value as Format)}>
              {FORMATS.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </label>
          <label>Speed<input type="number" min={0.25} max={4} step={0.25} value={speed} onChange={(event) => setSpeed(Number(event.target.value))} /></label>
          <label>Language<input value={language} onChange={(event) => setLanguage(event.target.value)} /></label>
          <label>Default title<input value={title} onChange={(event) => setTitle(event.target.value)} /></label>
        </div>
        <label>
          Instructions
          <textarea
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            disabled={model !== "gpt-4o-mini-tts"}
            rows={2}
          />
        </label>
        <div className="audio-actions">
          <button type="submit" disabled={busy || !file}>Preview CSV</button>
          <button type="button" onClick={() => void run("create")} disabled={busy || !file}>Queue batch</button>
        </div>
      </form>
      {preview && (
        <div className="audio-result">
          <h3>Preview: {preview.filename}</h3>
          <p>{preview.validCount} valid of {preview.rowCount} rows</p>
          {preview.errors.length > 0 && <pre>{preview.errors.join("\n")}</pre>}
          <table>
            <thead><tr><th>Row</th><th>Title</th><th>Voice</th><th>Script</th></tr></thead>
            <tbody>
              {preview.preview.map((row, index) => (
                <tr key={index}>
                  <td>{index + 1}</td>
                  <td>{String(row.title ?? "")}</td>
                  <td>{String(row.voice ?? "")}</td>
                  <td>{String(row.script ?? "").slice(0, 120)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {job && (
        <div className="audio-result">
          <h3>Queued: {job.filename}</h3>
          <pre>{JSON.stringify({ id: job.id, status: job.status, total: job.total }, null, 2)}</pre>
          <p className="muted">Open Processing to run a bounded cycle or inspect per-record status.</p>
        </div>
      )}
    </div>
  );
}

function LibraryView({ api, onError }: Props) {
  const [assets, setAssets] = useState<AudioAsset[]>([]);
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const page = (await api.listAudioAssets({ search, status })) as Envelope<AudioAsset[]>;
      setAssets(page.data);
      onError(null);
    } catch (err) {
      onError(describe(err));
    }
  }, [api, search, status, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  const download = async (asset: AudioAsset) => {
    setBusy(true);
    try {
      saveBlob(await api.downloadAudioAsset(asset.id), `${asset.title}.${asset.format}`);
      onError(null);
    } catch (err) {
      onError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (asset: AudioAsset) => {
    setBusy(true);
    try {
      await api.deleteAudioAsset(asset.id);
      await load();
      onError(null);
    } catch (err) {
      onError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="audio-view">
      <form
        className="audio-actions"
        onSubmit={(event) => { event.preventDefault(); void load(); }}
      >
        <input placeholder="Search title" value={search} onChange={(event) => setSearch(event.target.value)} />
        <select value={status} onChange={(event) => setStatus(event.target.value)}>
          <option value="">All statuses</option>
          <option value="ready">ready</option>
          <option value="processing">processing</option>
          <option value="failed">failed</option>
        </select>
        <button type="submit">Search</button>
      </form>
      <table>
        <thead>
          <tr><th>Title</th><th>Status</th><th>Voice</th><th>Format</th><th>Created</th><th>Actions</th></tr>
        </thead>
        <tbody>
          {assets.map((asset) => (
            <tr key={asset.id}>
              <td>{asset.title}</td>
              <td>{asset.status}</td>
              <td>{asset.voice}</td>
              <td>{asset.format}</td>
              <td>{asset.created_at}</td>
              <td>
                <button onClick={() => void download(asset)} disabled={busy || asset.status !== "ready"}>Download</button>
                <button onClick={() => void remove(asset)} disabled={busy}>Delete</button>
              </td>
            </tr>
          ))}
          {!assets.length && <tr><td colSpan={6}>No audio assets yet.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function ProcessingView({ api, onError }: Props) {
  const [jobs, setJobs] = useState<AudioBatchJob[]>([]);
  const [detail, setDetail] = useState<AudioBatchJob | null>(null);
  const [busy, setBusy] = useState(false);

  const loadList = useCallback(async () => {
    try {
      const page = (await api.listAudioBatches()) as Envelope<AudioBatchJob[]>;
      setJobs(page.data);
      onError(null);
    } catch (err) {
      onError(describe(err));
    }
  }, [api, onError]);

  useEffect(() => {
    void loadList();
  }, [loadList]);

  const open = async (id: string) => {
    setBusy(true);
    try {
      setDetail(await api.audioBatch(id));
      onError(null);
    } catch (err) {
      onError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    await loadList();
    if (detail) setDetail(await api.audioBatch(detail.id));
  };

  const process = async () => {
    setBusy(true);
    try {
      await api.processAudioBatchNow();
      await refresh();
      onError(null);
    } catch (err) {
      onError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  const retry = async () => {
    if (!detail) return;
    setBusy(true);
    try {
      setDetail(await api.retryAudioBatch(detail.id));
      await loadList();
      onError(null);
    } catch (err) {
      onError(describe(err));
    } finally {
      setBusy(false);
    }
  };

  const report = async () => {
    if (!detail) return;
    try {
      saveBlob(await api.downloadBatchReport(detail.id), `batch-${detail.id}.csv`);
      onError(null);
    } catch (err) {
      onError(describe(err));
    }
  };

  return (
    <div className="audio-view">
      <div className="audio-actions">
        <button onClick={() => void loadList()}>Refresh</button>
        <button onClick={() => void process()} disabled={busy}>Process one cycle</button>
        {detail && <button onClick={() => void retry()} disabled={busy}>Retry failed</button>}
        {detail && <button onClick={() => void report()}>Download report</button>}
      </div>
      <table>
        <thead><tr><th>Batch</th><th>Status</th><th>Ready</th><th>Failed</th><th>Rate limited</th></tr></thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.id}>
              <td><button onClick={() => void open(job.id)}>{job.filename}</button></td>
              <td>{job.status}</td>
              <td>{job.completed}/{job.total}</td>
              <td>{job.failed}</td>
              <td>{job.rate_limited}</td>
            </tr>
          ))}
          {!jobs.length && <tr><td colSpan={5}>No batches yet.</td></tr>}
        </tbody>
      </table>
      {detail && (
        <div className="audio-result">
          <h3>{detail.filename}</h3>
          <pre>{JSON.stringify({
            id: detail.id,
            status: detail.status,
            total: detail.total,
            completed: detail.completed,
            failed: detail.failed,
            error: detail.error,
          }, null, 2)}</pre>
          <table>
            <thead><tr><th>Row</th><th>Title</th><th>Status</th><th>Attempts</th><th>Error</th></tr></thead>
            <tbody>
              {(detail.items ?? []).map((item) => (
                <tr key={item.id}>
                  <td>{item.row_number}</td>
                  <td>{item.title}</td>
                  <td>{item.status}</td>
                  <td>{item.attempts}</td>
                  <td>{item.error ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SettingsView({ api, provider, onRefreshProvider, onError }: ProviderProps & { onRefreshProvider: () => void }) {
  const [settings, setSettings] = useState<Record<string, unknown> | null>(null);

  const load = useCallback(async () => {
    try {
      const info = await api.systemInfo();
      const raw = (info.settings as Record<string, unknown> | undefined) ?? {};
      setSettings(Object.fromEntries(Object.entries(raw).filter(([key]) => key.startsWith("audio_"))));
      onError(null);
    } catch (err) {
      onError(describe(err));
    }
  }, [api, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="audio-view">
      <div className="audio-actions">
        <button onClick={onRefreshProvider}>Refresh provider status</button>
      </div>
      <pre>{JSON.stringify(provider ?? { status: "loading" }, null, 2)}</pre>
      <h3>Batch settings</h3>
      <pre>{JSON.stringify(settings ?? {}, null, 2)}</pre>
      <p className="muted">
        CSV columns: <code>{CSV_COLUMNS}</code>. Rate-limit handling is client-side spacing with
        bounded backoff; it does not claim provider quota.
      </p>
    </div>
  );
}
