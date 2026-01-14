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

const seedMessages: ChatMessage[] = [
  {
    id: 1,
    role: "assistant",
    text:
      "Hi there. I can help with gentle reminders, daily routines, or questions about memory care.",
  },
];

let nextMessageId = 2;
let nextConversationId = 2;

async function getAssistantReply(prompt: string, conversationId: string) {
  const trimmed = prompt.trim();
  if (!trimmed) {
    return { text: "Tell me a little more, and I will help." };
  }

  // 10 Second timeout
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 10000);

  try {
    const response = await fetch("http://localhost:8000/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: "demo_user",
        conversation_id: conversationId,
        message: trimmed,
      }),
      signal: controller.signal,
    });

    if (!response.ok) {
      const errorBody = await response.json().catch(() => null);
      return {
        text:
          errorBody?.error ||
          errorBody?.detail ||
          `Backend error (${response.status})`,
      };
    }

    const data = await response.json();
    if (!data?.answer) {
      return { text: "Sorry, I couldn't get an answer right now." };
    }

    return { text: data.answer, conversationId: data.conversation_id || conversationId };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return { text: "The request timed out. Please try again." };
    }
    return { text: "Sorry, I couldn't reach the assistant right now." };
  } finally {
    clearTimeout(timeoutId);
  }
}


export default function Home() {
  const [conversations, setConversations] = useState<Conversation[]>([
    {
      id: 1,
      title: "Morning routine",
      messages: seedMessages,
    },
  ]);
  const [activeConversationId, setActiveConversationId] = useState(1);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const scrollViewportRef = useRef<HTMLDivElement | null>(null);

  const activeConversation =
    conversations.find((conversation) => conversation.id === activeConversationId) ??
    conversations[0];

  useEffect(() => {
    const viewport = scrollViewportRef.current;
    if (!viewport || !activeConversation) {
      return;
    }
    viewport.scrollTo({ top: viewport.scrollHeight, behavior: "smooth" });
  }, [activeConversationId, activeConversation?.messages.length]);

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
    setConversations((current) => [newConversation, ...current]);
    setActiveConversationId(newConversation.id);
    setDraft("");
  }

  async function handleSend() {
    const trimmed = draft.trim();
    if (!trimmed || isSending || !activeConversation) {
      return;
    }

    setIsSending(true);
    const userMessage: ChatMessage = {
      id: nextMessageId++,
      role: "user",
      text: trimmed,
    };
    setConversations((current) =>
      current.map((conversation) =>
        conversation.id === activeConversation.id
          ? {
              ...conversation,
              messages: [...conversation.messages, userMessage],
              title: conversation.title.startsWith("New conversation")
                ? trimmed
                : conversation.title,
            }
          : conversation,
      ),
    );
    setDraft("");

    const reply = await getAssistantReply(trimmed, String(activeConversation.id));
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
    setIsSending(false);
  }

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

            <Paper className="chat-card" p="lg" radius="lg" shadow="md">
              <Stack gap="md">
                <ScrollArea
                  h={360}
                  type="scroll"
                  offsetScrollbars
                  viewportRef={scrollViewportRef}
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
                    <Button size="md" type="submit" loading={isSending}>
                      Send
                    </Button>
                  </Group>
                </form>
              </Stack>
            </Paper>
          </Stack>
        </div>
      </Container>
    </Box>
  );
}
