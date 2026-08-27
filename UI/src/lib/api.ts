import type { components } from "@/types/api";

/**
 * The one place the app talks to FastAPI.
 *
 * Every type below is generated from the API's OpenAPI schema by
 * `npm run api:types` — none of it is hand-written. A field renamed in Python
 * becomes a TypeScript error here rather than an `undefined` a user finds.
 */
type Schemas = components["schemas"];

export type Bank = Schemas["BankOut"];
export type Family = Schemas["FamilyOut"];
export type Product = Schemas["ProductOut"];
export type FinanceQuote = Schemas["FinanceQuoteOut"];
export type ProfitShareQuote = Schemas["ProfitShareQuoteOut"];
export type Conversion = Schemas["ConversionOut"];
export type Rate = Schemas["RateOut"];
export type CardInstallmentQuote = Schemas["CardInstallmentQuoteOut"];
export type MileRate = Schemas["MileRateOut"];
export type Comparison = Schemas["ComparisonOut"];
export type Unavailable = Schemas["UnavailableOut"];
export type Chunk = Schemas["ChunkOut"];
export type Constraints = Schemas["ConstraintsOut"];
export type BankLimits = Schemas["BankLimitsOut"];
export type ProducedComponents = Schemas["ComponentsResponse"];
export type ComponentCategory = Schemas["CategoryOut"];
export type TableSummary = Schemas["TableSummaryOut"];
export type TableListOut = Schemas["TableListOut"];
export type TableDetailOut = Schemas["TableDetailOut"];
export type TableOverviewOut = Schemas["TableOverviewOut"];
export type TableOverviewState = Schemas["TableOverviewState"];
export type LiveOverviewState = Schemas["LiveOverviewState"];
export type LiveOverviewRequest = Schemas["LiveOverviewRequest"];
export type TableOverviewRequest = Schemas["TableOverviewRequest"];
export type TableOverviewStarted = Schemas["TableOverviewStarted"];
export type SearchResponse = Schemas["SearchResponse"];
export type Profile = Schemas["ProfileOut"];
export type SavedView = Schemas["SavedViewOut"];
export type ChatSession = Schemas["ChatSessionOut"];
export type ChatSessionDetail = Schemas["ChatSessionDetail"];
export type ChatMessage = Schemas["ChatMessageOut"];
export type MessageFeedback = Schemas["MessageFeedbackOut"];
export type StreamEvent = Schemas["StreamEvent"];
export type TableMetadata = Schemas["TableMetadataOut"];
export type ContextLevel = Schemas["ContextLevelOut"];
export type CompactionResult = Schemas["CompactionResult"];
export type Recommendation = Schemas["RecommendationOut"];
export type ChatModel = Schemas["ModelOut"];
export type ChatModels = Schemas["ModelsResponse"];
export type TokenPair = Schemas["TokenPair"];
export type User = Schemas["UserOut"];
export type ResetPasswordResponse = Schemas["ResetPasswordResponse"];
export type VoiceTranscription = Schemas["VoiceTranscriptionOut"];
export type UserStats = Schemas["StatsOut"];
export type Automation = Schemas["AutomationOut"];
export type AutomationReport = Schemas["ReportOut"];
export type AutomationReportSummary = Schemas["ReportSummary"];
export type PreparedAttachment = Schemas["PreparedAttachmentOut"];
export type NotificationSettings = Schemas["NotificationSettingsOut"];

/**
 * Relative, so requests go through the Next rewrite to FastAPI and the browser
 * sees one origin. No CORS preflight on every call, and no API host baked into
 * the client bundle.
 */
const BASE = "/api";

/** An error carrying the status, so callers can branch without parsing strings. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }

  /** The token is missing or expired; the caller should re-authenticate. */
  get isUnauthenticated() {
    return this.status === 401;
  }

  /**
   * The request was understood and refused — a bank that does not sell this
   * product, a family key that does not exist. Not a bug: something to show
   * the user as an answer.
   */
  get isRefusal() {
    return this.status === 422;
  }

  /** A dependency is down. Worth offering a retry. */
  get isUnavailable() {
    return this.status === 503;
  }
}

