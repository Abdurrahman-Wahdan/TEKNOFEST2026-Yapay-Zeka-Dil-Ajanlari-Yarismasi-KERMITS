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
export type SearchResponse = Schemas["SearchResponse"];
export type Profile = Schemas["ProfileOut"];
export type SavedView = Schemas["SavedViewOut"];
export type ChatSession = Schemas["ChatSessionOut"];
export type ChatSessionDetail = Schemas["ChatSessionDetail"];
export type ChatMessage = Schemas["ChatMessageOut"];
export type StreamEvent = Schemas["StreamEvent"];
export type TableMetadata = Schemas["TableMetadataOut"];
export type ContextLevel = Schemas["ContextLevelOut"];
export type CompactionResult = Schemas["CompactionResult"];
export type ChatModel = Schemas["ModelOut"];
export type ChatModels = Schemas["ModelsResponse"];
export type TokenPair = Schemas["TokenPair"];
export type User = Schemas["UserOut"];
export type ResetPasswordResponse = Schemas["ResetPasswordResponse"];

/**
 * Relative, so requests go through the Next rewrite to FastAPI and the browser
 * sees one origin. No CORS preflight on every call, and no API host baked into
 * the client bundle.
 */
const BASE = "/api";

/** An error carrying the status, so callers can branch without parsing strings. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
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

/** Set by the auth provider on login and cleared on logout. */
export function setAccessToken(token: string | null) {
  accessToken = token;
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

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  const response = await fetch(`${BASE}${path}`, { ...init, headers });
  if (!response.ok) throw await toError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  // ----- auth -----
  signup: (body: Schemas["SignupRequest"]) =>
    request<TokenPair>("/auth/signup", { method: "POST", body: JSON.stringify(body) }),
  login: (body: Schemas["LoginRequest"]) =>
    request<TokenPair>("/auth/login", { method: "POST", body: JSON.stringify(body) }),
  refresh: (refresh_token: string) =>
    request<TokenPair>("/auth/refresh", {
      method: "POST",
      body: JSON.stringify({ refresh_token }),
    }),
  me: () => request<User>("/auth/me"),
  resetPassword: (body: Schemas["ResetPasswordRequest"]) =>
    request<ResetPasswordResponse>("/auth/reset-password", {
      method: "POST",
      body: JSON.stringify(body),
    }),

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

  // ----- comparison-table pool (dataprep.compare, offline) -----
  /** Every table in one category ("ürün" | "kampanya"), for the browse picker. */
  compareTablesList: (category: "ürün" | "kampanya") =>
    request<TableListOut>(`/compare-tables${queryString({ category })}`),
  /** One table, shaped for `<TableWidget />`. */
  compareTable: (id: string) => request<TableDetailOut>(`/compare-tables/${id}`),

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
  views: () => request<SavedView[]>("/me/views"),
  saveView: (body: Schemas["SavedViewIn"]) =>
    request<SavedView>(`/me/views/${body.slug}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  deleteView: (slug: string) =>
    request<void>(`/me/views/${slug}`, { method: "DELETE" }),

  // ----- chat -----
  chatSessions: () => request<ChatSession[]>("/chat/sessions"),
  chatSession: (id: string) => request<ChatSessionDetail>(`/chat/sessions/${id}`),
  deleteChatSession: (id: string) =>
    request<void>(`/chat/sessions/${id}`, { method: "DELETE" }),
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
  tableMetadata: (body: Schemas["TableMetadataRequest"]) =>
    request<TableMetadata>("/chat/table-metadata", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};

/**
 * Ask the agent, yielding events as they stream in.
 *
 * Hand-rolled over fetch rather than `EventSource`: EventSource cannot send a
 * POST body or an Authorization header, so the question would have to go in the
 * URL and the token with it — a user's question in a query string ends up in
 * every access log it passes through.
 */
export async function* askStream(
  body: {
    question: string;
    session_id?: string;
    context?: Schemas["AttachedContext"][];
    captures?: Schemas["CapturePayload"][];
    toolResults?: Schemas["ToolResult"][];
    think?: boolean;
    model?: string | null;
  },
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const headers = new Headers({ "Content-Type": "application/json" });
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  const response = await fetch(`${BASE}/chat/ask`, {
    method: "POST",
    headers,
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
