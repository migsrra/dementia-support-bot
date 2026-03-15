import type { Route } from "./+types/home";
import {
  Anchor,
  Box,
  Button,
  Container,
  Group,
  Paper,
  ScrollArea,
  Stack,
  Text,
  TextInput,
  Title,
} from "@mantine/core";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { invokeAgent } from "~/api/chatbotClient";

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Dementia Support Chat" },
    {
      name: "description",
      content:
        "Supportive, calm conversations powered by a RAG knowledge base.",
    },
  ];
}

type ChatMessage = {
  id: number;
  role: "assistant" | "user";
  text: string;
};

type Conversation = {
  id: number;
  sessionID: string;
  title: string;
  messages: ChatMessage[];
};

const URL_PATTERN = /(https?:\/\/[^\s]+)/g;

function renderMessageWithLinks(text: string) {
  const parts = text.split(URL_PATTERN);
  return parts.map((part, index) => {
    if (/^https?:\/\/\S+$/.test(part)) {
      return (
        <Anchor
          key={`${part}-${index}`}
          href={part}
          target="_blank"
          rel="noreferrer"
        >
          {part}
        </Anchor>
      );
    }
    return part;
  });
}

function createSessionId() {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  const randomPart = Math.random().toString(36).slice(2, 10);
  return `${Date.now()}-${randomPart}`;
}

// Initial data
const seedMessages: ChatMessage[] = [
  {
    id: 1,
    role: "assistant",
    text: "How can I help you today?",
  },
];

let nextMessageId = 2;
let nextConversationId = 2;

// Backend call function
function formatCitationsInline(citations: unknown[]): string {
  if (!citations?.length) return "";

  const allLinks: string[] = [];

  citations.forEach((c) => {
    const cit = c as any;
    const refs = Array.isArray(cit?.retrievedReferences)
      ? cit.retrievedReferences
      : [];

    refs.forEach((ref: any) => {
      const sourceUrl = ref?.metadata?.source_url ?? null;
      const uri =
        ref?.location?.webLocation?.url ??
        ref?.location?.s3Location?.uri ??
        ref?.location?.uri ??
        ref?.location?.path ??
        null;

      const preferredLink = sourceUrl ?? uri;
      if (preferredLink) allLinks.push(String(preferredLink));
    });
  });

  // dedupe while keeping order
  const uniq = allLinks.filter((u, idx) => allLinks.indexOf(u) === idx);
  const pretty = uniq.map((u, i) => `[${i + 1}] ${u}`);

  return `\n\nSources:\n${pretty.join("\n")}`;
}

type AssistantReply = { text: string; citations: unknown[] };

async function getAssistantReply(
  prompt: string,
  sessionID: string,
  signal: AbortSignal,
): Promise<AssistantReply> {
  const trimmed = prompt.trim();
  if (!trimmed) {
    return { text: "Tell me a little more, and I will help.", citations: [] };
  }

  try {
    const data = await invokeAgent(sessionID, trimmed, signal);

    const citations = data.attribution?.citations ?? [];

    const baseText =
      data.response || "Sorry, I couldn't get an answer right now.";

    const text = baseText + formatCitationsInline(citations);
    return { text, citations };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    console.error("invokeAgent error:", error);
    return {
      text: "Sorry, I couldn't reach the assistant right now.",
      citations: [],
    };
  }
}

/*
   OLD getAssistantReply (REFERENCE ONLY)
   Kept from Miguel’s working version before we embedded sources in output.

async function getAssistantReply(
  prompt: string,
  sessionID: string,
  signal: AbortSignal,
) {
  // removing whitespace and returning early if empty
  const trimmed = prompt.trim();
  if (!trimmed) {
    return { text: "Tell me a little more, and I will help." };
  }

  try {
    const data = await invokeAgent(sessionID, trimmed, signal);

    // error check
    if (!data?.response) {
      return { text: "Sorry, I couldn't get an answer right now." };
    }

    // return chatbot response
    return { text: data.response };
  } catch (error) {
    // catches exceptions like failures and aborts
    // timeout exception
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error; // UI will handle cancellation
    }
    console.error("getAssistantReply error:", error);
    // generic exception handle
    return { text: "Sorry, I couldn't reach the assistant right now." };
  }
}
*/

