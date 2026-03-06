export type DocumentItem = {
    key: string;
    sizeBytes?: number;
    lastModified?: string;
};

const DOCS_API_BASE_URL = import.meta.env.VITE_DOCUMENTS_API_BASE_URL ?? "";
const DOCS_API_LIST_PATH = import.meta.env.VITE_DOCUMENTS_LIST_PATH ?? "/documents/list";
const DOCS_API_DELETE_PATH = import.meta.env.VITE_DOCUMENTS_DELETE_PATH ?? "/documents/delete";

const UPLOAD_API_BASE_URL = import.meta.env.VITE_UPLOAD_API_BASE_URL ?? DOCS_API_BASE_URL;
const DOCS_API_UPLOAD_PATH = import.meta.env.VITE_DOCUMENTS_UPLOAD_PATH ?? "/documents/upload";


// let mockDocuments: DocumentItem[] = [
//     {
//         key: "caregiver-handbook.pdf",
//         sizeBytes: 2_781_331,
//         lastModified: "2026-03-03T14:11:02.000Z",
//     },
//     {
//         key: "daily-routine-template.md",
//         sizeBytes: 24_102,
//         lastModified: "2026-03-04T10:34:48.000Z",
//     },
//     {
//         key: "appointment-notes.txt",
//         sizeBytes: 15_119,
//         lastModified: "2026-03-02T19:06:15.000Z",
//     },
// ];

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
}




function wait(ms: number) {
    return new Promise<void>((resolve) => {
        setTimeout(resolve, ms);
    });
}

 
export async function uploadDocument(file: File): Promise<void> {
  assertConfigured("VITE_UPLOAD_API_BASE_URL", UPLOAD_API_BASE_URL);

  const safeName = encodeURIComponent(file.name);
  const url = joinUrl(UPLOAD_API_BASE_URL, `${DOCS_API_UPLOAD_PATH}/${safeName}`);

  const form = new FormData();
  form.append("file", file, file.name); // field name can be "file"

  await fetchOrThrow(url, {
    method: "POST",
    body: form,
    // IMPORTANT:
    // - do NOT set Content-Type manually
    // - browser will set multipart/form-data; boundary=...
  });
}


// Future Lambda wiring: replace mock implementations above with real fetch calls
// using DOCS_API_BASE_URL + *_PATH placeholders. Keep this module as the single
// integration boundary for document list/upload/delete operations.
void DOCS_API_BASE_URL;
void DOCS_API_LIST_PATH;
void DOCS_API_UPLOAD_PATH;
void DOCS_API_DELETE_PATH;
