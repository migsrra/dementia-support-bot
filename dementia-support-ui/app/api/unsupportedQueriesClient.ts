export type UnsupportedQuery = {
  id: string;
  queryText: string;
  timestamp?: string;
};

type UnsupportedQueryApiItem = Record<string, unknown>;

const UNSUPPORTED_QUERIES_API_BASE_URL =
  import.meta.env.VITE_UNSUPPORTED_QUERIES_API_BASE_URL ?? "";
const UNSUPPORTED_QUERIES_DELETE_API_BASE_URL =
  import.meta.env.VITE_UNSUPPORTED_QUERIES_DELETE_API_BASE_URL ??
  UNSUPPORTED_QUERIES_API_BASE_URL;
const UNSUPPORTED_QUERIES_LIST_PATH =
  import.meta.env.VITE_UNSUPPORTED_QUERIES_LIST_PATH ?? "/unsupported-queries";
const UNSUPPORTED_QUERIES_DELETE_PATH =
  import.meta.env.VITE_UNSUPPORTED_QUERIES_DELETE_PATH ??
  "/queries/{id}/timestamps/{timestamp}";

function assertConfigured(name: string, value: string) {
  if (!value) throw new Error(`${name} is not configured.`);
}

function joinUrl(base: string, path: string) {
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

async function fetchOrThrow(input: RequestInfo | URL, init?: RequestInit) {
  const res = await fetch(input, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Request failed (${res.status}): ${text || res.statusText}`);
  }
  return res;
}

function getStringField(item: UnsupportedQueryApiItem, keys: string[]) {
  for (const key of keys) {
    const value = item[key];
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return undefined;
}

function normalizeUnsupportedQuery(item: UnsupportedQueryApiItem): UnsupportedQuery | null {
  const id = getStringField(item, ["id", "query_id", "queryID"]);
  const queryText = getStringField(item, ["queryText", "query_text", "query", "prompt", "text"]);
  const timestamp = getStringField(item, ["timestamp", "createdAt", "created_at"]);

  if (!id || !queryText) return null;

  return { id, queryText, timestamp };
}

export async function listUnsupportedQueries(): Promise<UnsupportedQuery[]> {
  assertConfigured(
    "VITE_UNSUPPORTED_QUERIES_API_BASE_URL",
    UNSUPPORTED_QUERIES_API_BASE_URL,
  );

  const url = joinUrl(UNSUPPORTED_QUERIES_API_BASE_URL, UNSUPPORTED_QUERIES_LIST_PATH);
  const res = await fetchOrThrow(url, { method: "GET" });
  const payload = (await res.json().catch(() => null)) as
    | UnsupportedQueryApiItem[]
    | { items?: UnsupportedQueryApiItem[] }
    | null;

  const items = Array.isArray(payload) ? payload : Array.isArray(payload?.items) ? payload.items : [];

  return items
    .map((item) => normalizeUnsupportedQuery(item))
    .filter((item): item is UnsupportedQuery => item !== null);
}

export async function deleteUnsupportedQuery(
  id: string,
  options?: { timestamp?: string },
): Promise<void> {
  assertConfigured(
    "VITE_UNSUPPORTED_QUERIES_DELETE_API_BASE_URL",
    UNSUPPORTED_QUERIES_DELETE_API_BASE_URL,
  );
  if (!options?.timestamp) {
    throw new Error("Unsupported query timestamp is required for deletion.");
  }

  const deletePath = UNSUPPORTED_QUERIES_DELETE_PATH
    .replace("{id}", encodeURIComponent(id))
    .replace("{timestamp}", encodeURIComponent(options.timestamp));
  const url = joinUrl(UNSUPPORTED_QUERIES_DELETE_API_BASE_URL, deletePath);

  await fetchOrThrow(url, { method: "DELETE" });
}

void UNSUPPORTED_QUERIES_API_BASE_URL;
void UNSUPPORTED_QUERIES_DELETE_API_BASE_URL;
void UNSUPPORTED_QUERIES_LIST_PATH;
void UNSUPPORTED_QUERIES_DELETE_PATH;
