import type { Route } from "./+types/docIngestion";
import {
  Alert,
  Badge,
  Box,
  Button,
  Container,
  Group,
  Modal,
  Paper,
  Progress,
  Stack,
  Table,
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
  listDocuments,
  type UploadPhiEntity,
  uploadDocumentAnyway,
  type UploadRejectedResponse,
  uploadDocument,
} from "~/api/documentsClient";

const MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".pdf", ".txt", ".doc", ".docx", ".md"];
const SUCCESS_ALERT_TTL_MS = 4500;
const INITIAL_PHI_ENTITY_COUNT = 10;
const ACCEPTED_MIME_TYPES = new Set([
  "application/pdf",
  "text/plain",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/markdown",
  "text/x-markdown",
]);

type SortDirection = "asc" | "desc";

type UploadDecisionState = {
  fileName: string;
  response: UploadRejectedResponse;
} | null;

type UploadSuccessState = {
  title: string;
  message: string;
} | null;

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Document Manager" },
    {
      name: "description",
      content: "Manage RAG knowledge documents with mock S3 upload and delete actions.",
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
  if (!ACCEPTED_EXTENSIONS.includes(extension) && !ACCEPTED_MIME_TYPES.has(file.type)) {
    return "Unsupported file type. Allowed: pdf, txt, doc, docx, md.";
  }
  if (file.size > MAX_UPLOAD_SIZE_BYTES) {
    return "File is too large. Maximum size is 25 MB.";
  }
  return null;
}

function formatPhiScore(score?: number) {
  if (typeof score !== "number") return "Unknown";
  return `${(score * 100).toFixed(1)}%`;
}

function getPhiEntityLabel(entity: UploadPhiEntity) {
  const category =
    entity.category && entity.category !== "PROTECTED_HEALTH_INFORMATION"
      ? entity.category
      : undefined;
  const parts = [entity.type, category].filter(Boolean);
  return parts.length > 0 ? parts.join(" • ") : "Unknown PHI entity";
}

