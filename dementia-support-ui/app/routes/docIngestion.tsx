import type { Route } from "./+types/docIngestion";
import {
  Alert,
  Badge,
  Box,
  Button,
  Container,
  Group,
  Modal,
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
  type UploadScreeningSummary,
  uploadDocumentAnyway,
  type UploadPhiGroup,
  type UploadRejectedResponse,
  uploadDocument,
} from "~/api/documentsClient";
import {
  deleteUnsupportedQuery,
  listUnsupportedQueries,
  type UnsupportedQuery,
} from "~/api/unsupportedQueriesClient";

const MAX_UPLOAD_SIZE_BYTES = 5 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".pdf"];
const SUCCESS_ALERT_TTL_MS = 10000;
const INITIAL_PHI_GROUP_EXAMPLE_COUNT = 5;
const UNSUPPORTED_QUERY_PREVIEW_LENGTH = 160;
const ACCEPTED_MIME_TYPES = new Set([
  "application/pdf",
]);

type SortDirection = "asc" | "desc";
type DocumentSortField = "filename" | "size" | "lastModified";

type UploadDecisionState = {
  fileName: string;
  response: UploadRejectedResponse;
} | null;

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
type UnsupportedQuerySortDirection = "latest" | "oldest";
type RejectedUploadAction = "cancel" | "upload-anyway" | null;

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
        <strong>Protected Health Information (PHI)</strong> and does not appear{" "}
        <strong>relevant</strong> to the dementia knowledge base. Review the
        document before adding it.
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
        <strong>{fileName}</strong> does not appear <strong>relevant</strong> to
        the dementia knowledge base. Review the document before adding it.
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
  const [activeTab, setActiveTab] =
    useState<DocIngestionTab>("document-ingestion");
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filterText, setFilterText] = useState("");
  const [documentSortField, setDocumentSortField] =
    useState<DocumentSortField>("filename");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [sourceUrl, setSourceUrl] = useState("");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<UploadSuccessState>(null);
  const [uploadDecision, setUploadDecision] =
    useState<UploadDecisionState>(null);
  const [visiblePhiGroupCounts, setVisiblePhiGroupCounts] = useState<
    Record<string, number>
  >({});
  const [isUploading, setIsUploading] = useState(false);
  const [resolvingRejectedUploadAction, setResolvingRejectedUploadAction] =
    useState<RejectedUploadAction>(null);
  const [uploadProgress, setUploadProgress] = useState(0);
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
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
    void loadDocuments();
  }, [loadDocuments]);

  const loadUnsupportedQueries = useCallback(async () => {
    setIsUnsupportedQueriesLoading(true);
    setUnsupportedQueriesError(null);
    try {
      const items = await listUnsupportedQueries();
      setUnsupportedQueries(items);
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Failed to load unsupported queries.";
      setUnsupportedQueriesError(message);
    } finally {
      setIsUnsupportedQueriesLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadUnsupportedQueries();
  }, [loadUnsupportedQueries]);

  useEffect(() => {
    if (!isUploading) {
      setUploadProgress(0);
      return;
    }

    const timer = window.setInterval(() => {
      setUploadProgress((current) => (current >= 90 ? current : current + 8));
    }, 140);

    return () => {
      window.clearInterval(timer);
    };
  }, [isUploading]);

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
  }, [uploadDecision]);

  useEffect(() => {
    return () => {
      if (previewDocumentUrl) {
        URL.revokeObjectURL(previewDocumentUrl);
      }
    };
  }, [previewDocumentUrl]);

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

  const filteredPhiGroups = useMemo(() => {
    return (uploadDecision?.response.phiGroups ?? []).map((group) => ({
      key: group.key,
      label: group.label,
      items: group.items,
    })) satisfies PhiGroup[];
  }, [uploadDecision]);

  const acceptedPhiStatus = uploadSuccess?.screeningSummary
    ? getScreeningBadge(getAcceptedPhiStatus(uploadSuccess.screeningSummary))
    : null;
  const acceptedRelevanceStatus = uploadSuccess?.screeningSummary
    ? getScreeningBadge(
        getAcceptedRelevanceStatus(uploadSuccess.screeningSummary),
      )
    : null;
  const rejectedPhiStatus = uploadDecision
    ? getScreeningBadge(getRejectedPhiStatus(uploadDecision.response))
    : null;
  const rejectedRelevanceStatus = uploadDecision
    ? getScreeningBadge(getRejectedRelevanceStatus(uploadDecision.response))
    : null;

  const sortedUnsupportedQueries = useMemo(() => {
    const items = [...unsupportedQueries];
    items.sort((a, b) => {
      const aTime = a.timestamp ? new Date(a.timestamp).getTime() : 0;
      const bTime = b.timestamp ? new Date(b.timestamp).getTime() : 0;
      const diff = bTime - aTime;
      return unsupportedQuerySortDirection === "latest" ? diff : -diff;
    });
    return items;
  }, [unsupportedQueries, unsupportedQuerySortDirection]);

  function handlePickedFile(file: File | null) {
    setUploadError(null);
    setUploadSuccess(null);
    if (!file) {
      setSelectedFile(null);
      return;
    }
    const validationError = validateFile(file);
    if (validationError) {
      setSelectedFile(null);
      setUploadError(validationError);
      return;
    }
    setSelectedFile(file);
  }

  function handleDrop(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
    if (isUploading || uploadDecision) return;
    const file = event.dataTransfer.files?.[0] ?? null;
    handlePickedFile(file);
  }

  function handleDragOver(event: React.DragEvent<HTMLDivElement>) {
    event.preventDefault();
  }

  async function handleUpload() {
    if (!selectedFile || isUploading) return;

    const normalizedSourceUrl = sourceUrl.trim() || undefined;

    const previousDocuments = documents;
    const optimisticItem: DocumentItem = {
      key: selectedFile.name,
      sizeBytes: selectedFile.size,
      lastModified: new Date().toISOString(),
    };

    setUploadError(null);
    setDeleteError(null);
    setUploadSuccess(null);
    setUploadDecision(null);
    setIsUploading(true);
    setDocuments((current) => [
      optimisticItem,
      ...current.filter((document) => document.key !== optimisticItem.key),
    ]);

    try {
      const response = await uploadDocument(selectedFile, {
        sourceUrl: normalizedSourceUrl,
      });
      setUploadProgress(100);

      if (response.status === "accepted") {
        await loadDocuments();
        setUploadSuccess({
          title: "Upload complete",
          message: `Uploaded ${selectedFile.name}`,
          screeningSummary: response.screeningSummary,
        });
        setSelectedFile(null);
        setSourceUrl("");
        if (fileInputRef.current) fileInputRef.current.value = "";
      } else {
        setDocuments(previousDocuments);
        setUploadDecision({
          fileName: selectedFile.name,
          response,
        });
      }
    } catch (error) {
      setDocuments(previousDocuments);
      const message = error instanceof Error ? error.message : "Upload failed.";
      setUploadError(message);
    } finally {
      setIsUploading(false);
    }
  }

  function openDeleteModal(key: string) {
    if (isUploading) return;
    setDeleteError(null);
    setDeleteSuccess(null);
    setPendingDeleteKey(key);
    setIsDeleteModalOpen(true);
  }

  async function handleCancelRejectedUpload() {
    if (
      !uploadDecision?.response.quarantineKey ||
      resolvingRejectedUploadAction !== null
    )
      return;

    setResolvingRejectedUploadAction("cancel");
    setUploadError(null);
    try {
      await cancelDocumentUpload(uploadDecision.response.quarantineKey);
      setUploadDecision(null);
      setSelectedFile(null);
      setSourceUrl("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      setUploadSuccess({
        title: "Upload cancelled",
        message: `Cancelled upload for ${uploadDecision.fileName}`,
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Cancel upload failed.";
      setUploadError(message);
    } finally {
      setResolvingRejectedUploadAction(null);
    }
  }

  async function handleUploadAnyway() {
    if (
      !uploadDecision?.response.uploadId ||
      !uploadDecision.response.quarantineKey ||
      resolvingRejectedUploadAction !== null
    ) {
      return;
    }

    setResolvingRejectedUploadAction("upload-anyway");
    setUploadError(null);
    try {
      const normalizedSourceUrl = sourceUrl.trim() || undefined;
      await uploadDocumentAnyway(
        uploadDecision.response.uploadId,
        uploadDecision.response.quarantineKey,
        { sourceUrl: normalizedSourceUrl },
      );
      setUploadDecision(null);
      setSelectedFile(null);
      setSourceUrl("");
      if (fileInputRef.current) fileInputRef.current.value = "";
      await loadDocuments();
      setUploadSuccess({
        title: "Upload complete",
        message: `Uploaded ${uploadDecision.fileName}`,
      });
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Upload anyway failed.";
      setUploadError(message);
    } finally {
      setResolvingRejectedUploadAction(null);
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
      setUnsupportedQueries((current) =>
        current.filter((item) => item.id !== query.id),
      );
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

  return (
    <Box className="page">
      <Container size="xl" py={48}>
        <Stack gap="xl">
          <Group justify="space-between" align="flex-end">
            <Stack gap={6}>
              <Text size="sm" c="dimmed">
                Knowledge Base Management
              </Text>
              <Title order={1}>
                {activeTab === "unsupported-queries"
                  ? "Review unsupported queries"
                  : "Upload and manage knowledge base documents"}
              </Title>
            </Stack>
            <Button component={Link} to="/" variant="light">
              Back to Chat
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
                        opacity: uploadDecision ? 0.6 : 1,
                        pointerEvents: uploadDecision ? "none" : undefined,
                      }}
                    >
                      <Stack gap="sm" align="center">
                        <Text fw={600}>Drag and drop a file here</Text>
                        <Text size="sm" c="dimmed">
                          Supported: {ACCEPTED_EXTENSIONS.join(", ")} (max 5
                          MB)
                        </Text>
                        <input
                          ref={fileInputRef}
                          type="file"
                          accept={ACCEPTED_EXTENSIONS.join(",")}
                          className="hidden-file-input"
                          onChange={(event) =>
                            handlePickedFile(
                              event.currentTarget.files?.[0] ?? null,
                            )
                          }
                          disabled={isUploading || Boolean(uploadDecision)}
                        />
                        <Button
                          variant="default"
                          onClick={() => fileInputRef.current?.click()}
                          disabled={isUploading || Boolean(uploadDecision)}
                        >
                          Choose File
                        </Button>
                      </Stack>
                    </Paper>

                    {selectedFile ? (
                      <Paper withBorder radius="md" p="sm">
                        <Stack gap="sm">
                          <Group justify="space-between" align="center">
                            <div>
                              <Text fw={600}>{selectedFile.name}</Text>
                              <Text size="sm" c="dimmed">
                                {formatSize(selectedFile.size)}
                              </Text>
                            </div>
                            <Button
                              onClick={handleUpload}
                              loading={isUploading}
                              disabled={isUploading || Boolean(uploadDecision)}
                            >
                              Upload
                            </Button>
                          </Group>
                          <TextInput
                            label="Source URL (optional)"
                            placeholder="https://example.com/original-document.pdf"
                            value={sourceUrl}
                            onChange={(event) =>
                              setSourceUrl(event.currentTarget.value)
                            }
                            disabled={isUploading || Boolean(uploadDecision)}
                          />
                        </Stack>
                      </Paper>
                    ) : null}

                    {isUploading ? (
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
                        <Stack gap={4}>
                          <Text size="sm">{uploadSuccess.message}</Text>
                          {uploadSuccess.screeningSummary ? (
                            <>
                              <Stack gap={2}>
                                <Group gap="xs" align="center">
                                  <Text size="sm" fw={600}>
                                    PHI screening
                                  </Text>
                                  {acceptedPhiStatus ? (
                                    <Badge
                                      color={acceptedPhiStatus.color}
                                      variant="light"
                                    >
                                      {acceptedPhiStatus.label}
                                    </Badge>
                                  ) : null}
                                </Group>
                                {acceptedPhiStatus ? (
                                  <Text size="sm" c="dimmed">
                                    {acceptedPhiStatus.detail}
                                  </Text>
                                ) : null}
                              </Stack>
                              <Stack gap={2}>
                                <Group gap="xs" align="center">
                                  <Text size="sm" fw={600}>
                                    Relevance assessment
                                  </Text>
                                  {acceptedRelevanceStatus ? (
                                    <Badge
                                      color={acceptedRelevanceStatus.color}
                                      variant="light"
                                    >
                                      {acceptedRelevanceStatus.label}
                                    </Badge>
                                  ) : null}
                                </Group>
                                {acceptedRelevanceStatus ? (
                                  <Text size="sm" c="dimmed">
                                    {acceptedRelevanceStatus.detail}
                                  </Text>
                                ) : null}
                              </Stack>
                            </>
                          ) : null}
                        </Stack>
                      </Alert>
                    ) : null}
                    {uploadDecision ? (
                      <Alert
                        color="yellow"
                        variant="light"
                        title="Document review required"
                      >
                        <Stack gap="sm">
                          <Stack gap={4}>
                            <Stack gap={2}>
                              <Group gap="xs" align="center">
                                <Text size="sm" fw={600}>
                                  PHI screening
                                </Text>
                                {rejectedPhiStatus ? (
                                  <Badge
                                    color={rejectedPhiStatus.color}
                                    variant="light"
                                  >
                                    {rejectedPhiStatus.label}
                                  </Badge>
                                ) : null}
                              </Group>
                              {rejectedPhiStatus ? (
                                <Text size="sm" c="dimmed">
                                  {rejectedPhiStatus.detail}
                                </Text>
                              ) : null}
                            </Stack>
                            <Stack gap={2}>
                              <Group gap="xs" align="center">
                                <Text size="sm" fw={600}>
                                  Relevance assessment
                                </Text>
                                {rejectedRelevanceStatus ? (
                                  <Badge
                                    color={rejectedRelevanceStatus.color}
                                    variant="light"
                                  >
                                    {rejectedRelevanceStatus.label}
                                  </Badge>
                                ) : null}
                              </Group>
                              {rejectedRelevanceStatus ? (
                                <Text size="sm" c="dimmed">
                                  {rejectedRelevanceStatus.detail}
                                </Text>
                              ) : null}
                            </Stack>
                          </Stack>
                          <Text size="sm">
                            {formatRejectedUploadSummary(
                              uploadDecision.response,
                              uploadDecision.fileName,
                            )}
                          </Text>
                          {uploadDecision.response.screeningSummary
                            ?.isRelevant === false ||
                          uploadDecision.response.reason === "not_relevant" ||
                          uploadDecision.response.reason ===
                            "possible_phi_detected_and_not_relevant" ? (
                            <Paper withBorder radius="md" p="sm">
                              <Stack gap={4}>
                                <Text size="sm" fw={600}>
                                  Relevance screening
                                </Text>
                                <Text size="sm">
                                  Result: <strong>Not relevant</strong>
                                </Text>
                                {uploadDecision.response.screeningSummary
                                  ?.relevanceReason ? (
                                  <Text size="sm">
                                    Reason:{" "}
                                    {
                                      uploadDecision.response.screeningSummary
                                        .relevanceReason
                                    }
                                  </Text>
                                ) : null}
                              </Stack>
                            </Paper>
                          ) : null}
                          {uploadDecision.response.screeningSummary
                            ?.phiDetected ||
                          uploadDecision.response.reason ===
                            "possible_phi_detected" ||
                          uploadDecision.response.reason ===
                            "possible_phi_detected_and_not_relevant" ? (
                            <Paper withBorder radius="md" p="sm">
                              <Stack gap={6}>
                                <Text size="sm" fw={600}>
                                  PHI Screening: Detected Categories
                                </Text>
                                {filteredPhiGroups.length ? (
                                  filteredPhiGroups.map((group) => {
                                    const visibleCount =
                                      visiblePhiGroupCounts[group.key] ??
                                      INITIAL_PHI_GROUP_EXAMPLE_COUNT;
                                    const visibleItems = group.items.slice(
                                      0,
                                      visibleCount,
                                    );
                                    const remainingCount = Math.max(
                                      group.items.length - visibleItems.length,
                                      0,
                                    );

                                    return (
                                      <Stack key={group.key} gap={4}>
                                        <Text size="sm" fw={600}>
                                          {group.label} ({group.items.length})
                                        </Text>
                                        {visibleItems.map((entity, index) => (
                                          <Text
                                            key={`${group.key}-${entity.text}-${index}`}
                                            size="sm"
                                            pl="md"
                                          >
                                            - {entity.text || "Unknown"} (
                                            {formatPhiScore(entity.score)})
                                          </Text>
                                        ))}
                                        {remainingCount > 0 ? (
                                          <Group gap="xs">
                                            <Button
                                              size="xs"
                                              variant="default"
                                              onClick={() =>
                                                setVisiblePhiGroupCounts(
                                                  (current) => ({
                                                    ...current,
                                                    [group.key]: Math.min(
                                                      visibleCount +
                                                        INITIAL_PHI_GROUP_EXAMPLE_COUNT,
                                                      group.items.length,
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
                                                    [group.key]:
                                                      group.items.length,
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
                                  <Text size="sm" c="dimmed">
                                    No PHI entries above 80% confidence were
                                    found to display.
                                  </Text>
                                )}
                              </Stack>
                            </Paper>
                          ) : null}
                          <Group gap="sm">
                            <Button
                              variant="default"
                              onClick={() => void handleCancelRejectedUpload()}
                              loading={
                                resolvingRejectedUploadAction === "cancel"
                              }
                              disabled={
                                resolvingRejectedUploadAction !== null
                              }
                            >
                              Cancel
                            </Button>
                            <Button
                              color="yellow"
                              onClick={() => void handleUploadAnyway()}
                              loading={
                                resolvingRejectedUploadAction ===
                                "upload-anyway"
                              }
                              disabled={
                                resolvingRejectedUploadAction !== null
                              }
                            >
                              Upload anyway
                            </Button>
                          </Group>
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
                      <Text c="dimmed">Loading documents...</Text>
                    ) : filteredAndSortedDocuments.length === 0 ? (
                      <Text c="dimmed">
                        No documents found for this filter.
                      </Text>
                    ) : (
                      <Stack gap="sm">
                        <Group justify="space-between" align="center">
                          <Text size="sm" c="dimmed">
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
                                          void handlePreviewDocument(document.key)
                                        }
                                        loading={
                                          isPreviewLoading &&
                                          previewDocumentKey === document.key
                                        }
                                        disabled={
                                          isUploading ||
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
                                        void handleDownloadDocument(document.key)
                                      }
                                      loading={
                                        pendingDownloadKey === document.key
                                      }
                                      disabled={
                                        (pendingDownloadKey !== null &&
                                          pendingDownloadKey !== document.key) ||
                                        isUploading
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
                                        isUploading ||
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
                      <Text size="sm" c="dimmed">
                        Review questions the chatbot could not answer from the
                        knowledge base.
                      </Text>
                    </Stack>
                    <Button
                      variant="default"
                      onClick={() => void loadUnsupportedQueries()}
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
                    <Text c="dimmed">Loading unsupported queries...</Text>
                  ) : unsupportedQueries.length === 0 ? (
                    <Text c="dimmed">No unsupported queries found.</Text>
                  ) : (
                    <Stack gap="sm">
                      <Group justify="space-between" align="center">
                        <Text size="sm" c="dimmed">
                          {unsupportedQueries.length} quer
                          {unsupportedQueries.length === 1 ? "y" : "ies"}
                        </Text>
                      </Group>
                      <Table
                        striped
                        highlightOnHover
                        withTableBorder
                        style={{ tableLayout: "fixed" }}
                      >
                        <Table.Thead>
                          <Table.Tr>
                            <Table.Th style={{ width: "68%" }}>Query</Table.Th>
                            <Table.Th style={{ width: "16%" }}>
                              <UnstyledButton
                                onClick={() =>
                                  setUnsupportedQuerySortDirection((current) =>
                                    current === "latest" ? "oldest" : "latest",
                                  )
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
                                Date{" "}
                                {unsupportedQuerySortDirection === "latest"
                                  ? "▼"
                                  : "▲"}
                              </UnstyledButton>
                            </Table.Th>
                            <Table.Th style={{ width: "16%" }}>Action</Table.Th>
                          </Table.Tr>
                        </Table.Thead>
                        <Table.Tbody>
                          {sortedUnsupportedQueries.map((query) => {
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
        title={previewDocumentKey ? `Preview: ${previewDocumentKey}` : "PDF Preview"}
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
            <Text size="sm" c="dimmed">
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
