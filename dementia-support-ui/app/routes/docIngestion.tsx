import type { Route } from "./+types/docIngestion";
import {
  Alert,
  Badge,
  Box,
  Button,
  Container,
  Group,
  Modal,
  PasswordInput,
  UnstyledButton,
  Paper,
  Progress,
  Stack,
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";
import {
  cancelDocumentUpload,
  type DocumentItem,
  deleteDocument,
  getDocumentBlob,
  listDocuments,
  triggerKbSync,
  type UploadScreeningSummary,
  uploadDocumentAnyway,
  type UploadPhiGroup,
  type UploadRejectedResponse,
  uploadDocument,
  type UploadDocumentResponse,
} from "~/api/documentsClient";
import {
  deleteUnsupportedQuery,
  listUnsupportedQueries,
  type UnsupportedQuery,
  type UnsupportedQuerySortDirection,
} from "~/api/unsupportedQueriesClient";

const MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".pdf"];
const SUCCESS_ALERT_TTL_MS = 10000;
const INITIAL_PHI_GROUP_EXAMPLE_COUNT = 5;
const UNSUPPORTED_QUERY_PREVIEW_LENGTH = 160;
const UNSUPPORTED_QUERIES_PAGE_LIMIT = 25;
const SECONDARY_TEXT_COLOR = "gray.7";
const DEMO_MODE = true;
const DOC_INGESTION_AUTH_STORAGE_KEY = "doc-ingestion-demo-authenticated";
const DEMO_DOCTOR_USERNAME = "demoUser";
const DEMO_DOCTOR_PASSWORD = "1234";
const ACCEPTED_MIME_TYPES = new Set(["application/pdf"]);

type FileUploadTracking = {
  id: string; // unique identifier
  file: File;
  sourceUrl: string;
  status:
    | "pending"
    | "uploading"
    | "decision-pending"
    | "approved"
    | "rejected"
    | "completed"
    | "error";
  response?: UploadDocumentResponse;
  error?: string;
};

type SortDirection = "asc" | "desc";
type DocumentSortField = "filename" | "size" | "lastModified";

type UploadSuccessState = {
  title: string;
  message: string;
  screeningSummary?: UploadScreeningSummary;
} | null;

type PhiGroup = {
  key: string;
  label: string;
  items: UploadPhiGroup["items"];
};

type DocIngestionTab = "document-ingestion" | "unsupported-queries";
export function meta({}: Route.MetaArgs) {
  return [
    { title: "Document Manager" },
    {
      name: "description",
      content:
        "Manage RAG knowledge documents with mock S3 upload and delete actions.",
    },
  ];
}

function formatSize(sizeBytes?: number) {
  if (typeof sizeBytes !== "number") return "Unknown";
  if (sizeBytes < 1024) return `${sizeBytes} B`;
  if (sizeBytes < 1024 * 1024) return `${(sizeBytes / 1024).toFixed(1)} KB`;
  return `${(sizeBytes / (1024 * 1024)).toFixed(2)} MB`;
}

function formatDate(isoDate?: string) {
  if (!isoDate) return "Unknown";
  const date = new Date(isoDate);
  if (Number.isNaN(date.getTime())) return "Unknown";
  return date.toLocaleString();
}

function validateFile(file: File) {
  const extension = `.${file.name.split(".").pop()?.toLowerCase() ?? ""}`;
  if (
    !ACCEPTED_EXTENSIONS.includes(extension) &&
    !ACCEPTED_MIME_TYPES.has(file.type)
  ) {
    return "Unsupported file type. Allowed: pdf.";
  }
  if (file.size > MAX_UPLOAD_SIZE_BYTES) {
    return "File is too large. Maximum size is 5 MB.";
  }
  return null;
}

function formatPhiScore(score?: number) {
  if (typeof score !== "number") return "Unknown";
  return `${(score * 100).toFixed(1)}%`;
}

function getRejectedPhiStatus(response: UploadRejectedResponse) {
  const phiDetected =
    response.screeningSummary?.phiDetected ||
    response.reason === "possible_phi_detected" ||
    response.reason === "possible_phi_detected_and_not_relevant";

  return phiDetected
    ? "Review required. Potential protected health information was identified."
    : response.reason === "unable_to_extract_text"
      ? "Not completed. No readable text was available for PHI screening."
      : "Passed. No protected health information was detected.";
}

function getRejectedRelevanceStatus(response: UploadRejectedResponse) {
  const isNotRelevant =
    response.screeningSummary?.isRelevant === false ||
    response.reason === "not_relevant" ||
    response.reason === "possible_phi_detected_and_not_relevant";

  return isNotRelevant
    ? "Review required. The document does not appear relevant to the dementia knowledge base."
    : response.reason === "unable_to_extract_text"
      ? "Not completed. No readable text was available for relevance screening."
      : "Passed. The document appears relevant to the dementia knowledge base.";
}

function getAcceptedPhiStatus(summary?: UploadScreeningSummary) {
  return summary?.phiDetected
    ? "Review required. Potential protected health information was identified."
    : "Passed. No protected health information was detected.";
}

function getAcceptedRelevanceStatus(summary?: UploadScreeningSummary) {
  return summary?.isRelevant === false
    ? "Review required. The document does not appear relevant to the dementia knowledge base."
    : "Passed. The document appears relevant to the dementia knowledge base.";
}

function formatRejectedUploadSummary(
  response: UploadRejectedResponse,
  fileName: string,
) {
  const summary = response.screeningSummary;
  const phiDetected =
    summary?.phiDetected ||
    response.reason === "possible_phi_detected" ||
    response.reason === "possible_phi_detected_and_not_relevant";
  const isNotRelevant =
    summary?.isRelevant === false ||
    response.reason === "not_relevant" ||
    response.reason === "possible_phi_detected_and_not_relevant";

  if (phiDetected && isNotRelevant) {
    return (
      <>
        <strong>"{fileName}"</strong> may contain{" "}
        <strong>Protected Health Information (PHI)</strong> and does{" "}
        <strong>not</strong> appear <strong>relevant</strong> to the dementia
        knowledge base. Review the document before adding it.
      </>
    );
  }

  if (phiDetected) {
    return (
      <>
        <strong>"{fileName}"</strong> may contain{" "}
        <strong>Protected Health Information (PHI)</strong>. Review the document
        before adding it to the dementia knowledge base.
      </>
    );
  }

  if (isNotRelevant) {
    return (
      <>
        <strong>{fileName}</strong> does <strong>not</strong> appear{" "}
        <strong>relevant</strong> to the dementia knowledge base. Review the
        document before adding it.
      </>
    );
  }

  if (response.reason === "unable_to_extract_text") {
    return (
      <>
        <strong>No readable text</strong> could be extracted from{" "}
        <strong>{fileName}</strong>. Review the document before adding it to the
        dementia knowledge base.
      </>
    );
  }

  return (
    <>
      <strong>{fileName}</strong> requires <strong>manual review</strong> before
      it can be added to the dementia knowledge base.
    </>
  );
}

function getScreeningBadge(statusText: string) {
  if (statusText.startsWith("Passed.")) {
    return {
      color: "teal" as const,
      label: "Passed",
      detail: statusText.replace("Passed. ", ""),
    };
  }

  if (statusText.startsWith("Review required.")) {
    return {
      color: "red" as const,
      label: "Review required",
      detail: statusText.replace("Review required. ", ""),
    };
  }

  return {
    color: "gray" as const,
    label: "Not completed",
    detail: statusText.replace("Not completed. ", ""),
  };
}