let accessToken: string | null = null;
let accessTokenExpiresAt = 0;

type StoredRefreshToken = { token: string; remember: boolean };
type AuthSessionHooks = {
  getRefreshToken: () => StoredRefreshToken | null;
  applyTokens: (tokens: TokenPair, remember: boolean) => void;
  onSessionExpired: () => void;
};

let authSessionHooks: AuthSessionHooks | null = null;
let refreshInFlight: Promise<string | null> | null = null;
let sessionExpiryNotified = false;

/**
 * Connect the framework-neutral transport to React's session state.
 *
 * Keeping this as callbacks avoids an api -> AuthProvider -> api import cycle,
 * while still giving every REST/stream consumer one refresh and expiry path.
 */
export function setAuthSessionHooks(hooks: AuthSessionHooks | null) {
  authSessionHooks = hooks;
}

/** Set by the auth provider on login and cleared on logout. */
export function setAccessToken(token: string | null, expiresInSeconds?: number) {
  accessToken = token;
  if (token) sessionExpiryNotified = false;
  accessTokenExpiresAt =
    token && expiresInSeconds && expiresInSeconds > 0
      ? Date.now() + expiresInSeconds * 1000
      : 0;
}

function notifySessionExpired() {
  if (!authSessionHooks || sessionExpiryNotified) return;
  sessionExpiryNotified = true;
  authSessionHooks.onSessionExpired();
}

export function getAccessToken() {
  return accessToken;
}

async function toError(response: Response): Promise<ApiError> {
  // FastAPI puts the message in `detail`, which is a string for our raised
  // HTTPExceptions and an array for its own validation failures.
  let detail = response.statusText;
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") {
      detail = body.detail;
    } else if (Array.isArray(body?.detail)) {
      detail = body.detail
        .map((d: { msg?: string }) => d.msg ?? "")
        .filter(Boolean)
        .join("; ");
    }
  } catch {
    // A non-JSON body (a proxy error page) leaves statusText as the message.
  }
  return new ApiError(response.status, detail);
}

function validTokenPair(value: unknown): value is TokenPair {
  if (!value || typeof value !== "object") return false;
  const pair = value as Partial<TokenPair>;
  return (
    typeof pair.access_token === "string" &&
    pair.access_token.length > 0 &&
    typeof pair.refresh_token === "string" &&
    pair.refresh_token.length > 0 &&
    typeof pair.expires_in === "number" &&
    pair.expires_in > 0
  );
}

/**
 * Rotate the session once, shared by every caller that notices expiry.
 *
 * A burst of queries after a sleeping laptop therefore sends one refresh, not
 * one per widget. Only an invalid/expired refresh token ends the session;
 * network and server failures remain retryable and never log a user out.
 */
