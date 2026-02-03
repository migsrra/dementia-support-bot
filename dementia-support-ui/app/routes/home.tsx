import type { Route } from "./+types/home";
import {
  Badge,
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

export function meta({}: Route.MetaArgs) {
  return [
    { title: "Dementia Support Chat" },
    {
      name: "description",
      content: "Supportive, calm conversations powered by a RAG knowledge base.",
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
  title: string;
  messages: ChatMessage[];
};

// Initial data
const seedMessages: ChatMessage[] = [
  {
    id: 1,
    role: "assistant",
    text:
      "How can I help you today?",
  },
];

let nextMessageId = 2;
let nextConversationId = 2;

// Backend call function
async function getAssistantReply(prompt: string, conversationId: string, signal: AbortSignal) {
  
  // removing whitespace and returning early if empty
  const trimmed = prompt.trim();
  if (!trimmed) {  return { text: "Tell me a little more, and I will help." }; }

  try {
    // sent request to backend, blocks
    const response = await fetch("http://localhost:8000/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: "demo_user",
        conversation_id: conversationId,
        message: trimmed,
      }),
      signal,   // signal sent by UI
    });

    // error handling
    if (!response.ok) {
      const errorBody = await response.json().catch(() => null);
      return {
        text:
          errorBody?.error ||
          errorBody?.detail ||
          `Backend error (${response.status})`,
      };
    }

    // parse success response
    const data = await response.json();

    // error check
    if (!data?.answer) { return { text: "Sorry, I couldn't get an answer right now." }; }

    // return chatbot response
    return { text: data.answer, conversationId: data.conversation_id || conversationId };
  } catch (error) {   // catches exeptions like failures and aborts
    // timeout exception
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;    // UI will handle cancellation
    }
    console.error("getAssistantReply error:", error);
    // generic exception handle
    return { text: "Sorry, I couldn't reach the assistant right now." };
  } 
}

// frontend, updates UI automatically when states change
export default function Home() {
  // states
  const [conversations, setConversations] = useState<Conversation[]>([
    {
      id: 1,
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
    conversations.find((conversation) => conversation.id === activeConversationId) ??
    conversations[0];

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
      const reply = await getAssistantReply(trimmed, String(activeConversation.id), newController.signal);
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
              ? { ...conversation, messages: [...conversation.messages, cancelledMessage] }
              : conversation
          )
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
                  variant={conversation.id === activeConversationId ? "filled" : "subtle"}
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
                      <Text>{message.text}</Text>
                    </Paper>
                  ))}
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