function isPdfDocument(documentKey: string) {
  return documentKey.toLowerCase().endsWith(".pdf");
}

export default function DocIngestion() {
  const [isAuthResolved, setIsAuthResolved] = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [authError, setAuthError] = useState<string | null>(null);
  const [activeTab, setActiveTab] =
    useState<DocIngestionTab>("document-ingestion");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filterText, setFilterText] = useState("");
  const [documentSortField, setDocumentSortField] =
    useState<DocumentSortField>("lastModified");
  const [sortDirection, setSortDirection] = useState<SortDirection>("desc");
  const [uploadedFiles, setUploadedFiles] = useState<FileUploadTracking[]>([]);
  const [uploadBatchInProgress, setUploadBatchInProgress] = useState(false);
  const [sourceUrls, setSourceUrls] = useState<Record<string, string>>({});
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<UploadSuccessState>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isResolvingReviewBatch, setIsResolvingReviewBatch] = useState(false);
  const [visiblePhiGroupCounts, setVisiblePhiGroupCounts] = useState<
    Record<string, number>
  >({});
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [pendingDeleteKey, setPendingDeleteKey] = useState<string | null>(null);
  const [pendingDownloadKey, setPendingDownloadKey] = useState<string | null>(
    null,
  );
  const [previewDocumentKey, setPreviewDocumentKey] = useState<string | null>(
    null,
  );
  const [previewDocumentUrl, setPreviewDocumentUrl] = useState<string | null>(
    null,
  );
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteSuccess, setDeleteSuccess] = useState<string | null>(null);
  const [unsupportedQueries, setUnsupportedQueries] = useState<
    UnsupportedQuery[]
  >([]);
  const [isUnsupportedQueriesLoading, setIsUnsupportedQueriesLoading] =
    useState(true);
  const [unsupportedQueriesError, setUnsupportedQueriesError] = useState<
    string | null
  >(null);
  const [unsupportedDeleteError, setUnsupportedDeleteError] = useState<
    string | null
  >(null);
  const [pendingUnsupportedDeleteId, setPendingUnsupportedDeleteId] = useState<
    string | null
  >(null);
  const [expandedUnsupportedQueryIds, setExpandedUnsupportedQueryIds] =
    useState<Record<string, boolean>>({});
  const [unsupportedQuerySortDirection, setUnsupportedQuerySortDirection] =
    useState<UnsupportedQuerySortDirection>("latest");
  const [unsupportedQueriesPageIndex, setUnsupportedQueriesPageIndex] =
    useState(0);
  const [unsupportedQueriesPageTokens, setUnsupportedQueriesPageTokens] =
    useState<string[]>([""]);
  const [unsupportedQueriesNextToken, setUnsupportedQueriesNextToken] =
    useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const currentUnsupportedQueriesToken =
    unsupportedQueriesPageTokens[unsupportedQueriesPageIndex] || "";

  useEffect(() => {
    const storedAuthState =
      window.sessionStorage.getItem(DOC_INGESTION_AUTH_STORAGE_KEY) === "true";
    setIsAuthenticated(storedAuthState);
    setIsAuthResolved(true);
  }, []);

  const loadDocuments = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const items = await listDocuments();
      setDocuments(items);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to load documents.";
      setLoadError(message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isAuthResolved || !isAuthenticated) return;
    void loadDocuments();
  }, [isAuthResolved, isAuthenticated, loadDocuments]);

  const loadUnsupportedQueries = useCallback(async (pageToken?: string) => {
    setIsUnsupportedQueriesLoading(true);
    setUnsupportedQueriesError(null);
    try {
      const page = await listUnsupportedQueries({
        limit: UNSUPPORTED_QUERIES_PAGE_LIMIT,
        nextToken: pageToken,
        sortDirection: unsupportedQuerySortDirection,
      });
      setUnsupportedQueries(page.items);
      setUnsupportedQueriesNextToken(page.nextToken ?? null);
      setExpandedUnsupportedQueryIds({});
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Failed to load unsupported queries.";
      setUnsupportedQueriesError(message);
      setUnsupportedQueriesNextToken(null);
    } finally {
      setIsUnsupportedQueriesLoading(false);
    }
  }, [unsupportedQuerySortDirection]);

  useEffect(() => {
    if (!isAuthResolved || !isAuthenticated) return;
    void loadUnsupportedQueries(currentUnsupportedQueriesToken || undefined);
  }, [
    currentUnsupportedQueriesToken,
    isAuthResolved,
    isAuthenticated,
    loadUnsupportedQueries,
  ]);

  useEffect(() => {
    if (uploadBatchInProgress) {
      const timer = window.setInterval(() => {
        setUploadProgress((current) => (current >= 90 ? current : current + 8));
      }, 140);

      return () => {
        window.clearInterval(timer);
      };
    }
  }, [uploadBatchInProgress]);

  useEffect(() => {
    if (!uploadSuccess) return;

    const timer = window.setTimeout(() => {
      setUploadSuccess(null);
    }, SUCCESS_ALERT_TTL_MS);

    return () => {
      window.clearTimeout(timer);
    };
  }, [uploadSuccess]);

  useEffect(() => {
    if (!deleteSuccess) return;

    const timer = window.setTimeout(() => {
      setDeleteSuccess(null);
    }, SUCCESS_ALERT_TTL_MS);

    return () => {
      window.clearTimeout(timer);
    };
  }, [deleteSuccess]);

  useEffect(() => {
    setVisiblePhiGroupCounts({});
  }, [uploadedFiles]);

  useEffect(() => {
    return () => {
      if (previewDocumentUrl) {
        URL.revokeObjectURL(previewDocumentUrl);
      }
    };
  }, [previewDocumentUrl]);

  useEffect(() => {
    if (uploadBatchInProgress || isResolvingReviewBatch) return;
    if (uploadedFiles.length === 0) return;

    const hasPendingWork = uploadedFiles.some(
      (file) =>
        file.status === "pending" ||
        file.status === "uploading" ||
        file.status === "decision-pending",
    );
    if (hasPendingWork) return;

    const hasAnyCompletedUpload = uploadedFiles.some(
      (file) => file.status === "completed",
    );

    setIsResolvingReviewBatch(true);
    void (async () => {
      try {
        if (hasAnyCompletedUpload) {
          await triggerKbSync();
        }
        await loadDocuments();
        if (hasAnyCompletedUpload) {
          setUploadSuccess({
            title: "Review complete",
            message:
              "All documents in this review batch have been resolved and synced.",
          });
        }
        setUploadedFiles([]);
        setSourceUrls({});
        if (fileInputRef.current) fileInputRef.current.value = "";
      } catch (error) {
        const message =
          error instanceof Error
            ? error.message
            : "Failed to finalize document review batch.";
        setUploadError(message);
      } finally {
        setIsResolvingReviewBatch(false);
      }
    })();
  }, [
    isResolvingReviewBatch,
    loadDocuments,
    uploadBatchInProgress,
    uploadedFiles,
  ]);

  const filteredAndSortedDocuments = useMemo(() => {
    const filtered = documents.filter(
      (document) =>
        !document.key.endsWith(".metadata.json") &&
        document.key.toLowerCase().includes(filterText.toLowerCase()),
    );
    return filtered.sort((a, b) => {
      let result = 0;
      if (documentSortField === "filename") {
        result = a.key.localeCompare(b.key);
      } else if (documentSortField === "size") {
        result = (a.sizeBytes ?? -1) - (b.sizeBytes ?? -1);
      } else {
        result =
          new Date(a.lastModified ?? 0).getTime() -
          new Date(b.lastModified ?? 0).getTime();
      }
      return sortDirection === "asc" ? result : -result;
    });
  }, [documentSortField, documents, filterText, sortDirection]);

  const unsupportedQueriesPageNumber = unsupportedQueriesPageIndex + 1;
  const hasPreviousUnsupportedQueriesPage = unsupportedQueriesPageIndex > 0;
  const hasNextUnsupportedQueriesPage = Boolean(unsupportedQueriesNextToken);

  function handlePickedFiles(files: FileList | null) {
    setUploadError(null);
    if (!files || files.length === 0) {
      return;
    }

    const newFiles: FileUploadTracking[] = [];
    let hasError = false;

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const validationError = validateFile(file);
      if (validationError) {
        setUploadError(`${file.name}: ${validationError}`);
        hasError = true;
        continue;
      }
      newFiles.push({
        id: `${file.name}-${Date.now()}-${i}`,
        file,
        sourceUrl: "",
        status: "pending",
      });
    }

    if (newFiles.length > 0) {
      setUploadedFiles((current) => [...current, ...newFiles]);
    }
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (uploadBatchInProgress) return;
    const files = event.dataTransfer.files ?? null;
    handlePickedFiles(files);
  }

  function handleDragOver(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
  }

  async function handleUploadAll() {
    // Get files in pending status
    const pendingFiles = uploadedFiles.filter((f) => f.status === "pending");
    if (pendingFiles.length === 0) return;

    setUploadBatchInProgress(true);
    setUploadError(null);
    setUploadProgress(0);

    // Update all files to uploading status
    setUploadedFiles((current) =>
      current.map((f) =>
        pendingFiles.find((pf) => pf.id === f.id)
          ? { ...f, status: "uploading" as const }
          : f,
      ),
    );

    try {
      // Upload all files in parallel
      const uploadPromises = pendingFiles.map((fileTracking) =>
        uploadDocument(fileTracking.file, {
          sourceUrl: sourceUrls[fileTracking.id],
          deferKbSync: true,
        })
          .then((response) => ({
            id: fileTracking.id,
            response,
            error: null,
          }))
          .catch((error) => ({
            id: fileTracking.id,
            response: undefined,
            error: error instanceof Error ? error.message : "Upload failed",
          })),
      );

      const results = await Promise.all(uploadPromises);
      setUploadProgress(100);

      // Process results: separate accepted, rejected, and errors
      const acceptedCount = results.filter(
        (r) => r.response?.status === "accepted",
      ).length;
      const rejectedCount = results.filter(
        (r) => r.response?.status === "rejected",
      ).length;
      const errorCount = results.filter((r) => r.error).length;

      // Update file statuses based on results
      setUploadedFiles((current) =>
        current.map((f) => {
          const result = results.find((r) => r.id === f.id);
          if (!result) return f;

          if (result.error) {
            return { ...f, status: "error" as const, error: result.error };
          }

          if (result.response?.status === "accepted") {
            return {
              ...f,
              status: "completed" as const,
              response: result.response,
            };
          }

          if (result.response?.status === "rejected") {
            return {
              ...f,
              status: "decision-pending" as const,
              response: result.response,
            };
          }

          return f;
        }),
      );

      // Show success message if there are accepted files
      if (acceptedCount > 0) {
        setUploadSuccess({
          title: "Files processed",
          message: `${acceptedCount} file${acceptedCount === 1 ? "" : "s"} accepted${rejectedCount > 0 ? `, ${rejectedCount} needs review` : ""}${errorCount > 0 ? `, ${errorCount} error${errorCount === 1 ? "" : "s"}` : ""}`,
        });
      } else if (errorCount > 0) {
        setUploadError(
          `All uploads failed. ${errorCount} error${errorCount === 1 ? "" : "s"}.`,
        );
      }
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Upload batch failed.";
      setUploadError(message);
    } finally {
      setUploadBatchInProgress(false);
    }
  }

  function openDeleteModal(key: string) {
    if (uploadBatchInProgress) return;
    setDeleteError(null);
    setDeleteSuccess(null);
    setPendingDeleteKey(key);
    setIsDeleteModalOpen(true);
  }

  async function handleApproveFile(fileId: string) {
    const fileTracking = uploadedFiles.find((f) => f.id === fileId);
    if (!fileTracking?.response || fileTracking.response.status !== "rejected")
      return;

    const response = fileTracking.response;
    if (!response.uploadId || !response.quarantineKey) return;

    setUploadError(null);
    setUploadedFiles((current) =>
      current.map((f) =>
        f.id === fileId ? { ...f, status: "uploading" as const } : f,
      ),
    );

    try {
      await uploadDocumentAnyway(response.uploadId, response.quarantineKey, {
        sourceUrl: sourceUrls[fileId],
        deferKbSync: true,
      });
      setUploadedFiles((current) =>
        current.map((f) =>
          f.id === fileId ? { ...f, status: "completed" as const } : f,
        ),
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Approval failed.";
      setUploadError(message);
      setUploadedFiles((current) =>
        current.map((f) =>
          f.id === fileId
            ? { ...f, status: "decision-pending" as const, error: message }
            : f,
        ),
      );
    }
  }

  async function handleRejectFile(fileId: string) {
    const fileTracking = uploadedFiles.find((f) => f.id === fileId);
    if (!fileTracking?.response || fileTracking.response.status !== "rejected")
      return;

    const response = fileTracking.response;
    if (!response.quarantineKey) return;

    setUploadError(null);
    setUploadedFiles((current) =>
      current.map((f) =>
        f.id === fileId ? { ...f, status: "uploading" as const } : f,
      ),
    );

    try {
      await cancelDocumentUpload(response.quarantineKey);
      setUploadedFiles((current) =>
        current.map((f) =>
          f.id === fileId ? { ...f, status: "rejected" as const } : f,
        ),
      );
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Rejection failed.";
      setUploadError(message);
      setUploadedFiles((current) =>
        current.map((f) =>
          f.id === fileId
            ? { ...f, status: "decision-pending" as const, error: message }
            : f,
        ),
      );
    }
  }

  async function confirmDelete() {
    if (!pendingDeleteKey || isDeleting) return;

    setIsDeleting(true);
    setDeleteError(null);
    setDeleteSuccess(null);
    try {
      await deleteDocument(pendingDeleteKey);
      setDocuments((current) =>
        current.filter((document) => document.key !== pendingDeleteKey),
      );
      setDeleteSuccess(`Deleted ${pendingDeleteKey}`);
      setIsDeleteModalOpen(false);
      setPendingDeleteKey(null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Delete failed.";
      setDeleteError(message);
    } finally {
      setIsDeleting(false);
    }
  }

  async function handleDownloadDocument(documentKey: string) {
    if (pendingDownloadKey) return;

    setLoadError(null);
    setPendingDownloadKey(documentKey);

    try {
      const blob = await getDocumentBlob(documentKey);
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = objectUrl;
      link.download = documentKey.split("/").pop() || "download";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(objectUrl);
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Download failed.";
      setLoadError(message);
    } finally {
      setPendingDownloadKey(null);
    }
  }

  function closePreviewModal() {
    setPreviewDocumentKey(null);
    setPreviewDocumentUrl((current) => {
      if (current) {
        URL.revokeObjectURL(current);
      }
      return null;
    });
  }

  async function handlePreviewDocument(documentKey: string) {
    if (isPreviewLoading || !isPdfDocument(documentKey)) return;

    setLoadError(null);
    setPreviewDocumentKey(documentKey);
    setIsPreviewLoading(true);

    try {
      const blob = await getDocumentBlob(documentKey);
      const objectUrl = URL.createObjectURL(blob);
      setPreviewDocumentUrl((current) => {
        if (current) {
          URL.revokeObjectURL(current);
        }
        return objectUrl;
      });
    } catch (error) {
      setPreviewDocumentKey(null);
      const message =
        error instanceof Error ? error.message : "Preview failed.";
      setLoadError(message);
    } finally {
      setIsPreviewLoading(false);
    }
  }

  async function handleUnsupportedDelete(query: UnsupportedQuery) {
    if (pendingUnsupportedDeleteId) return;

    setPendingUnsupportedDeleteId(query.id);
    setUnsupportedDeleteError(null);
    try {
      await deleteUnsupportedQuery(query.id, { timestamp: query.timestamp });

      if (unsupportedQueries.length === 1 && unsupportedQueriesPageIndex > 0) {
        setUnsupportedQueriesNextToken(null);
        setUnsupportedQueriesPageIndex((current) => Math.max(0, current - 1));
      } else {
        void loadUnsupportedQueries(
          currentUnsupportedQueriesToken || undefined,
        );
      }
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Failed to delete unsupported query.";
      setUnsupportedDeleteError(message);
    } finally {
      setPendingUnsupportedDeleteId(null);
    }
  }

  function handleUnsupportedQueriesRefresh() {
    setUnsupportedQueriesError(null);
    setUnsupportedDeleteError(null);
    setUnsupportedQueriesNextToken(null);
    setUnsupportedQueriesPageTokens([""]);
    setUnsupportedQueriesPageIndex(0);

    if (unsupportedQueriesPageIndex === 0) {
      void loadUnsupportedQueries(undefined);
    }
  }

  function handleUnsupportedQueriesPreviousPage() {
    if (isUnsupportedQueriesLoading || unsupportedQueriesPageIndex === 0) {
      return;
    }

    setUnsupportedQueriesNextToken(null);
    setUnsupportedQueriesPageIndex((current) => Math.max(0, current - 1));
  }

  function handleUnsupportedQueriesNextPage() {
    const nextPageToken = unsupportedQueriesNextToken;
    if (isUnsupportedQueriesLoading || !nextPageToken) {
      return;
    }

    const nextPageIndex = unsupportedQueriesPageIndex + 1;
    setUnsupportedQueriesNextToken(null);
    setUnsupportedQueriesPageTokens((current) => {
      const updated = current.slice(0, nextPageIndex);
      updated[nextPageIndex] = nextPageToken;
      return updated;
    });
    setUnsupportedQueriesPageIndex(nextPageIndex);
  }

  function toggleUnsupportedQueryExpanded(queryId: string) {
    setExpandedUnsupportedQueryIds((current) => ({
      ...current,
      [queryId]: !current[queryId],
    }));
  }

  function handleDocumentSort(field: DocumentSortField) {
    if (documentSortField === field) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
      return;
    }

    setDocumentSortField(field);
    setSortDirection("asc");
  }

  function getDocumentSortIndicator(field: DocumentSortField) {
    if (documentSortField !== field) return "";
    return sortDirection === "asc" ? " ▲" : " ▼";
  }

  function toggleUnsupportedQuerySortDirection() {
    setUnsupportedQueriesNextToken(null);
    setUnsupportedQueriesPageTokens([""]);
    setUnsupportedQueriesPageIndex(0);
    setExpandedUnsupportedQueryIds({});
    setUnsupportedQuerySortDirection((current) =>
      current === "latest" ? "oldest" : "latest",
    );
  }

  function handleLogin(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (
      username.trim() === DEMO_DOCTOR_USERNAME &&
      password === DEMO_DOCTOR_PASSWORD
    ) {
      window.sessionStorage.setItem(DOC_INGESTION_AUTH_STORAGE_KEY, "true");
      setIsAuthenticated(true);
      setAuthError(null);
      setPassword("");
      return;
    }

    setAuthError("Incorrect username or password.");
  }

  function handleLogout() {
    window.sessionStorage.removeItem(DOC_INGESTION_AUTH_STORAGE_KEY);
    setIsAuthenticated(false);
    setPassword("");
    setAuthError(null);
    setUnsupportedQueries([]);
    setUnsupportedQueriesPageIndex(0);
    setUnsupportedQueriesPageTokens([""]);
    setUnsupportedQueriesNextToken(null);
    setUnsupportedQueriesError(null);
    setUnsupportedDeleteError(null);
    setExpandedUnsupportedQueryIds({});
    setUploadedFiles([]);
    setSourceUrls({});
    setPreviewDocumentKey(null);
    setPreviewDocumentUrl((current) => {
      if (current) {
        URL.revokeObjectURL(current);
      }
      return null;
    });
  }

  if (!isAuthResolved) {
    return (
      <Box className="page">
        <Container size="sm" py={64}>
          <Paper className="chat-card" p="xl" radius="lg" shadow="md">
            <Stack gap="sm" align="center">
              <Text size="sm" c={SECONDARY_TEXT_COLOR}>
                Physician Access
              </Text>
              <Title order={2}>Loading secure document manager</Title>
            </Stack>
          </Paper>
        </Container>
      </Box>
    );
  }

  if (!isAuthenticated) {
    return (
      <Box className="page">
        <Container size="sm" py={64}>
          <Stack gap="xl">
            <Group justify="space-between" align="flex-end">
              <Stack gap={6}>
                <Text size="sm" c={SECONDARY_TEXT_COLOR}>
                  {"Physician Access"}
                </Text>
                <Title order={1}>{"Sign-in to manage documents"}</Title>
              </Stack>
              <Button component={Link} to="/" variant="light">
                Back to Chat
              </Button>
            </Group>

            <Paper className="chat-card" p="xl" radius="lg" shadow="md">
              <form onSubmit={handleLogin}>
                <Stack gap="md">
                  {DEMO_MODE ? (
                    <>
                      <Text c={SECONDARY_TEXT_COLOR}>
                        This is a demo-only authentication layer for the
                        physician document management area.
                      </Text>
                      <Alert
                        color="blue"
                        variant="light"
                        title="Demo credentials"
                      >
                        Username: {DEMO_DOCTOR_USERNAME}
                        <br />
                        Password: {DEMO_DOCTOR_PASSWORD}
                      </Alert>
                    </>
                  ) : (
                    <Text c={SECONDARY_TEXT_COLOR}>
                      Enter your physician credentials to access document
                      management.
                    </Text>
                  )}
                  <TextInput
                    label="Username"
                    placeholder={
                      DEMO_MODE ? DEMO_DOCTOR_USERNAME : "Enter username"
                    }
                    value={username}
                    onChange={(event) => {
                      setUsername(event.currentTarget.value);
                      if (authError) setAuthError(null);
                    }}
                    autoComplete="username"
                  />
                  <PasswordInput
                    label="Password"
                    placeholder="Enter password"
                    value={password}
                    onChange={(event) => {
                      setPassword(event.currentTarget.value);
                      if (authError) setAuthError(null);
                    }}
                    autoComplete="current-password"
                  />
                  {authError ? (
                    <Alert color="red" variant="light" title="Sign-in failed">
                      {authError}
                    </Alert>
                  ) : null}
                  <Button type="submit">
                    {DEMO_MODE ? "Sign-in" : "Sign in"}
                  </Button>
                </Stack>
              </form>
            </Paper>
          </Stack>
        </Container>
      </Box>
    );
  }

  return (
    <Box className="page">
      <Container size="xl" py={48}>
        <Stack gap="xl">
          <Group justify="space-between" align="flex-end">
            <Stack gap={6}>
              <Text size="sm" c={SECONDARY_TEXT_COLOR}>
                Knowledge Base Management
              </Text>
              <Title order={1}>
                {activeTab === "unsupported-queries"
                  ? "Review unsupported queries"
                  : "Upload and manage knowledge base documents"}
              </Title>
            </Stack>
            <Button
              component={Link}
              to="/"
              variant="light"
              onClick={handleLogout}
            >
              Back to Chat (log out)
            </Button>
          </Group>

          <Tabs
            value={activeTab}
            onChange={(value) =>
              setActiveTab((value as DocIngestionTab) ?? "document-ingestion")
            }
          >
            <Tabs.List>
              <Tabs.Tab value="document-ingestion">Manage Documents</Tabs.Tab>
              <Tabs.Tab value="unsupported-queries">
                Unsupported Queries
              </Tabs.Tab>
            </Tabs.List>

            <Tabs.Panel value="document-ingestion" pt="lg">
              <Stack gap="xl">
                <Paper className="chat-card" p="lg" radius="lg" shadow="md">
                  <Stack gap="md">
                    <Group justify="space-between">
                      <Text fw={600}>Upload a document</Text>
                    </Group>
                    <Paper
                      className="upload-dropzone"
                      radius="md"
                      p="lg"
                      onDrop={handleDrop}
                      onDragOver={handleDragOver}
                      style={{
                        opacity: uploadBatchInProgress ? 0.6 : 1,
                        pointerEvents: uploadBatchInProgress
                          ? "none"
                          : undefined,
                      }}
                    >
                      <Stack gap="sm" align="center">
                        <Text fw={600}>Drag and drop files here</Text>
                        <Text size="sm" c={SECONDARY_TEXT_COLOR}>
                          Supported: {ACCEPTED_EXTENSIONS.join(", ")} (max 5 MB
                          each)
                        </Text>
                        <input
                          ref={fileInputRef}
                          type="file"
                          accept={ACCEPTED_EXTENSIONS.join(",")}
                          multiple
                          className="hidden-file-input"
                          onChange={(event) =>
                            handlePickedFiles(event.currentTarget.files)
                          }
                          disabled={uploadBatchInProgress}
                        />
                        <Button
                          variant="default"
                          onClick={() => fileInputRef.current?.click()}
                          disabled={uploadBatchInProgress}
                        >
                          Choose Files
                        </Button>
                      </Stack>
                    </Paper>

                    {uploadedFiles.length > 0 ? (
                      <Paper withBorder radius="md" p="sm">
                        <Stack gap="sm">
                          <Text fw={600} size="sm">
                            Selected files ({uploadedFiles.length})
                          </Text>
                          {uploadedFiles.map((fileTracking) => (
                            <Paper
                              key={fileTracking.id}
                              withBorder
                              radius="sm"
                              p="xs"
                              bg="gray.0"
                            >
                              <Stack gap="xs">
                                <Group justify="space-between" align="center">
                                  <div>
                                    <Text fw={600} size="sm">
                                      {fileTracking.file.name}
                                    </Text>
                                    <Text size="xs" c={SECONDARY_TEXT_COLOR}>
                                      {formatSize(fileTracking.file.size)}
                                    </Text>
                                  </div>
                                  <Badge
                                    variant="light"
                                    color={
                                      fileTracking.status === "completed"
                                        ? "teal"
                                        : fileTracking.status === "rejected"
                                          ? "red"
                                          : fileTracking.status === "error"
                                            ? "red"
                                            : fileTracking.status ===
                                                "decision-pending"
                                              ? "yellow"
                                              : fileTracking.status ===
                                                  "uploading"
                                                ? "blue"
                                                : "gray"
                                    }
                                  >
                                    {fileTracking.status === "pending"
                                      ? "Ready"
                                      : fileTracking.status === "uploading"
                                        ? "Uploading..."
                                        : fileTracking.status ===
                                            "decision-pending"
                                          ? "Review needed"
                                          : fileTracking.status}
                                  </Badge>
                                </Group>
                                <TextInput
                                  label="Source URL (optional)"
                                  placeholder="https://example.com/document.pdf"
                                  size="xs"
                                  value={sourceUrls[fileTracking.id] ?? ""}
                                  onChange={(event) => {
                                    const value =
                                      event.currentTarget?.value ??
                                      event.target?.value ??
                                      "";
                                    setSourceUrls((current) => ({
                                      ...current,
                                      [fileTracking.id]: value,
                                    }));
                                  }}
                                  disabled={
                                    uploadBatchInProgress ||
                                    fileTracking.status !== "pending"
                                  }
                                />
                              </Stack>
                            </Paper>
                          ))}
                          <Group gap="sm">
                            <Button
                              onClick={() => void handleUploadAll()}
                              loading={uploadBatchInProgress}
                              disabled={
                                uploadBatchInProgress ||
                                !uploadedFiles.some(
                                  (f) => f.status === "pending",
                                )
                              }
                            >
                              Upload All
                            </Button>
                            <Button
                              variant="default"
                              onClick={() => {
                                setUploadedFiles([]);
                                setSourceUrls({});
                                if (fileInputRef.current)
                                  fileInputRef.current.value = "";
                              }}
                              disabled={uploadBatchInProgress}
                            >
                              Clear
                            </Button>
                          </Group>
                        </Stack>
                      </Paper>
                    ) : null}

                    {uploadBatchInProgress ? (
                      <Progress
                        value={uploadProgress}
                        animated
                        size="lg"
                        radius="xl"
                      />
                    ) : null}

                    {uploadError ? (
                      <Alert
                        color="red"
                        variant="light"
                        title="Upload error"
                        withCloseButton
                        closeButtonLabel="Dismiss upload error"
                        onClose={() => setUploadError(null)}
                      >
                        {uploadError}
                      </Alert>
                    ) : null}
                    {uploadSuccess ? (
                      <Alert
                        color="teal"
                        variant="light"
                        title={uploadSuccess.title}
                        withCloseButton
                        closeButtonLabel="Dismiss upload success"
                        onClose={() => setUploadSuccess(null)}
                      >
                        {uploadSuccess.message}
                      </Alert>
                    ) : null}
                    {uploadedFiles.filter(
                      (f) => f.status === "decision-pending",
                    ).length > 0 ? (
                      <Alert
                        color="yellow"
                        variant="light"
                        title="Documents review required"
                      >
                        <Stack gap="md">
                          {uploadedFiles
                            .filter((f) => f.status === "decision-pending")
                            .map((fileTracking) => {
                              if (
                                !fileTracking.response ||
                                fileTracking.response.status !== "rejected"
                              ) {
                                return null;
                              }

                              const response = fileTracking.response;
                              const phiStatus = getScreeningBadge(
                                getRejectedPhiStatus(response),
                              );
                              const relevanceStatus = getScreeningBadge(
                                getRejectedRelevanceStatus(response),
                              );
                              const phiGroups = (response.phiGroups ?? []).map(
                                (group) => ({
                                  key: group.key,
                                  label: group.label,
                                  items: group.items,
                                }),
                              );

                              return (
                                <Paper
                                  key={fileTracking.id}
                                  withBorder
                                  radius="md"
                                  p="md"
                                >
                                  <Stack gap="sm">
                                    <Text fw={600}>
                                      {fileTracking.file.name}
                                    </Text>

                                    <Stack gap={4}>
                                      <Stack gap={2}>
                                        <Group gap="xs" align="center">
                                          <Text size="sm" fw={600}>
                                            PHI screening
                                          </Text>
                                          {phiStatus ? (
                                            <Badge
                                              color={phiStatus.color}
                                              variant="light"
                                            >
                                              {phiStatus.label}
                                            </Badge>
                                          ) : null}
                                        </Group>
                                        {phiStatus ? (
                                          <Text
                                            size="sm"
                                            c={SECONDARY_TEXT_COLOR}
                                          >
                                            {phiStatus.detail}
                                          </Text>
                                        ) : null}
                                      </Stack>
                                      <Stack gap={2}>
                                        <Group gap="xs" align="center">
                                          <Text size="sm" fw={600}>
                                            Relevance assessment
                                          </Text>
                                          {relevanceStatus ? (
                                            <Badge
                                              color={relevanceStatus.color}
                                              variant="light"
                                            >
                                              {relevanceStatus.label}
                                            </Badge>
                                          ) : null}
                                        </Group>
                                        {relevanceStatus ? (
                                          <Text
                                            size="sm"
                                            c={SECONDARY_TEXT_COLOR}
                                          >
                                            {relevanceStatus.detail}
                                          </Text>
                                        ) : null}
                                      </Stack>
                                    </Stack>

                                    <Text size="sm">
                                      {formatRejectedUploadSummary(
                                        response,
                                        fileTracking.file.name,
                                      )}
                                    </Text>

                                    {response.screeningSummary?.isRelevant ===
                                      false ||
                                    response.reason === "not_relevant" ||
                                    response.reason ===
                                      "possible_phi_detected_and_not_relevant" ? (
                                      <Paper withBorder radius="md" p="sm">
                                        <Stack gap={4}>
                                          <Text size="sm" fw={600}>
                                            Relevance screening
                                          </Text>
                                          <Text size="sm">
                                            Result:{" "}
                                            <strong>Not relevant</strong>
                                          </Text>
                                          {response.screeningSummary
                                            ?.relevanceReason ? (
                                            <Text size="sm">
                                              Reason:{" "}
                                              {
                                                response.screeningSummary
                                                  .relevanceReason
                                              }
                                            </Text>
                                          ) : null}
                                        </Stack>
                                      </Paper>
                                    ) : null}

                                    {response.screeningSummary?.phiDetected ||
                                    response.reason ===
                                      "possible_phi_detected" ||
                                    response.reason ===
                                      "possible_phi_detected_and_not_relevant" ? (
                                      <Paper withBorder radius="md" p="sm">
                                        <Stack gap={6}>
                                          <Stack gap={0}>
                                            <Text size="sm" fw={600}>
                                              PHI Screening
                                            </Text>
                                            <Text
                                              size="sm"
                                              c={SECONDARY_TEXT_COLOR}
                                            >
                                              Detected Categories (% =
                                              Confidence)
                                            </Text>
                                          </Stack>
                                          {phiGroups.length ? (
                                            phiGroups.map((group) => {
                                              const visibleCount =
                                                visiblePhiGroupCounts[
                                                  `${fileTracking.id}-${group.key}`
                                                ] ??
                                                INITIAL_PHI_GROUP_EXAMPLE_COUNT;
                                              const visibleItems =
                                                group.items.slice(
                                                  0,
                                                  visibleCount,
                                                );
                                              const remainingCount = Math.max(
                                                group.items.length -
                                                  visibleItems.length,
                                                0,
                                              );

                                              return (
                                                <Stack
                                                  key={`${fileTracking.id}-${group.key}`}
                                                  gap={4}
                                                >
                                                  <Box
                                                    component="ul"
                                                    m={0}
                                                    pl="xl"
                                                    style={{
                                                      listStyleType: "disc",
                                                    }}
                                                  >
                                                    <Text
                                                      component="li"
                                                      size="sm"
                                                      fw={600}
                                                    >
                                                      {group.label} (
                                                      {group.items.length})
                                                    </Text>
                                                  </Box>
                                                  <Box
                                                    component="ul"
                                                    m={0}
                                                    ml="lg"
                                                    pl="1.75rem"
                                                    style={{
                                                      listStyleType: "circle",
                                                    }}
                                                  >
                                                    {visibleItems.map(
                                                      (entity, index) => (
                                                        <Text
                                                          key={`${fileTracking.id}-${group.key}-${entity.text}-${index}`}
                                                          component="li"
                                                          size="sm"
                                                        >
                                                          {entity.text ||
                                                            "Unknown"}{" "}
                                                          (
                                                          {formatPhiScore(
                                                            entity.score,
                                                          )}
                                                          )
                                                        </Text>
                                                      ),
                                                    )}
                                                  </Box>
                                                  {remainingCount > 0 ? (
                                                    <Group gap="xs">
                                                      <Button
                                                        size="xs"
                                                        variant="default"
                                                        onClick={() =>
                                                          setVisiblePhiGroupCounts(
                                                            (current) => ({
                                                              ...current,
                                                              [`${fileTracking.id}-${group.key}`]:
                                                                Math.min(
                                                                  visibleCount +
                                                                    INITIAL_PHI_GROUP_EXAMPLE_COUNT,
                                                                  group.items
                                                                    .length,
                                                                ),
                                                            }),
                                                          )
                                                        }
                                                      >
                                                        Show{" "}
                                                        {Math.min(
                                                          INITIAL_PHI_GROUP_EXAMPLE_COUNT,
                                                          remainingCount,
                                                        )}{" "}
                                                        more
                                                      </Button>
                                                      <Button
                                                        size="xs"
                                                        variant="subtle"
                                                        onClick={() =>
                                                          setVisiblePhiGroupCounts(
                                                            (current) => ({
                                                              ...current,
                                                              [`${fileTracking.id}-${group.key}`]:
                                                                group.items
                                                                  .length,
                                                            }),
                                                          )
                                                        }
                                                      >
                                                        Show all
                                                      </Button>
                                                    </Group>
                                                  ) : null}
                                                </Stack>
                                              );
                                            })
                                          ) : (
                                            <Text
                                              size="sm"
                                              c={SECONDARY_TEXT_COLOR}
                                            >
                                              No PHI entries above 80%
                                              confidence were found to display.
                                            </Text>
                                          )}
                                        </Stack>
                                      </Paper>
                                    ) : null}

                                    <Group gap="sm">
                                      <Button
                                        variant="default"
                                        onClick={() =>
                                          void handleRejectFile(fileTracking.id)
                                        }
                                        loading={
                                          fileTracking.status === "uploading"
                                        }
                                        disabled={
                                          fileTracking.status === "uploading"
                                        }
                                      >
                                        Reject
                                      </Button>
                                      <Button
                                        color="yellow"
                                        onClick={() =>
                                          void handleApproveFile(
                                            fileTracking.id,
                                          )
                                        }
                                        loading={
                                          fileTracking.status === "uploading"
                                        }
                                        disabled={
                                          fileTracking.status === "uploading"
                                        }
                                      >
                                        Upload Anyway
                                      </Button>
                                    </Group>
                                  </Stack>
                                </Paper>
                              );
                            })}
                        </Stack>
                      </Alert>
                    ) : null}
                  </Stack>
                </Paper>

                <Paper className="chat-card" p="lg" radius="lg" shadow="md">
                  <Stack gap="md">
                    <Group justify="space-between" align="center">
                      <Text fw={600}>Knowledge Base Documents</Text>
                      <Button
                        variant="default"
                        onClick={() => void loadDocuments()}
                        loading={isLoading}
                      >
                        Refresh
                      </Button>
                    </Group>

                    <TextInput
                      placeholder="Search filename..."
                      value={filterText}
                      onChange={(event) =>
                        setFilterText(event.currentTarget.value)
                      }
                      disabled={isLoading}
                    />

                    {loadError ? (
                      <Alert
                        color="red"
                        variant="light"
                        title="Could not load documents"
                        withCloseButton
                        closeButtonLabel="Dismiss load error"
                        onClose={() => setLoadError(null)}
                      >
                        {loadError}
                      </Alert>
                    ) : null}

                    {deleteError ? (
                      <Alert
                        color="red"
                        variant="light"
                        title="Delete error"
                        withCloseButton
                        closeButtonLabel="Dismiss delete error"
                        onClose={() => setDeleteError(null)}
                      >
                        {deleteError}
                      </Alert>
                    ) : null}
                    {deleteSuccess ? (
                      <Alert
                        color="teal"
                        variant="light"
                        title="Delete complete"
                        withCloseButton
                        closeButtonLabel="Dismiss delete success"
                        onClose={() => setDeleteSuccess(null)}
                      >
                        {deleteSuccess}
                      </Alert>
                    ) : null}

                    {isLoading ? (
                      <Text c={SECONDARY_TEXT_COLOR}>Loading documents...</Text>
                    ) : filteredAndSortedDocuments.length === 0 ? (
                      <Text c={SECONDARY_TEXT_COLOR}>
                        No documents found for this filter.
                      </Text>
                    ) : (
                      <Stack gap="sm">
                        <Group justify="space-between" align="center">
                          <Text size="sm" c={SECONDARY_TEXT_COLOR}>
                            {filteredAndSortedDocuments.length} document
                            {filteredAndSortedDocuments.length === 1 ? "" : "s"}
                          </Text>
                        </Group>
                        <Table striped highlightOnHover withTableBorder>
                          <Table.Thead>
                            <Table.Tr>
                              <Table.Th>
                                <UnstyledButton
                                  onClick={() => handleDocumentSort("filename")}
                                  style={{
                                    color: "inherit",
                                    font: "inherit",
                                    fontWeight: "inherit",
                                    display: "inline-flex",
                                    alignItems: "center",
                                    gap: 4,
                                    cursor: "pointer",
                                  }}
                                >
                                  Filename{getDocumentSortIndicator("filename")}
                                </UnstyledButton>
                              </Table.Th>
                              <Table.Th>
                                <UnstyledButton
                                  onClick={() => handleDocumentSort("size")}
                                  style={{
                                    color: "inherit",
                                    font: "inherit",
                                    fontWeight: "inherit",
                                    display: "inline-flex",
                                    alignItems: "center",
                                    gap: 4,
                                    cursor: "pointer",
                                  }}
                                >
                                  Size{getDocumentSortIndicator("size")}
                                </UnstyledButton>
                              </Table.Th>
                              <Table.Th>
                                <UnstyledButton
                                  onClick={() =>
                                    handleDocumentSort("lastModified")
                                  }
                                  style={{
                                    color: "inherit",
                                    font: "inherit",
                                    fontWeight: "inherit",
                                    display: "inline-flex",
                                    alignItems: "center",
                                    gap: 4,
                                    cursor: "pointer",
                                  }}
                                >
                                  Last Modified
                                  {getDocumentSortIndicator("lastModified")}
                                </UnstyledButton>
                              </Table.Th>
                              <Table.Th>Actions</Table.Th>
                            </Table.Tr>
                          </Table.Thead>
                          <Table.Tbody>
                            {filteredAndSortedDocuments.map((document) => (
                              <Table.Tr key={document.key}>
                                <Table.Td>{document.key}</Table.Td>
                                <Table.Td>
                                  {formatSize(document.sizeBytes)}
                                </Table.Td>
                                <Table.Td>
                                  {formatDate(document.lastModified)}
                                </Table.Td>
                                <Table.Td>
                                  <Group gap="xs" wrap="nowrap">
                                    {isPdfDocument(document.key) ? (
                                      <Button
                                        variant="light"
                                        size="xs"
                                        onClick={() =>
                                          void handlePreviewDocument(
                                            document.key,
                                          )
                                        }
                                        loading={
                                          isPreviewLoading &&
                                          previewDocumentKey === document.key
                                        }
                                        disabled={
                                          uploadBatchInProgress ||
                                          pendingDownloadKey !== null ||
                                          (isPreviewLoading &&
                                            previewDocumentKey !== document.key)
                                        }
                                      >
                                        Preview
                                      </Button>
                                    ) : null}
                                    <Button
                                      variant="default"
                                      size="xs"
                                      onClick={() =>
                                        void handleDownloadDocument(
                                          document.key,
                                        )
                                      }
                                      loading={
                                        pendingDownloadKey === document.key
                                      }
                                      disabled={
                                        (pendingDownloadKey !== null &&
                                          pendingDownloadKey !==
                                            document.key) ||
                                        uploadBatchInProgress
                                      }
                                    >
                                      Download
                                    </Button>
                                    <Button
                                      color="red"
                                      variant="light"
                                      size="xs"
                                      onClick={() =>
                                        openDeleteModal(document.key)
                                      }
                                      disabled={
                                        isDeleting ||
                                        uploadBatchInProgress ||
                                        pendingDownloadKey !== null
                                      }
                                    >
                                      Delete
                                    </Button>
                                  </Group>
                                </Table.Td>
                              </Table.Tr>
                            ))}
                          </Table.Tbody>
                        </Table>
                      </Stack>
                    )}
                  </Stack>
                </Paper>
              </Stack>
            </Tabs.Panel>

            <Tabs.Panel value="unsupported-queries" pt="lg">
              <Paper className="chat-card" p="lg" radius="lg" shadow="md">
                <Stack gap="md">
                  <Group justify="space-between" align="center">
                    <Stack gap={2}>
                      <Text fw={600}>Unsupported chatbot queries</Text>
                      <Text size="sm" c={SECONDARY_TEXT_COLOR}>
                        Review questions the chatbot could not answer from the
                        knowledge base.
                      </Text>
                    </Stack>
                    <Button
                      variant="default"
                      onClick={handleUnsupportedQueriesRefresh}
                      loading={isUnsupportedQueriesLoading}
                    >
                      Refresh
                    </Button>
                  </Group>

                  {unsupportedQueriesError ? (
                    <Alert
                      color="red"
                      variant="light"
                      title="Could not load unsupported queries"
                      withCloseButton
                      closeButtonLabel="Dismiss unsupported query load error"
                      onClose={() => setUnsupportedQueriesError(null)}
                    >
                      {unsupportedQueriesError}
                    </Alert>
                  ) : null}

                  {unsupportedDeleteError ? (
                    <Alert
                      color="red"
                      variant="light"
                      title="Delete error"
                      withCloseButton
                      closeButtonLabel="Dismiss unsupported query delete error"
                      onClose={() => setUnsupportedDeleteError(null)}
                    >
                      {unsupportedDeleteError}
                    </Alert>
                  ) : null}

                  {isUnsupportedQueriesLoading ? (
                    <Text c={SECONDARY_TEXT_COLOR}>
                      Loading unsupported queries...
                    </Text>
                  ) : unsupportedQueries.length === 0 ? (
                    <Text c={SECONDARY_TEXT_COLOR}>
                      No unsupported queries found.
                    </Text>
                  ) : (
                    <Stack gap="sm">
                      <Group justify="space-between" align="center">
                        <Text size="sm" c={SECONDARY_TEXT_COLOR}>
                          Page {unsupportedQueriesPageNumber} -{" "}
                          {unsupportedQueries.length} quer
                          {unsupportedQueries.length === 1 ? "y" : "ies"}
                        </Text>
                        <Group gap="xs">
                          <Button
                            variant="default"
                            size="xs"
                            onClick={handleUnsupportedQueriesPreviousPage}
                            disabled={
                              !hasPreviousUnsupportedQueriesPage ||
                              isUnsupportedQueriesLoading ||
                              pendingUnsupportedDeleteId !== null
                            }
                          >
                            Previous
                          </Button>
                          <Button
                            variant="default"
                            size="xs"
                            onClick={handleUnsupportedQueriesNextPage}
                            disabled={
                              !hasNextUnsupportedQueriesPage ||
                              isUnsupportedQueriesLoading ||
                              pendingUnsupportedDeleteId !== null
                            }
                          >
                            Next
                          </Button>
                        </Group>
                      </Group>

                      {unsupportedQueries.length === 0 ? (
                        <Text c={SECONDARY_TEXT_COLOR}>
                          {unsupportedQueriesPageIndex === 0
                            ? "No unsupported queries found."
                            : "No unsupported queries on this page."}
                        </Text>
                      ) : (
                        <Table
                          striped
                          highlightOnHover
                          withTableBorder
                          style={{ tableLayout: "fixed" }}
                        >
                          <Table.Thead>
                            <Table.Tr>
                              <Table.Th style={{ width: "68%" }}>
                                Query
                              </Table.Th>
                              <Table.Th style={{ width: "16%" }}>
                                <UnstyledButton
                                  onClick={toggleUnsupportedQuerySortDirection}
                                  style={{
                                    color: "inherit",
                                    font: "inherit",
                                    fontWeight: "inherit",
                                    display: "inline-flex",
                                    alignItems: "center",
                                    gap: 4,
                                    cursor: "pointer",
                                  }}
                                >
                                  Date{" "}
                                  {unsupportedQuerySortDirection === "latest"
                                    ? "▼"
                                    : "▲"}
                                </UnstyledButton>
                              </Table.Th>
                              <Table.Th style={{ width: "16%" }}>
                                Action
                              </Table.Th>
                            </Table.Tr>
                          </Table.Thead>
                          <Table.Tbody>
                            {unsupportedQueries.map((query) => {
                              const isExpanded =
                                expandedUnsupportedQueryIds[query.id] ?? false;
                              const isLongQuery =
                                query.queryText.length >
                                UNSUPPORTED_QUERY_PREVIEW_LENGTH;
                              const visibleQueryText =
                                isExpanded || !isLongQuery
                                  ? query.queryText
                                  : `${query.queryText.slice(0, UNSUPPORTED_QUERY_PREVIEW_LENGTH).trimEnd()}...`;

                              return (
                                <Table.Tr
                                  key={`${query.id}-${query.timestamp ?? "no-timestamp"}`}
                                >
                                  <Table.Td>
                                    <Stack gap={6}>
                                      <Text
                                        size="sm"
                                        style={{ whiteSpace: "normal" }}
                                      >
                                        {visibleQueryText}
                                      </Text>
                                      {isLongQuery ? (
                                        <Button
                                          variant="subtle"
                                          size="compact-xs"
                                          w="fit-content"
                                          onClick={() =>
                                            toggleUnsupportedQueryExpanded(
                                              query.id,
                                            )
                                          }
                                        >
                                          Show {isExpanded ? "less" : "more"}
                                        </Button>
                                      ) : null}
                                    </Stack>
                                  </Table.Td>
                                  <Table.Td style={{ width: "16%" }}>
                                    {formatDate(query.timestamp)}
                                  </Table.Td>
                                  <Table.Td>
                                    <Button
                                      color="red"
                                      variant="light"
                                      size="xs"
                                      onClick={() =>
                                        void handleUnsupportedDelete(query)
                                      }
                                      loading={
                                        pendingUnsupportedDeleteId === query.id
                                      }
                                      disabled={
                                        pendingUnsupportedDeleteId !== null
                                      }
                                    >
                                      Delete
                                    </Button>
                                  </Table.Td>
                                </Table.Tr>
                              );
                            })}
                          </Table.Tbody>
                        </Table>
                      )}
                    </Stack>
                  )}
                </Stack>
              </Paper>
            </Tabs.Panel>
          </Tabs>
        </Stack>
      </Container>

      <Modal
        opened={Boolean(previewDocumentUrl && previewDocumentKey)}
        onClose={closePreviewModal}
        title={
          previewDocumentKey ? `Preview: ${previewDocumentKey}` : "PDF Preview"
        }
        size="90%"
        centered
      >
        <Stack gap="md">
          {previewDocumentUrl ? (
            <object
              data={previewDocumentUrl}
              type="application/pdf"
              width="100%"
              height="720"
            >
              <Text size="sm">
                This browser could not display the PDF preview.
              </Text>
            </object>
          ) : (
            <Text size="sm" c={SECONDARY_TEXT_COLOR}>
              Loading PDF preview...
            </Text>
          )}
        </Stack>
      </Modal>

      <Modal
        opened={isDeleteModalOpen}
        onClose={() => {
          if (!isDeleting) setIsDeleteModalOpen(false);
        }}
        title="Confirm delete"
        centered
      >
        <Stack gap="md">
          <Text size="sm">
            Delete <strong>{pendingDeleteKey}</strong>? This action cannot be
            undone.
          </Text>
          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={() => setIsDeleteModalOpen(false)}
              disabled={isDeleting}
            >
              Cancel
            </Button>
            <Button
              color="red"
              onClick={() => void confirmDelete()}
              loading={isDeleting}
            >
              Delete
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Box>
  );
}
