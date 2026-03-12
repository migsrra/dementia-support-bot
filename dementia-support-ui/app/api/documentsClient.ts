export type DocumentItem = {
    key: string;
    sizeBytes?: number;
    lastModified?: string;
};

export type UploadAcceptedResponse = {
    status: "accepted";
    message?: string;
    kbKey?: string;
};

export type UploadRejectedReason =
    | "unable_to_extract_text"
    | "possible_phi_detected"
    | "not_relevant"
    | string;

export type UploadPhiTrait = {
    name?: string;
    score?: number;
};

export type UploadPhiAttribute = {
    type?: string;
    category?: string;
    score?: number;
    text?: string;
    relationshipScore?: number;
    relationshipType?: string;
    beginOffset?: number;
    endOffset?: number;
    traits?: UploadPhiTrait[];
};

export type UploadPhiEntity = {
    text?: string;
    type?: string;
    category?: string;
    score?: number;
    beginOffset?: number;
    endOffset?: number;
    chunkIndex?: number;
    traits?: UploadPhiTrait[];
    attributes?: UploadPhiAttribute[];
};

export type UploadRejectedResponse = {
    status: "rejected";
    reason: UploadRejectedReason;
    uploadId?: string;
    quarantineKey?: string;
    entities?: UploadPhiEntity[];
};

export type UploadDocumentResponse = UploadAcceptedResponse | UploadRejectedResponse;

const DOCS_API_BASE_URL = import.meta.env.VITE_DOCUMENTS_API_BASE_URL ?? "";
const DOCS_API_LIST_PATH = import.meta.env.VITE_DOCUMENTS_LIST_PATH ?? "/documents/list";
const DOCS_API_DELETE_PATH = import.meta.env.VITE_DOCUMENTS_DELETE_PATH ?? "/documents/delete";

const UPLOAD_API_BASE_URL = import.meta.env.VITE_UPLOAD_API_BASE_URL ?? DOCS_API_BASE_URL;
const DOCS_API_UPLOAD_PATH = import.meta.env.VITE_DOCUMENTS_UPLOAD_PATH ?? "/documents/upload";

const DOCS_API_UPLOAD_OVERRIDE_URL = import.meta.env.VITE_DOCUMENTS_UPLOAD_OVERRIDE ?? "";
const DOCS_API_CANCEL_UPLOAD_PATH =
    import.meta.env.VITE_DOCUMENTS_CANCEL_UPLOAD_PATH ?? "/documents/cancel-upload";
const KB_SYNC_API_URL = import.meta.env.VITE_KB_SYNC_API_URL ?? "";


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

export async function listDocuments(): Promise<DocumentItem[]> {

    assertConfigured("VITE_DOCUMENTS_API_BASE_URL", DOCS_API_BASE_URL);

    const url = joinUrl(DOCS_API_BASE_URL, DOCS_API_LIST_PATH);
    const res = await fetchOrThrow(url, { method: "GET" });

    const contentType = res.headers.get("content-type") || "";
    const xmlText = await res.text();


    // Its XML for some reason
    const doc = new DOMParser().parseFromString(xmlText, "application/xml");
    const contents = Array.from(doc.getElementsByTagName("Contents"));

    return contents
        .map((c) => {
            const key = c.getElementsByTagName("Key")[0]?.textContent ?? "";
            const size = c.getElementsByTagName("Size")[0]?.textContent ?? undefined;
            const lastModified = c.getElementsByTagName("LastModified")[0]?.textContent ?? undefined;

            return {
                key,
                sizeBytes: size ? Number(size) : undefined,
                lastModified: lastModified || undefined,
            } satisfies DocumentItem;
        })
        .filter((d) => d.key);


}

export async function deleteDocument(key: string): Promise<void> {
    assertConfigured("VITE_DOCUMENTS_API_BASE_URL", DOCS_API_BASE_URL);

    // IMPORTANT: encode key so spaces/#/? don't break the URL
    const url = joinUrl(DOCS_API_BASE_URL, `${DOCS_API_DELETE_PATH}/${encodeURIComponent(key)}`);
    await fetchOrThrow(url, { method: "DELETE" });
    await triggerKbSync();
}


async function triggerKbSync() {
    assertConfigured("VITE_KB_SYNC_API_URL", KB_SYNC_API_URL);
    await fetchOrThrow(KB_SYNC_API_URL, { method: "POST" });
}

 
export async function uploadDocument(file: File): Promise<UploadDocumentResponse> {
  assertConfigured("VITE_UPLOAD_API_BASE_URL", UPLOAD_API_BASE_URL);

  const safeName = encodeURIComponent(file.name);
  const url = joinUrl(UPLOAD_API_BASE_URL, `${DOCS_API_UPLOAD_PATH}/${safeName}`);

  const form = new FormData();
  form.append("file", file, file.name); // field name can be "file"

  const res = await fetchOrThrow(url, {
    method: "POST",
    body: form,
    // IMPORTANT:
    // - do NOT set Content-Type manually
    // - browser will set multipart/form-data; boundary=...
  });

  const payload = (await res.json().catch(() => null)) as UploadDocumentResponse | null;
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
): Promise<void> {
    assertConfigured("VITE_DOCUMENTS_UPLOAD_OVERRIDE", DOCS_API_UPLOAD_OVERRIDE_URL);

    const url = DOCS_API_UPLOAD_OVERRIDE_URL;
    await fetchOrThrow(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ uploadId, quarantineKey }),
    });

    await triggerKbSync();
}

export async function cancelDocumentUpload(quarantineKey: string): Promise<void> {


    // Can comment out if TTL implemented for rejected documents. 

    // assertConfigured("VITE_DOCUMENTS_API_BASE_URL", DOCS_API_BASE_URL);

    // // Mirror deleteDocument, but target the quarantine object endpoint instead.
    // const url = joinUrl(
    //     DOCS_API_BASE_URL,
    //     `${DOCS_API_CANCEL_UPLOAD_PATH}/${encodeURIComponent(quarantineKey)}`,
    // );
    // await fetchOrThrow(url, { method: "DELETE" });
}


// Future Lambda wiring: replace mock implementations above with real fetch calls
// using DOCS_API_BASE_URL + *_PATH placeholders. Keep this module as the single
// integration boundary for document list/upload/delete operations.
void DOCS_API_BASE_URL;
void DOCS_API_LIST_PATH;
void DOCS_API_UPLOAD_PATH;
void DOCS_API_UPLOAD_OVERRIDE_URL;
void DOCS_API_CANCEL_UPLOAD_PATH;
void DOCS_API_DELETE_PATH;
void KB_SYNC_API_URL;
