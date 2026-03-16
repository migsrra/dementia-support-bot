export type DocumentItem = {
  key: string;
  sizeBytes?: number;
  lastModified?: string;
};

export type UploadAcceptedResponse = {
  status: "accepted";
  message?: string;
  kbKey?: string;
  screeningSummary?: UploadScreeningSummary;
};

export type UploadRejectedReason =
  | "unable_to_extract_text"
  | "possible_phi_detected"
  | "possible_phi_detected_and_not_relevant"
  | "not_relevant"
  | string;

export type UploadPhiDisplayItem = {
  text?: string;
  score?: number;
};

export type UploadPhiGroup = {
  key: string;
  label: string;
  items: UploadPhiDisplayItem[];
};

export type UploadScreeningSummary = {
  phiDetected: boolean;
  isRelevant: boolean;
  relevanceReason?: string;
};

export type UploadRejectedResponse = {
  status: "rejected";
  reason: UploadRejectedReason;
  uploadId?: string;
  quarantineKey?: string;
  phiGroups?: UploadPhiGroup[];
  screeningSummary?: UploadScreeningSummary;
};

export type UploadDocumentResponse =
  | UploadAcceptedResponse
  | UploadRejectedResponse;

const MANAGE_KBDOCS_API_BASE_URL =
  import.meta.env.VITE_MANAGE_KBDOCS_API_BASE_URL ?? "";
const MANAGE_KBDOCS_LIST_PATH =
  import.meta.env.VITE_MANAGE_KBDOCS_LIST_PATH ?? "/documents";
const MANAGE_KBDOCS_DELETE_PATH =
  import.meta.env.VITE_MANAGE_KBDOCS_DELETE_PATH ?? "/documents";

const UPLOAD_API_BASE_URL =
  import.meta.env.VITE_UPLOAD_API_BASE_URL ?? MANAGE_KBDOCS_API_BASE_URL;
const DOCS_API_UPLOAD_PATH =
  import.meta.env.VITE_DOCUMENTS_UPLOAD_PATH ?? "/documents/upload";

const DOCS_API_UPLOAD_OVERRIDE_URL =
  import.meta.env.VITE_DOCUMENTS_UPLOAD_OVERRIDE ?? "";
const DOCS_API_CANCEL_UPLOAD_PATH =
  import.meta.env.VITE_DOCUMENTS_CANCEL_UPLOAD_PATH ??
  "/documents/cancel-upload";
const KB_SYNC_API_URL = import.meta.env.VITE_KB_SYNC_API_URL ?? "";
const PRESIGNED_BASE_URL = import.meta.env.VITE_PRESIGNED_BASE_URL ?? "";

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
    throw new Error(
      `Request failed (${res.status}): ${text || res.statusText}`,
    );
  }
  return res;
}

export async function listDocuments(): Promise<DocumentItem[]> {
  assertConfigured(
    "VITE_MANAGE_KBDOCS_API_BASE_URL",
    MANAGE_KBDOCS_API_BASE_URL,
  );

  const url = joinUrl(MANAGE_KBDOCS_API_BASE_URL, MANAGE_KBDOCS_LIST_PATH);
  const res = await fetchOrThrow(url, { method: "GET" });

  const payload = (await res.json().catch(() => null)) as
    | { items?: DocumentItem[] }
    | null;
  const items = payload?.items;

  if (!Array.isArray(items)) {
    throw new Error("List documents API returned an unexpected response.");
  }

  return items.filter((document) => Boolean(document?.key));
}

export async function deleteDocument(key: string): Promise<void> {
  assertConfigured(
    "VITE_MANAGE_KBDOCS_API_BASE_URL",
    MANAGE_KBDOCS_API_BASE_URL,
  );

  // IMPORTANT: encode key so spaces/#/? don't break the URL
  const url = joinUrl(
    MANAGE_KBDOCS_API_BASE_URL,
    `${MANAGE_KBDOCS_DELETE_PATH}/${encodeURIComponent(key)}`,
  );
  await fetchOrThrow(url, { method: "DELETE" });
  await triggerKbSync();
}

async function triggerKbSync() {
  assertConfigured("VITE_KB_SYNC_API_URL", KB_SYNC_API_URL);
  await fetchOrThrow(KB_SYNC_API_URL, { method: "POST" });
}