export default function DocIngestion() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filterText, setFilterText] = useState("");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<UploadSuccessState>(null);
  const [uploadDecision, setUploadDecision] = useState<UploadDecisionState>(null);
  const [visiblePhiEntityCount, setVisiblePhiEntityCount] = useState(INITIAL_PHI_ENTITY_COUNT);
  const [isUploading, setIsUploading] = useState(false);
  const [isResolvingRejectedUpload, setIsResolvingRejectedUpload] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [pendingDeleteKey, setPendingDeleteKey] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteSuccess, setDeleteSuccess] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  function getRejectedUploadMessage(reason: string, fileName: string) {
    switch (reason) {
      case "unable_to_extract_text":
        return `No readable text could be extracted from ${fileName}. Review the document before adding it to the dementia knowledge base.`;
      case "possible_phi_detected":
        return `${fileName} may contain protected health information. Review it carefully before adding it to the dementia knowledge base.`;
      case "not_relevant":
        return `${fileName} does not appear relevant to the dementia knowledge base.`;
      default:
        return `${fileName} requires manual review before it can be added to the dementia knowledge base.`;
    }
  }

  const loadDocuments = useCallback(async () => {
    setIsLoading(true);
    setLoadError(null);
    try {
      const items = await listDocuments();
      setDocuments(items);
    } catch (error) {
      const message = error instanceof Error ? error.message : "Failed to load documents.";
      setLoadError(message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

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
    setVisiblePhiEntityCount(INITIAL_PHI_ENTITY_COUNT);
  }, [uploadDecision]);

  const filteredAndSortedDocuments = useMemo(() => {
    const filtered = documents.filter((document) =>
      document.key.toLowerCase().includes(filterText.toLowerCase()),
    );
    return filtered.sort((a, b) => {
      const result = a.key.localeCompare(b.key);
      return sortDirection === "asc" ? result : -result;
    });
  }, [documents, filterText, sortDirection]);

  const visiblePhiEntities = useMemo(() => {
    const entities = uploadDecision?.response.entities ?? [];
    return entities.slice(0, visiblePhiEntityCount);
  }, [uploadDecision, visiblePhiEntityCount]);

  const remainingPhiEntityCount = useMemo(() => {
    const total = uploadDecision?.response.entities?.length ?? 0;
    return Math.max(total - visiblePhiEntities.length, 0);
  }, [uploadDecision, visiblePhiEntities.length]);

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
      const response = await uploadDocument(selectedFile);
      setUploadProgress(100);

      if (response.status === "accepted") {
        await loadDocuments();
        setUploadSuccess({
          title: "Upload complete",
          message: `Uploaded ${selectedFile.name}`,
        });
        setSelectedFile(null);
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
    if (!uploadDecision?.response.quarantineKey || isResolvingRejectedUpload) return;

    setIsResolvingRejectedUpload(true);
    setUploadError(null);
    try {
      await cancelDocumentUpload(uploadDecision.response.quarantineKey);
      setUploadDecision(null);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      setUploadSuccess({
        title: "Upload cancelled",
        message: `Cancelled upload for ${uploadDecision.fileName}`,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Cancel upload failed.";
      setUploadError(message);
    } finally {
      setIsResolvingRejectedUpload(false);
    }
  }

  async function handleUploadAnyway() {
    if (
      !uploadDecision?.response.uploadId ||
      !uploadDecision.response.quarantineKey ||
      isResolvingRejectedUpload
    ) {
      return;
    }

    setIsResolvingRejectedUpload(true);
    setUploadError(null);
    try {
      await uploadDocumentAnyway(
        uploadDecision.response.uploadId,
        uploadDecision.response.quarantineKey,
      );
      setUploadDecision(null);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      await loadDocuments();
      setUploadSuccess({
        title: "Upload complete",
        message: `Uploaded ${uploadDecision.fileName}`,
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : "Upload anyway failed.";
      setUploadError(message);
    } finally {
      setIsResolvingRejectedUpload(false);
    }
  }

  async function confirmDelete() {
    if (!pendingDeleteKey || isDeleting) return;

    setIsDeleting(true);
    setDeleteError(null);
    setDeleteSuccess(null);
    try {
      await deleteDocument(pendingDeleteKey);
      setDocuments((current) => current.filter((document) => document.key !== pendingDeleteKey));
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

  return (
    <Box className="page">
      <Container size="xl" py={48}>
        <Stack gap="xl">
          <Group justify="space-between" align="flex-end">
            <Stack gap={6}>
              <Text size="sm" c="dimmed">
                AWS RAG Document Manager
              </Text>
              <Title order={1}>Upload and manage ingestion documents</Title>
            </Stack>
            <Button component={Link} to="/" variant="light">
              Back to Chat
            </Button>
          </Group>

          <Paper className="chat-card" p="lg" radius="lg" shadow="md">
            <Stack gap="md">
              <Group justify="space-between">
                <Text fw={600}>Upload Document</Text>
                <Badge variant="light" color="teal">
                  Mock S3
                </Badge>
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
                    Supported: {ACCEPTED_EXTENSIONS.join(", ")} (max 25 MB)
                  </Text>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept={ACCEPTED_EXTENSIONS.join(",")}
                    className="hidden-file-input"
                    onChange={(event) => handlePickedFile(event.currentTarget.files?.[0] ?? null)}
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
                </Paper>
              ) : null}

              {isUploading ? <Progress value={uploadProgress} animated size="lg" radius="xl" /> : null}

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
              {uploadDecision ? (
                <Alert color="yellow" variant="light" title="Document review required">
                  <Stack gap="sm">
                    <Text size="sm">
                      {getRejectedUploadMessage(
                        uploadDecision.response.reason,
                        uploadDecision.fileName,
                      )}
                    </Text>
                    {uploadDecision.response.reason === "possible_phi_detected" &&
                    uploadDecision.response.entities?.length ? (
                      <Paper withBorder radius="md" p="sm">
                        <Stack gap={6}>
                          <Text size="sm" fw={600}>
                            Detected PHI entities ({uploadDecision.response.entities.length})
                          </Text>
                          {visiblePhiEntities.map((entity, index) => (
                            <Text key={`${entity.type}-${entity.text}-${index}`} size="sm">
                              {index + 1}. {getPhiEntityLabel(entity)} | Text:{" "}
                              {entity.text || "Unknown"} | Confidence:{" "}
                              {formatPhiScore(entity.score)}
                            </Text>
                          ))}
                          {uploadDecision.response.entities.length > visiblePhiEntities.length ? (
                            <Group gap="xs">
                              <Button
                                size="xs"
                                variant="default"
                                onClick={() =>
                                  setVisiblePhiEntityCount((current) =>
                                    Math.min(
                                      current + INITIAL_PHI_ENTITY_COUNT,
                                      uploadDecision.response.entities?.length ?? current,
                                    ),
                                  )
                                }
                              >
                                Show {Math.min(INITIAL_PHI_ENTITY_COUNT, remainingPhiEntityCount)} more
                              </Button>
                              <Button
                                size="xs"
                                variant="subtle"
                                onClick={() =>
                                  setVisiblePhiEntityCount(
                                    uploadDecision.response.entities?.length ??
                                      INITIAL_PHI_ENTITY_COUNT,
                                  )
                                }
                              >
                                Show all
                              </Button>
                            </Group>
                          ) : null}
                        </Stack>
                      </Paper>
                    ) : null}
                    <Group gap="sm">
                      <Button
                        variant="default"
                        onClick={() => void handleCancelRejectedUpload()}
                        loading={isResolvingRejectedUpload}
                        disabled={isResolvingRejectedUpload}
                      >
                        Cancel
                      </Button>
                      <Button
                        color="yellow"
                        onClick={() => void handleUploadAnyway()}
                        loading={isResolvingRejectedUpload}
                        disabled={isResolvingRejectedUpload}
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
                <Text fw={600}>S3 Documents</Text>
                <Group gap="xs">
                  <Button variant="default" onClick={() => void loadDocuments()} loading={isLoading}>
                    Refresh
                  </Button>
                  <Button
                    variant="light"
                    onClick={() => setSortDirection((current) => (current === "asc" ? "desc" : "asc"))}
                  >
                    Sort filename {sortDirection === "asc" ? "A-Z" : "Z-A"}
                  </Button>
                </Group>
              </Group>

              <TextInput
                placeholder="Search filename..."
                value={filterText}
                onChange={(event) => setFilterText(event.currentTarget.value)}
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
                <Text c="dimmed">No documents found for this filter.</Text>
              ) : (
                <Table striped highlightOnHover withTableBorder>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>Filename</Table.Th>
                      <Table.Th>Size</Table.Th>
                      <Table.Th>Last Modified</Table.Th>
                      <Table.Th>Action</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {filteredAndSortedDocuments.map((document) => (
                      <Table.Tr key={document.key}>
                        <Table.Td>{document.key}</Table.Td>
                        <Table.Td>{formatSize(document.sizeBytes)}</Table.Td>
                        <Table.Td>{formatDate(document.lastModified)}</Table.Td>
                        <Table.Td>
                          <Button
                            color="red"
                            variant="light"
                            size="xs"
                            onClick={() => openDeleteModal(document.key)}
                            disabled={isDeleting || isUploading}
                          >
                            Delete
                          </Button>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              )}
            </Stack>
          </Paper>
        </Stack>
      </Container>

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
            Delete <strong>{pendingDeleteKey}</strong>? This action cannot be undone.
          </Text>
          <Group justify="flex-end">
            <Button
              variant="default"
              onClick={() => setIsDeleteModalOpen(false)}
              disabled={isDeleting}
            >
              Cancel
            </Button>
            <Button color="red" onClick={() => void confirmDelete()} loading={isDeleting}>
              Delete
            </Button>
          </Group>
        </Stack>
      </Modal>
    </Box>
  );
}