// frontend, updates UI automatically when states change
export default function Home() {
  // states
  const [conversations, setConversations] = useState<Conversation[]>([
    {
      id: 1,
      sessionID: createSessionId(),
      title: "New Conversation",
      messages: seedMessages,
    },
  ]);
  const [activeConversationId, setActiveConversationId] = useState(1);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const scrollViewportRef = useRef<HTMLDivElement | null>(null);
  const [controller, setController] = useState<AbortController | null>(null);

  const activeConversation =
    conversations.find(
      (conversation) => conversation.id === activeConversationId,
    ) ?? conversations[0];

  // run after render if something changed
  useEffect(() => {
    const viewport = scrollViewportRef.current;
    if (!viewport || !activeConversation) {
      return;
    }
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
  }, [activeConversationId, activeConversation?.messages.length]);

  // new conversation handler
  function handleNewConversation() {
    const newConversation: Conversation = {
      id: nextConversationId++,
      sessionID: createSessionId(),
      title: `New conversation ${nextConversationId - 1}`,
      messages: [
        {
          id: nextMessageId++,
          role: "assistant",
          text: "How can I help you today?",
        },
      ],
    };
    // update states
    setConversations((current) => [newConversation, ...current]);
    setActiveConversationId(newConversation.id);
    setDraft("");
  }

  // send message handler
  async function handleSend() {
    const trimmed = draft.trim();
    if (!trimmed || isSending || !activeConversation) {
      return;
    }

    // set message states
    const newController = new AbortController();
    setController(newController);
    setIsSending(true);
    const userMessage: ChatMessage = {
      id: nextMessageId++,
      role: "user",
      text: trimmed,
    };

    setConversations((current) =>
      current.map((conversation) => {
        if (conversation.id !== activeConversation.id) return conversation;

        const isFirstUserMessage =
          conversation.messages.filter((m) => m.role === "user").length === 0;

        return {
          ...conversation,
          messages: [...conversation.messages, userMessage],
          title: isFirstUserMessage ? trimmed : conversation.title,
        };
      }),
    );

    setDraft("");

    // send message to backend, return with chatbot response
    try {
      const reply = await getAssistantReply(
        trimmed,
        activeConversation.sessionID,
        newController.signal,
      );
      const assistantMessage: ChatMessage = {
        id: nextMessageId++,
        role: "assistant",
        text: reply.text,
      };

      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === activeConversation.id
            ? {
                ...conversation,
                messages: [...conversation.messages, assistantMessage],
              }
            : conversation,
        ),
      );
      // setIsSending(false);
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") {
        const cancelledMessage: ChatMessage = {
          id: nextMessageId++,
          role: "assistant",
          text: "Response cancelled.",
        };

        setConversations((current) =>
          current.map((conversation) =>
            conversation.id === activeConversation.id
              ? {
                  ...conversation,
                  messages: [...conversation.messages, cancelledMessage],
                }
              : conversation,
          ),
        );
      }
    } finally {
      setIsSending(false);
      setController(null);
    }
  }

  function handleCancel() {
    if (controller) {
      controller.abort();
    }
  }

  // UI layout
  return (
    <Box className="page">
      <Container size="xl" py={48}>
        <div className="chat-layout">
          <aside className="sidebar">
            <Group justify="space-between" align="center" mb="md">
              <Text fw={600}>Conversations</Text>
              <Button size="xs" variant="light" onClick={handleNewConversation}>
                New
              </Button>
            </Group>
            <Stack gap="xs">
              {conversations.map((conversation) => (
                <Button
                  key={conversation.id}
                  variant={
                    conversation.id === activeConversationId
                      ? "filled"
                      : "subtle"
                  }
                  color="teal"
                  justify="flex-start"
                  className="conversation-button"
                  onClick={() => setActiveConversationId(conversation.id)}
                >
                  <Text truncate>{conversation.title}</Text>
                </Button>
              ))}
            </Stack>
          </aside>

          <Stack gap="xl" className="chat-main">
            <Group justify="space-between" align="flex-end">
              <Stack gap={6}>
                <Text size="sm" c="dimmed">
                  Dementia Support Assistant
                </Text>
                <Title order={1}>Calm, guided chat for daily care</Title>
              </Stack>
              <Button component={Link} to="/docIngestion" variant="light">
                Manage Documents
              </Button>
            </Group>
            <Paper
              className="chat-card"
              p="lg"
              radius="lg"
              shadow="md"
              style={{
                display: "flex",
                flexDirection: "column",
                height: "70vh",
              }}
            >
              <ScrollArea
                type="scroll"
                offsetScrollbars
                viewportRef={scrollViewportRef}
                style={{ flex: 1, minHeight: 0 }}
              >
                <Stack gap="sm" className="messages">
                  {activeConversation?.messages.map((message) => (
                    <Paper
                      key={message.id}
                      className="message"
                      data-role={message.role}
                      p="md"
                      radius="lg"
                    >
                      <Text size="sm" fw={600} className="message-label">
                        {message.role === "assistant" ? "Assistant" : "You"}
                      </Text>
                      <Text style={{ whiteSpace: "pre-wrap" }}>
                        {renderMessageWithLinks(message.text)}
                      </Text>
                    </Paper>
                  ))}
                  {isSending && activeConversation ? (
                    <Paper
                      className="message"
                      data-role="assistant"
                      p="md"
                      radius="lg"
                    >
                      <Text size="sm" fw={600} className="message-label">
                        Assistant
                      </Text>
                      <Text
                        className="typing-indicator"
                        aria-label="Assistant is typing"
                      >
                        <span>.</span>
                        <span>.</span>
                        <span>.</span>
                      </Text>
                    </Paper>
                  ) : null}
                </Stack>
              </ScrollArea>

              {/* form stays pinned to bottom */}
              <form
                onSubmit={(event) => {
                  event.preventDefault();
                  handleSend();
                }}
              >
                <Group align="flex-end" gap="sm" wrap="nowrap">
                  <TextInput
                    className="message-input"
                    placeholder="Ask about routines, reminders, or support..."
                    styles={{ root: { flex: 1 } }}
                    size="md"
                    value={draft}
                    onChange={(event) => setDraft(event.currentTarget.value)}
                    disabled={isSending || !activeConversation}
                  />
                  {!isSending ? (
                    <Button size="md" type="submit">
                      Send
                    </Button>
                  ) : (
                    <Button
                      size="md"
                      color="red"
                      variant="light"
                      type="button"
                      onClick={handleCancel}
                    >
                      Cancel
                    </Button>
                  )}
                </Group>
              </form>
            </Paper>
          </Stack>
        </div>
      </Container>
    </Box>
  );
}
