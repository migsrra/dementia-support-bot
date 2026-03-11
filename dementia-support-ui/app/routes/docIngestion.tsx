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
  type DocumentItem,
  deleteDocument,
  listDocuments,
  uploadDocument,
} from "~/api/documentsClient";

const MAX_UPLOAD_SIZE_BYTES = 25 * 1024 * 1024;
const ACCEPTED_EXTENSIONS = [".pdf", ".txt", ".doc", ".docx", ".md"];
const ACCEPTED_MIME_TYPES = new Set([
  "application/pdf",
  "text/plain",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "text/markdown",
  "text/x-markdown",
]);

type SortDirection = "asc" | "desc";

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

export default function DocIngestion() {
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [filterText, setFilterText] = useState("");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadSuccess, setUploadSuccess] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [isDeleteModalOpen, setIsDeleteModalOpen] = useState(false);
  const [pendingDeleteKey, setPendingDeleteKey] = useState<string | null>(null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deleteSuccess, setDeleteSuccess] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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

  const filteredAndSortedDocuments = useMemo(() => {
    const filtered = documents.filter((document) =>
      document.key.toLowerCase().includes(filterText.toLowerCase()),
    );
    return filtered.sort((a, b) => {
      const result = a.key.localeCompare(b.key);
      return sortDirection === "asc" ? result : -result;
    });
  }, [documents, filterText, sortDirection]);

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
    if (isUploading) return;
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
    setIsUploading(true);
    setDocuments((current) => [
      optimisticItem,
      ...current.filter((document) => document.key !== optimisticItem.key),
    ]);

    try {
      await uploadDocument(selectedFile);
      setUploadProgress(100);
      await loadDocuments();
      setUploadSuccess(`Uploaded ${selectedFile.name}`);
      setSelectedFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
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
                    disabled={isUploading}
                  />
                  <Button
                    variant="default"
                    onClick={() => fileInputRef.current?.click()}
                    disabled={isUploading}
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
                    <Button onClick={handleUpload} loading={isUploading} disabled={isUploading}>
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
                  title="Upload complete"
                  withCloseButton
                  closeButtonLabel="Dismiss upload success"
                  onClose={() => setUploadSuccess(null)}
                >
                  {uploadSuccess}
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