export async function uploadDocument(
  file: File,
  options?: { sourceUrl?: string },
): Promise<UploadDocumentResponse> {
  assertConfigured("VITE_UPLOAD_API_BASE_URL", UPLOAD_API_BASE_URL);

  const safeName = encodeURIComponent(file.name);
  let url = joinUrl(UPLOAD_API_BASE_URL, `${DOCS_API_UPLOAD_PATH}/${safeName}`);
  const normalizedSourceUrl = options?.sourceUrl?.trim();
  if (normalizedSourceUrl) {
    const separator = url.includes("?") ? "&" : "?";
    url = `${url}${separator}sourceUrl=${encodeURIComponent(normalizedSourceUrl)}`;
  }

  const form = new FormData();
  form.append("file", file, file.name); // field name can be "file"

  const res = await fetchOrThrow(url, {
    method: "POST",
    body: form,
    // IMPORTANT:
    // - do NOT set Content-Type manually
    // - browser will set multipart/form-data; boundary=...
  });

  const payload = (await res
    .json()
    .catch(() => null)) as UploadDocumentResponse | null;
  if (!payload?.status) {
    throw new Error("Upload API returned an unexpected response.");
  }

  if (payload.status === "accepted") {
    await triggerKbSync();
  }

  return payload;
}

export async function uploadDocumentAnyway(
  uploadId: string,
  quarantineKey: string,
  options?: { sourceUrl?: string },
): Promise<void> {
  assertConfigured(
    "VITE_DOCUMENTS_UPLOAD_OVERRIDE",
    DOCS_API_UPLOAD_OVERRIDE_URL,
  );

  const url = DOCS_API_UPLOAD_OVERRIDE_URL;
  const normalizedSourceUrl = options?.sourceUrl?.trim();
  const requestBody: {
    uploadId: string;
    quarantineKey: string;
    sourceUrl?: string;
  } = {
    uploadId,
    quarantineKey,
  };
  if (normalizedSourceUrl) {
    requestBody.sourceUrl = normalizedSourceUrl;
  }

  await fetchOrThrow(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(requestBody),
  });

  await triggerKbSync();
}

export async function cancelDocumentUpload(
  quarantineKey: string,
): Promise<void> {
  // Can comment out if TTL implemented for rejected documents.
  // assertConfigured("VITE_DOCUMENTS_API_BASE_URL", DOCS_API_BASE_URL);
  // // Mirror deleteDocument, but target the quarantine object endpoint instead.
  // const url = joinUrl(
  //     DOCS_API_BASE_URL,
  //     `${DOCS_API_CANCEL_UPLOAD_PATH}/${encodeURIComponent(quarantineKey)}`,
  // );
  // await fetchOrThrow(url, { method: "DELETE" });
}

export async function getDocumentDownloadUrl(pdfName: string): Promise<string> {
  assertConfigured("VITE_PRESIGNED_URL_BASE_URL", PRESIGNED_BASE_URL);

  const url = joinUrl(PRESIGNED_BASE_URL, encodeURIComponent(pdfName));
  const res = await fetchOrThrow(url, { method: "GET" });

  const contentType = (res.headers.get("content-type") || "").toLowerCase();

  if (contentType.includes("application/json")) {
    const payload = (await res.json().catch(() => null)) as {
      url?: string;
      presignedUrl?: string;
      presigned_url?: string;
    } | null;

    const downloadUrl =
      payload?.url ?? payload?.presignedUrl ?? payload?.presigned_url;
    if (!downloadUrl) {
      throw new Error("Presigned URL API returned an unexpected response.");
    }
    return downloadUrl;
  }

  const text = (await res.text()).trim();
  if (!text) {
    throw new Error("Presigned URL API returned an empty response.");
  }

  return text;
}

export async function getDocumentBlob(documentKey: string): Promise<Blob> {
  const downloadUrl = await getDocumentDownloadUrl(documentKey);
  const response = await fetchOrThrow(downloadUrl, { method: "GET" });
  const blob = await response.blob();

  if (
    documentKey.toLowerCase().endsWith(".pdf") &&
    (!blob.type || blob.type === "application/octet-stream")
  ) {
    return new Blob([blob], { type: "application/pdf" });
  }

  return blob;
}
