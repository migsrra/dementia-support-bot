export type UnsupportedQuery = {
  id: string;
  queryText: string;
  timestamp?: string;
};

type UnsupportedQueryApiItem = Record<string, unknown>;
type UnsupportedQueryListResponse =
  | UnsupportedQueryApiItem[]
  | {
      count?: number;
      items?: UnsupportedQueryApiItem[];
      nextToken?: string | null;
      pageSize?: number;
    }
  | null;

export type UnsupportedQueriesPage = {
  count: number;
  items: UnsupportedQuery[];
  nextToken?: string;
  pageSize: number;
};

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
const UNSUPPORTED_QUERIES_PAGE_SIZE = 25;

function assertConfigured(name: string, value: string) {
  if (!value) throw new Error(`${name} is not configured.`);
}

function joinUrl(base: string, path: string) {
  return `${base.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

function addQueryParams(
  url: string,
  params: Record<string, string | undefined>,
) {
  const query = Object.entries(params)
    .filter(([, value]) => typeof value === "string" && value.length > 0)
    .map(
      ([key, value]) =>
        `${encodeURIComponent(key)}=${encodeURIComponent(value as string)}`,
    )
    .join("&");

  if (!query) return url;
  return `${url}${url.includes("?") ? "&" : "?"}${query}`;
}

async function fetchOrThrow(input: RequestInfo | URL, init?: RequestInit) {
  const res = await fetch(input, init);
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(
      `Request failed (${res.status}): ${text || res.statusText}`,
    );
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

function normalizeUnsupportedQuery(
  item: UnsupportedQueryApiItem,
): UnsupportedQuery | null {
  const id = getStringField(item, ["id", "query_id", "queryID"]);
  const queryText = getStringField(item, [
    "queryText",
    "query_text",
    "query",
    "prompt",
    "text",
  ]);
  const timestamp = getStringField(item, [
    "timestamp",
    "createdAt",
    "created_at",
  ]);

  if (!id || !queryText) return null;

  return { id, queryText, timestamp };
}

export async function listUnsupportedQueries(options?: {
  limit?: number;
  nextToken?: string;
}): Promise<UnsupportedQueriesPage> {
  assertConfigured(
    "VITE_UNSUPPORTED_QUERIES_API_BASE_URL",
    UNSUPPORTED_QUERIES_API_BASE_URL,
  );

  const requestedLimit =
    typeof options?.limit === "number" &&
    Number.isFinite(options.limit) &&
    options.limit > 0
      ? Math.floor(options.limit)
      : UNSUPPORTED_QUERIES_PAGE_SIZE;

  const url = joinUrl(
    UNSUPPORTED_QUERIES_API_BASE_URL,
    UNSUPPORTED_QUERIES_LIST_PATH,
  );
  const pagedUrl = addQueryParams(url, {
    limit: String(requestedLimit),
    nextToken: options?.nextToken,
  });

  const res = await fetchOrThrow(pagedUrl, { method: "GET" });
  const payload = (await res
    .json()
    .catch(() => null)) as UnsupportedQueryListResponse;

  const pageItems = Array.isArray(payload)
    ? payload
    : Array.isArray(payload?.items)
      ? payload.items
      : [];

  const items = pageItems
    .map((item) => normalizeUnsupportedQuery(item))
    .filter((item): item is UnsupportedQuery => item !== null);

  const nextToken =
    !Array.isArray(payload) &&
    typeof payload?.nextToken === "string" &&
    payload.nextToken.trim()
      ? payload.nextToken
      : undefined;

  const count =
    !Array.isArray(payload) &&
    typeof payload?.count === "number" &&
    Number.isFinite(payload.count)
      ? payload.count
      : items.length;

  const pageSize =
    !Array.isArray(payload) &&
    typeof payload?.pageSize === "number" &&
    Number.isFinite(payload.pageSize)
      ? payload.pageSize
      : requestedLimit;

  return {
    count,
    items,
    nextToken,
    pageSize,
  };
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

  const deletePath = UNSUPPORTED_QUERIES_DELETE_PATH.replace(
    "{id}",
    encodeURIComponent(id),
  ).replace("{timestamp}", encodeURIComponent(options.timestamp));
  const url = joinUrl(UNSUPPORTED_QUERIES_DELETE_API_BASE_URL, deletePath);

  await fetchOrThrow(url, { method: "DELETE" });
}

void UNSUPPORTED_QUERIES_API_BASE_URL;
void UNSUPPORTED_QUERIES_DELETE_API_BASE_URL;
void UNSUPPORTED_QUERIES_LIST_PATH;
void UNSUPPORTED_QUERIES_DELETE_PATH;
