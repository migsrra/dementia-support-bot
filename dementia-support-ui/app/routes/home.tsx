import type { Route } from "./+types/home";
import {
  Alert,
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
  id: string;
  role: "assistant" | "user";
  text: string;
};

type Conversation = {
  id: string;
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

function createClientId() {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  const randomPart = Math.random().toString(36).slice(2, 10);
  return `${Date.now()}-${randomPart}`;
}

function normalizeAssistantText(text: string) {
  return text
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]*\n[ \t]*/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

const INITIAL_ASSISTANT_MESSAGE =
  "Hi, I’m your dementia caregiving support chatbot. I’m here to answer questions and provide guidance related to dementia care.";

function createAssistantGreetingMessage(): ChatMessage {
  return {
    id: createClientId(),
    role: "assistant",
    text: INITIAL_ASSISTANT_MESSAGE,
  };
}

const STARTER_QUESTIONS = [
  "How can I calm someone with dementia who is feeling anxious?",
  "How do I gently remind my mother to eat her meals on time?",
  "Tips for engaging a loved one with dementia in conversation?",
  "What safety changes should I make at home for someone with dementia?",
];

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
  if (!uniq.length) return "";
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

    const baseText = normalizeAssistantText(
      data.response || "Sorry, I couldn't get an answer right now.",
    );

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
      id: createClientId(),
      sessionID: createSessionId(),
      title: "Current Conversation",
      messages: [createAssistantGreetingMessage()],
    },
  ]);
  const [activeConversationId, setActiveConversationId] = useState(
    () => conversations[0]?.id ?? "",
  );
  const [draft, setDraft] = useState("");
  const [sendingConversationIds, setSendingConversationIds] = useState<
    string[]
  >([]);
  const scrollViewportRef = useRef<HTMLDivElement | null>(null);
  const controllersRef = useRef<Record<string, AbortController>>({});

  const activeConversation =
    conversations.find(
      (conversation) => conversation.id === activeConversationId,
    ) ?? conversations[0];
  const isConversationUnstarted = Boolean(
    activeConversation &&
      activeConversation.messages.every((message) => message.role !== "user"),
  );
  const isConversationSending = (conversationId: string) =>
    sendingConversationIds.includes(conversationId);
  const isActiveConversationSending = Boolean(
    activeConversation && isConversationSending(activeConversation.id),
  );

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
      id: createClientId(),
      sessionID: createSessionId(),
      title: `New Conversation ${conversations.length + 1}`,
      messages: [createAssistantGreetingMessage()],
    };
    // update states
    setConversations((current) => [newConversation, ...current]);
    setActiveConversationId(newConversation.id);
    setDraft("");
  }

  // send message handler
  async function handleSend(messageOverride?: string) {
    if (!activeConversation) {
      return;
    }

    const conversationId = activeConversation.id;
    const sessionID = activeConversation.sessionID;
    const trimmed = (messageOverride ?? draft).trim();
    if (!trimmed || isConversationSending(conversationId)) {
      return;
    }

    // set message states
    const newController = new AbortController();
    controllersRef.current[conversationId] = newController;
    setSendingConversationIds((current) =>
      current.includes(conversationId)
        ? current
        : [...current, conversationId],
    );
    const userMessage: ChatMessage = {
      id: createClientId(),
      role: "user",
      text: trimmed,
    };

    setConversations((current) =>
      current.map((conversation) => {
        if (conversation.id !== conversationId) return conversation;

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
      const reply = await getAssistantReply(trimmed, sessionID, newController.signal);
      const assistantMessage: ChatMessage = {
        id: createClientId(),
        role: "assistant",
        text: reply.text,
      };

      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === conversationId
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
          id: createClientId(),
          role: "assistant",
          text: "Response cancelled.",
        };

        setConversations((current) =>
          current.map((conversation) =>
            conversation.id === conversationId
              ? {
                  ...conversation,
                  messages: [...conversation.messages, cancelledMessage],
                }
              : conversation,
          ),
        );
      }
    } finally {
      delete controllersRef.current[conversationId];
      setSendingConversationIds((current) =>
        current.filter((id) => id !== conversationId),
      );
    }
  }

  function handleCancel() {
    if (!activeConversation) {
      return;
    }

    const controller = controllersRef.current[activeConversation.id];
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
                Physician Sign In
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
                  {isActiveConversationSending && activeConversation ? (
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
                {isConversationUnstarted ? (
                  <Stack
                    key={activeConversation?.id}
                    gap="xs"
                    mb="sm"
                    className="starter-questions"
                  >
                    <Text
                      size="sm"
                      fw={600}
                      c="dimmed"
                      className="starter-question-heading"
                    >
                      Start with a suggested question
                    </Text>
                    <Stack gap="sm" w="100%" className="starter-question-list">
                      {STARTER_QUESTIONS.map((question) => (
                        <Button
                          key={`${activeConversation?.id}-${question}`}
                          type="button"
                          variant="unstyled"
                          radius="xl"
                          justify="flex-start"
                          className="starter-question-button"
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => handleSend(question)}
                          disabled={isActiveConversationSending}
                        >
                          {question}
                        </Button>
                      ))}
                    </Stack>
                  </Stack>
                ) : null}
                <Group align="flex-end" gap="sm" wrap="nowrap">
                  <TextInput
                    className="message-input"
                    placeholder="Ask about routines, reminders, or support..."
                    styles={{ root: { flex: 1 } }}
                    size="md"
                    value={draft}
                    onChange={(event) => setDraft(event.currentTarget.value)}
                    disabled={isActiveConversationSending || !activeConversation}
                  />
                  {!isActiveConversationSending ? (
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
            <Alert color="orange" variant="light" title="Disclaimer">
              Disclaimer: This tool provides general information only and is not
              a substitute for professional medical advice, diagnosis, or
              treatment. Always consult a qualified healthcare provider for
              medical concerns. As an AI system, this chatbot may generate
              incorrect or incomplete information.
            </Alert>
          </Stack>
        </div>
      </Container>
    </Box>
  );
}