export function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;

  const refresh = async () => {
    const stored = authSessionHooks?.getRefreshToken();
    if (!stored) {
      notifySessionExpired();
      return null;
    }

    const response = await fetch(`${BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: stored.token }),
    });

    if (!response.ok) {
      const error = await toError(response);
      if (error.isUnauthenticated) {
        notifySessionExpired();
        return null;
      }
      throw error;
    }

    const tokens: unknown = await response.json();
    if (!validTokenPair(tokens)) {
      throw new ApiError(502, "The refresh response was malformed.");
    }
    authSessionHooks?.applyTokens(tokens, stored.remember);
    return tokens.access_token;
  };

  refreshInFlight = refresh().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

/** Return a token with enough remaining life for a new request or socket. */
export async function ensureFreshAccessToken(
  minimumValidityMs = 60_000,
): Promise<string | null> {
  if (!accessToken) return null;
  if (
    accessTokenExpiresAt === 0 ||
    accessTokenExpiresAt - Date.now() > minimumValidityMs
  ) {
    return accessToken;
  }
  return refreshAccessToken();
}

type Query = Record<string, string | number | boolean | string[] | null | undefined>;

/** Query string builder that repeats a key per array item, as FastAPI expects. */
export function queryString(params: Query): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value)) {
      value.forEach((v) => search.append(key, v));
    } else {
      search.set(key, String(value));
    }
  }
  const qs = search.toString();
  return qs ? `?${qs}` : "";
}

type AuthRequestOptions = {
  includeAccessToken?: boolean;
  recoverAuthentication?: boolean;
};

async function fetchWithAuthentication(
  path: string,
  init: RequestInit = {},
  options: AuthRequestOptions = {},
): Promise<Response> {
  const includeAccessToken = options.includeAccessToken ?? true;
  const recoverAuthentication = options.recoverAuthentication ?? true;

  const send = () => {
    const headers = new Headers(init.headers);
    if (
      init.body &&
      !(init.body instanceof FormData) &&
      !headers.has("Content-Type")
    ) {
      headers.set("Content-Type", "application/json");
    }
    if (includeAccessToken && accessToken) {
      headers.set("Authorization", `Bearer ${accessToken}`);
    }
    return fetch(`${BASE}${path}`, { ...init, headers });
  };

  let response = await send();
  if (
    response.status !== 401 ||
    !includeAccessToken ||
    !recoverAuthentication ||
    !accessToken
  ) {
    return response;
  }

  const refreshed = await refreshAccessToken();
  if (!refreshed) return response;

  // We will not read the first 401 body. Cancel it before replaying so its
  // connection can be released immediately rather than waiting for GC.
  await response.body?.cancel().catch(() => undefined);
  response = await send();
  if (response.status === 401) notifySessionExpired();
  return response;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  options: AuthRequestOptions = {},
): Promise<T> {
  const response = await fetchWithAuthentication(path, init, options);
  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  // ----- auth -----
  signup: (body: Schemas["SignupRequest"]) =>
    request<TokenPair>(
      "/auth/signup",
      { method: "POST", body: JSON.stringify(body) },
      { includeAccessToken: false, recoverAuthentication: false },
    ),
  login: (body: Schemas["LoginRequest"]) =>
    request<TokenPair>(
      "/auth/login",
      { method: "POST", body: JSON.stringify(body) },
      { includeAccessToken: false, recoverAuthentication: false },
    ),
  refresh: (refresh_token: string) =>
    request<TokenPair>(
      "/auth/refresh",
      { method: "POST", body: JSON.stringify({ refresh_token }) },
      { includeAccessToken: false, recoverAuthentication: false },
    ),
  me: () => request<User>("/auth/me"),
  resetPassword: (body: Schemas["ResetPasswordRequest"]) =>
    request<ResetPasswordResponse>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify(body),
    }, { includeAccessToken: false, recoverAuthentication: false }),

  // ----- banks -----
  banks: () => request<Bank[]>("/banks"),
  families: () => request<Family[]>("/banks/families"),
  bank: (bank: string) => request<Bank>(`/banks/${bank}`),
  bankProducts: (bank: string, category = "finance") =>
    request<Product[]>(`/banks/${bank}/products${queryString({ category })}`),
  bankRates: (bank: string) => request<Rate[]>(`/banks/${bank}/rates`),
  financeQuote: (bank: string, params: { product: string; amount: number; term: number; monthly_profit_rate?: number }) =>
    request<FinanceQuote>(`/banks/${bank}/finance${queryString(params)}`),
  cardQuote: (
    bank: string,
    params: { card: string; amount: number; installments: number },
  ) => request<CardInstallmentQuote>(`/banks/${bank}/card${queryString(params)}`),
  mileRates: (bank: string) => request<MileRate[]>(`/banks/${bank}/miles`),

  // ----- comparison -----
  compareFinance: (params: {
    family: string;
    amount: number;
    term: number;
    monthly_profit_rate?: number;
    banks?: string[];
  }) => request<Comparison>(`/compare/finance${queryString(params)}`),
  compareProfitShare: (params: {
    family?: string;
    amount: number;
    term: number;
    unit?: string;
    currency?: string;
    banks?: string[];
  }) => request<Comparison>(`/compare/profit-share${queryString(params)}`),
  compareExchange: (params: {
    source: string;
    target: string;
    amount: number;
    banks?: string[];
  }) => request<Comparison>(`/compare/exchange${queryString(params)}`),
  compareCard: (params: {
    amount: number;
    installments: number;
    banks?: string[];
  }) => request<Comparison>(`/compare/card${queryString(params)}`),

  // ----- corpus -----
  search: (params: {
    q: string;
    bank?: string;
    doc_kind?: string;
    source_type?: string;
    active_only?: boolean;
    k?: number;
  }) => request<SearchResponse>(`/search${queryString(params)}`),

  /**
   * What the selected banks will accept, before anyone is asked.
   *
   * Read from cached catalogues, so a form can call this on every change
   * without touching a bank endpoint.
   */
  constraints: (params: {
    family: string;
    category?: "finance" | "profit_share";
    banks?: string[];
  }) => request<Constraints>(`/compare/constraints${queryString(params)}`),

  /**
   * Read whatever `/compare` is showing and say what it shows.
   *
   * One call where the pool needs two. The pool's card knows its key -- the
   * table id is in the URL it navigated to -- so it can GET to check before it
   * POSTs to start. A live board has no id, only its content, and having the
   * browser hash the outline to match Python's SHA-256 forever is a thing that
   * fails silently and looks like an overview that never arrives. So this posts
   * the page and gets back either the finished overview or the digest to poll
   * with.
   *
   * Safe to repeat: the digest is the content, so the same board answers from
   * cache and costs nothing.
   */
  startLiveOverview: (body: LiveOverviewRequest) =>
    request<LiveOverviewState>("/compare/overview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Poll for one that is being written. Never generates; see the POST. */
  liveOverview: (digest: string, locale: string) =>
    request<LiveOverviewState>(`/compare/overview${queryString({ digest, locale })}`),

  // ----- comparison-table pool (dataprep.compare, offline) -----
  /** Every table in one category ("ürün" | "kampanya"), for the browse picker. */
  compareTablesList: (category: "ürün" | "kampanya") =>
    request<TableListOut>(`/compare-tables${queryString({ category })}`),
  /** One table, shaped for `<TableWidget />`. */
  compareTable: (id: string) => request<TableDetailOut>(`/compare-tables/${id}`),
  /** Whether this table has an overview, is having one written, or has
      neither. Never generates one itself: a GET that costs a vision-model call
      is not safe to retry. */
  tableOverview: (id: string, locale: string) =>
    request<TableOverviewState>(`/compare-tables/${id}/overview${queryString({ locale })}`),
  /** Start writing the overview, handing the agent the page the browser is
      showing. Returns as soon as the work is queued — a generation outlives
      what any proxy will hold a socket open for, so the result is collected by
      polling `tableOverview` rather than waiting on this. */
  startTableOverview: (id: string, body: TableOverviewRequest) =>
    request<TableOverviewStarted>(`/compare-tables/${id}/overview`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // ----- produced components -----
  componentCategories: () => request<ComponentCategory[]>("/components"),
  /**
   * A topic page's RAG content. Served from fixtures until the producer lands;
   * `source` on the response says which, and the UI badges it.
   */
  categoryComponents: (category: string) =>
    request<ProducedComponents>(`/components/${category}`),

  // ----- profile -----
  profile: () => request<Profile>("/me/profile"),
  saveProfile: (body: Schemas["ProfileIn"]) =>
    request<Profile>("/me/profile", { method: "PUT", body: JSON.stringify(body) }),
  notificationSettings: () => request<NotificationSettings>("/me/settings/notifications"),
  saveNotificationSettings: (body: Schemas["NotificationSettingsIn"]) =>
    request<NotificationSettings>("/me/settings/notifications", {
      method: "PUT", body: JSON.stringify(body),
    }),
  views: () => request<SavedView[]>("/me/views"),
  saveView: (body: Schemas["SavedViewIn"]) =>
    request<SavedView>(`/me/views/${body.slug}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteView: (slug: string) =>
    request<void>(`/me/views/${slug}`, { method: "DELETE" }),
  /** Counts for the profile overview. No tokens — nothing records them. */
  stats: () => request<UserStats>("/me/stats"),

  // ----- automations -----
  automations: () => request<Automation[]>("/me/automations"),
  createAutomation: (body: Schemas["AutomationIn"]) =>
    request<Automation>("/me/automations", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /**
   * Create one from a sentence. Any field set by hand overrides what the agent
   * read out of the text — the user moved the picker after writing the sentence,
   * so their reading of "akşam" outranks the model's.
   */
  describeAutomation: (body: Schemas["AutomationDescribeIn"]) =>
    request<Automation>("/me/automations/describe", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateAutomation: (id: string, body: Schemas["AutomationPatch"]) =>
    request<Automation>(`/me/automations/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),
  deleteAutomation: (id: string) =>
    request<void>(`/me/automations/${id}`, { method: "DELETE" }),
  /**
   * Run one now, out of band. Returns as soon as the run has started — a report
   * is minutes of ten bank specialists, and the notification bell is how the
   * user learns it finished, exactly as for a scheduled run.
   */
  runAutomation: (id: string) =>
    request<{ started: boolean; automation_id: string }>(
      `/me/automations/${id}/run`,
      { method: "POST" },
    ),
  /** Summaries — no bodies. See `ReportSummary` on the Python side. */
  automationReports: (unreadOnly = false) =>
    request<AutomationReportSummary[]>(
      `/me/automations/reports${unreadOnly ? "?unread_only=true" : ""}`,
    ),
  /** The notification badge. One indexed count, polled on a timer. */
  unreadReportCount: () =>
    request<{ unread: number }>("/me/automations/reports/unread-count"),
  automationReport: (id: string) =>
    request<AutomationReport>(`/me/automations/reports/${id}`),
  /**
   * Marking read is what clears the bell, and it is deliberately separate from
   * fetching: a retry or a cache revalidation must not silently clear a
   * notification the user never saw.
   */
  markReportRead: (id: string) =>
    request<AutomationReport>(`/me/automations/reports/${id}/read`, {
      method: "POST",
    }),

  // ----- export -----

  /**
   * A table or a report as a file.
   *
   * The only call in this module that does not go through `request`: that helper
   * ends in `response.json()`, and this response is a spreadsheet. The filename
   * comes back in `Content-Disposition` rather than being built here, so the
   * name in the Downloads folder is the one the server put inside the file's own
   * metadata — see `api/export/filename.py`.
   */
  exportFile: async (
    body: Schemas["ExportRequest"],
  ): Promise<{ blob: Blob; disposition: string | null }> => {
    const response = await fetchWithAuthentication("/export", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (!response.ok) throw await toError(response);
    return {
      blob: await response.blob(),
      disposition: response.headers.get("Content-Disposition"),
    };
  },

  // ----- chat -----
  chatSessions: () => request<ChatSession[]>("/chat/sessions"),
  chatSession: (id: string) => request<ChatSessionDetail>(`/chat/sessions/${id}`),
  deleteChatSession: (id: string) =>
    request<void>(`/chat/sessions/${id}`, { method: "DELETE" }),
  saveMessageFeedback: (
    sessionId: string,
    messageId: string,
    body: Schemas["MessageFeedbackRequest"],
  ) => request<MessageFeedback>(
    `/chat/sessions/${sessionId}/messages/${messageId}/feedback`,
    { method: "PUT", body: JSON.stringify(body) },
  ),
  // The composer's model picker. No arguments: the caller has nothing to filter
  // this by, and the list is short enough that paging it would be theatre.
  models: () => request<ChatModels>("/models"),
  // How full the conversation's thread is. Only the supervisor's -- the bank
  // specialists have their own, compacted the same way, but they are working
  // memory rather than the conversation.
  contextLevel: (sessionId: string) =>
    request<ContextLevel>(`/chat/sessions/${sessionId}/context`),
  compactSession: (sessionId: string) =>
    request<CompactionResult>(`/chat/sessions/${sessionId}/compact`, {
      method: "POST",
    }),
  conversationRecommendation: (sessionId: string, locale: "en" | "tr", signal?: AbortSignal) =>
    request<Recommendation>(`/chat/sessions/${sessionId}/recommendation`, {
      method: "POST",
      body: JSON.stringify({ locale }),
      signal,
    }),
  tableMetadata: (body: Schemas["TableMetadataRequest"]) =>
    request<TableMetadata>("/chat/table-metadata", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  // ----- local speech-to-text -----
  voiceTranscription: (audio: Blob, signal?: AbortSignal) => {
    const body = new FormData();
    const extension = audio.type.includes("mp4")
      ? "m4a"
      : audio.type.includes("ogg")
        ? "ogg"
        : "webm";
    body.append("file", audio, `voice.${extension}`);
    return request<VoiceTranscription>("/voice/transcriptions", {
      method: "POST",
      body,
      signal,
    });
  },

  // ----- chat attachments -----
  prepareChatAttachment: (file: File, signal?: AbortSignal) => {
    const body = new FormData();
    body.append("file", file, file.name);
    return request<PreparedAttachment>("/chat/attachments", {
      method: "POST",
      body,
      signal,
    });
  },
};

/**
 * Ask the agent, yielding events as they stream in.
 *
 * Hand-rolled over fetch rather than `EventSource`: EventSource cannot send a
 * POST body or an Authorization header, so the question would have to go in the
 * URL and the token with it — a user's question in a query string ends up in
 * every access log it passes through.
 */
/**
 * Read a passage aloud. Resolves once the audio has started arriving.
 *
 * The response body is raw 16-bit PCM at the rate named in `X-Sample-Rate`,
 * still being generated — the caller reads it with a stream reader and schedules
 * the samples as they land, which is what makes the answer start playing in
 * ~0.13s instead of after the whole reading has been produced.
 *
 * The rate is read off the response rather than assumed. An `AudioContext` built
 * at the wrong rate does not fail; it plays the answer at the wrong pitch, which
 * is the kind of bug that survives a review because it still makes a sound.
 */
export async function speakText(
  text: string,
  signal?: AbortSignal,
): Promise<{ body: ReadableStream<Uint8Array>; sampleRate: number }> {
  const response = await fetchWithAuthentication("/voice/speech", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
    signal,
  });
  if (!response.ok) throw await toError(response);
  if (!response.body) throw new ApiError(500, "The reading carried no audio.");
  const declared = Number(response.headers.get("X-Sample-Rate"));
  return {
    body: response.body,
    sampleRate: Number.isFinite(declared) && declared > 0 ? declared : 48_000,
  };
}

export async function* askStream(
  body: {
    question: string;
    session_id?: string;
    /** Replace the last exchange instead of appending one. See `AskRequest`. */
    regenerate?: boolean;
    context?: Schemas["AttachedContext"][];
    captures?: Schemas["CapturePayload"][];
    attachments?: Schemas["PreparedAttachmentRef"][];
    toolResults?: Schemas["ToolResult"][];
    think?: boolean;
    webSearch?: boolean;
    model?: string | null;
  },
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const response = await fetchWithAuthentication("/chat/ask", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) throw await toError(response);
  if (!response.body) throw new ApiError(500, "The response carried no body.");

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += value;

    // SSE frames are separated by a blank line. A chunk can split one mid-way,
    // so the tail stays in the buffer until its terminator arrives — parsing
    // per chunk instead would drop or corrupt the frame at every boundary.
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";

    for (const frame of frames) {
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (!line) continue;
      try {
        yield JSON.parse(line.slice(6)) as StreamEvent;
      } catch {
        // A malformed frame is dropped rather than killing the stream: losing
        // one token beats losing the rest of the answer.
      }
    }
  }
}
