from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import os
from typing import List, Dict, Any
import time
import uuid
from collections import defaultdict, deque
from typing import Tuple
import re

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from fastapi.middleware.cors import CORSMiddleware

#
# Load config from .env in the same folder
# Keeps keys and IDs outside of the code
#
BASE_DIR = os.path.dirname(__file__)
ENV_PATH = os.path.join(BASE_DIR, ".env")
load_dotenv(dotenv_path=ENV_PATH)

app = FastAPI(title="Bedrock Knowledge Base RAG API")

# Command to host server
# python -m uvicorn core.chatbotService.chatbot:app --reload --port 8000

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

#
# Simple in-memory conversation store (dev/demo)
# Key: (user_id, conversation_id) -> deque of turns
# Each turn: {"role": "user"|"assistant"|"memory", "content": str, "ts": float}
#
class ConversationStore:
    def __init__(self, max_turns: int = 12, ttl_seconds: int = 6 * 60 * 60):
        self.max_turns = max_turns
        self.ttl_seconds = ttl_seconds
        self._data: Dict[Tuple[str, str], deque] = defaultdict(
            lambda: deque(maxlen=self.max_turns)
        )
        self._last_seen: Dict[Tuple[str, str], float] = {}

    def _key(self, user_id: str, conversation_id: str) -> Tuple[str, str]:
        return (user_id, conversation_id)

    def append(self, user_id: str, conversation_id: str, role: str, content: str) -> None:
        k = self._key(user_id, conversation_id)
        self._data[k].append({"role": role, "content": content, "ts": time.time()})
        self._last_seen[k] = time.time()

    def get(self, user_id: str, conversation_id: str) -> List[Dict[str, Any]]:
        self.cleanup()
        return list(self._data.get(self._key(user_id, conversation_id), []))

    def cleanup(self) -> None:
        now = time.time()
        expired = [k for k, t in self._last_seen.items() if now - t > self.ttl_seconds]
        for k in expired:
            self._data.pop(k, None)
            self._last_seen.pop(k, None)


store = ConversationStore(max_turns=12, ttl_seconds=6 * 60 * 60)


def parse_memory_instruction(message: str) -> str:
    """
    If the user explicitly says 'remember ...', extract the content to remember.
    Returns "" if not a memory instruction.
    """
    if not message:
        return ""
    m = message.strip()

    pat = re.compile(r"^(?:please\s+)?remember(?:\s+that)?\s*[:\-]?\s*(.+)$", re.I)
    mm = pat.match(m)
    if mm:
        return mm.group(1).strip()

    return ""


def get_remembered_notes(history: List[Dict[str, Any]]) -> List[str]:
    """
    Collect stored memory notes from history turns where role == 'memory'.
    """
    notes: List[str] = []
    for turn in history:
        if turn.get("role") == "memory":
            content = (turn.get("content") or "").strip()
            if content:
                notes.append(content)
    return notes


# -------------------------
# Human-readable source helpers
# -------------------------

def _get_uri(meta: Dict[str, Any]) -> str:
    return (meta.get("x-amz-bedrock-kb-source-uri") or "").strip()


def _get_filename(meta: Dict[str, Any]) -> str:
    uri = _get_uri(meta)
    if uri:
        return os.path.basename(uri)
    return (meta.get("title") or meta.get("source") or meta.get("filename") or "document").strip()


def _get_page(meta: Dict[str, Any]) -> str:
    page = meta.get("x-amz-bedrock-kb-document-page-number")
    if isinstance(page, (int, float)):
        return f"p.{int(page)}"
    return "p.?"


def _infer_org_and_validity(filename: str) -> Tuple[str, str]:
    """
    IMPORTANT: do NOT invent authors/doctors.
    We only infer organization when it's obvious from the filename.
    """
    f = filename.lower()

    if "alzheimer" in f:
        return ("Alzheimer Society", "Recognized dementia-care organization; caregiver-facing guidance.")
    if "caregiver" in f or "care-partner" in f:
        return ("Caregiver resource document", "Practical caregiver guidance document in the knowledge base.")
    if "mvp" in f:
        return ("Caregiver resource document", "Educational dementia-care resource in the knowledge base.")

    return ("Knowledge base document", "Retrieved from the dementia-care knowledge base; used for practical guidance.")


def format_sources_human(sources: List[Dict[str, Any]], max_items: int = 6) -> List[Dict[str, str]]:
    """
    Dedup + return a list of dicts:
      [{"doc": "file.pdf", "page": "p.2", "org": "...", "valid": "..."}, ...]
    """
    if not sources:
        return []

    seen = set()
    out: List[Dict[str, str]] = []

    for s in sources:
        meta = s.get("metadata", {}) or {}

        uri = _get_uri(meta)
        page_raw = meta.get("x-amz-bedrock-kb-document-page-number")
        chunk = (meta.get("x-amz-bedrock-kb-chunk-id") or "").strip()
        key = (uri, page_raw, chunk)

        if key in seen:
            continue
        seen.add(key)

        doc = _get_filename(meta)
        page = _get_page(meta)
        org, valid = _infer_org_and_validity(doc)

        out.append({"doc": doc, "page": page, "org": org, "valid": valid})

        if len(out) >= max_items:
            break

    return out


def build_readable_citation_pack(sources: List[Dict[str, Any]]) -> str:
    """
    Build a compact "allowed citations" list for the formatter model.
    The model is instructed to ONLY use these strings (no new doctor names).
    """
    items = format_sources_human(sources, max_items=6)
    if not items:
        return ""

    lines = []
    for i, it in enumerate(items, 1):
        lines.append(
            f"{i}. {it['doc']} ({it['page']}) — {it['org']}. Validity: {it['valid']}"
        )
    return "\n".join(lines)


def normalize_human_output(text: str) -> str:
    """
    Final cleanup pass to ensure:
      - tips are on separate lines
      - "Source:" is on its own line
      - "Encouragement:" is on its own line
      - fix broken page formatting like "(p.\n\n6)" -> "(p.6)"
      - numbering never merges into previous line
    """
    if not text:
        return ""

    # Normalize Windows newlines
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Fix broken page formatting: (p.\n\n6) -> (p.6)
    text = re.sub(r"\(p\.\s*\n+\s*(\d+)\)", r"(p.\1)", text)

    # Also handle variants: "p.\n6" -> "p.6"
    text = re.sub(r"p\.\s*\n+\s*(\d+)", r"p.\1", text)

    # Ensure each "Source:" begins on a new line
    text = re.sub(r"[ \t]*Source:[ \t]*", "\nSource: ", text)

    # Ensure Encouragement starts on its own block
    text = re.sub(r"[ \t]*Encouragement:[ \t]*", "\n\nEncouragement: ", text)

    # Force numbered items like "2. **" to start on a new paragraph
    text = re.sub(r"\s+(?=\d+\.\s+\*\*)", "\n\n", text)

    # If the model forgets "Encouragement:" and just starts with "Remember,"
    # force it onto its own paragraph so it doesn't stick to the last Source line.
    text = re.sub(r"\.\s*(Remember,)", r".\n\nEncouragement: \1", text)

    # Clean up excessive blank lines
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def format_answer_for_humans(draft_answer: str, sources: List[Dict[str, Any]]) -> str:
    """
    Second pass: make output readable + put a readable source line after each tip.
    NEW: rotate sources so each tip uses a different citation line when possible.
    """
    draft_answer = (draft_answer or "").strip()
    if not draft_answer:
        return ""

    citation_pack = build_readable_citation_pack(sources)

    # If no sources, still keep things readable and encouraging
    if not citation_pack:
        fallback = (
            f"{draft_answer}\n\n"
            "You’re doing something hard — taking it one step at a time is already progress."
        )
        return normalize_human_output(fallback)

    prompt = (
        "You are formatting a dementia-care answer for a human reader.\n"
        "DO NOT invent doctors, authors, organizations, or quotes.\n"
        "Only use the citation lines provided in 'Citations you may use'.\n\n"
        "Output requirements:\n"
        "1) Provide 4–6 practical tips as a numbered list (1. ... 2. ... etc).\n"
        "2) AFTER EACH tip, add exactly ONE line that starts exactly with:\n"
        "   Source: <one citation line copied exactly from the allowed list>\n"
        "3) IMPORTANT: rotate sources so tips do NOT all use the same citation.\n"
        "   - Use a DIFFERENT citation line for each tip whenever possible.\n"
        "   - Prefer using citations in order (1, then 2, then 3...).\n"
        "   - Only repeat citations if you have more tips than citation lines; then cycle from the top.\n"
        "4) Make each tip concise and actionable (1–2 sentences).\n"
        "5) End with 1–2 sentences of gentle encouragement.\n"
        "6) Make sure each numbered tip starts on its own line, and each Source line is on its own line.\n\n"
        "Citations you may use (copy exactly, do not modify):\n"
        f"{citation_pack}\n\n"
        "Answer to format:\n"
        f"{draft_answer}\n"
    )

    formatted = rag_response(prompt)
    if formatted.get("answer"):
        return normalize_human_output(formatted["answer"].strip())

    # Fallback: attach readable citation pack at bottom (still human-readable)
    fallback = (
        f"{draft_answer}\n\n"
        "Sources (readable):\n"
        f"{citation_pack}\n\n"
        "You’re not alone — support is allowed."
    )
    return normalize_human_output(fallback)


def build_context_text(
    history: List[Dict[str, Any]], new_user_message: str, max_chars: int = 4000
) -> str:
    """
    Transcript + remembered notes + system policy.
    """
    remembered = get_remembered_notes(history)
    remembered_block = "\n".join([f"- {x}" for x in remembered]) if remembered else "(none)"

    lines: List[str] = []
    for turn in history:
        role = turn.get("role", "")
        if role == "memory":
            continue
        content = (turn.get("content") or "").strip()
        if not content:
            continue
        prefix = "User" if role == "user" else "Assistant"
        lines.append(f"{prefix}: {content}")

    lines.append(f"User: {new_user_message.strip()}")
    transcript = "\n".join(lines)

    if len(transcript) > max_chars:
        transcript = transcript[-max_chars:]

    return (
        "System:\n"
        "You are a dementia-support chatbot.\n"
        "For dementia-care guidance, prioritize answering from the knowledge base.\n"
        "Do NOT make up facts. If the KB does not support an answer, say so.\n"
        "You also have 'Remembered notes' below: these are trusted RAM memory the user explicitly asked you to remember.\n"
        "If the user asks about something in Remembered notes, answer using those notes (do NOT claim it came from the KB).\n"
        "When you provide dementia guidance from the KB, keep it practical and supportive.\n\n"
        f"Remembered notes (RAM memory for this conversation):\n{remembered_block}\n\n"
        f"Conversation transcript:\n{transcript}\n\n"
        "Assistant:"
    )


#
# Bedrock configuration
#
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_KB_ID = os.getenv("BEDROCK_KB_ID")
BEDROCK_MODEL_ARN = os.getenv("BEDROCK_MODEL_ARN")

bedrock_agent = None
if BEDROCK_KB_ID:
    bedrock_agent = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)


def rag_response(concern: str) -> Dict[str, Any]:
    """
    Calls Bedrock retrieve_and_generate() on the Knowledge Base.
    """
    concern = (concern or "").strip()

    if not concern:
        return {
            "answer": "",
            "sources": [],
            "backend": "bedrock-error",
            "error": "Empty question. Please type something.",
        }

    if bedrock_agent is None:
        return {
            "answer": "",
            "sources": [],
            "backend": "bedrock-missing",
            "error": (
                "Bedrock client is not configured. "
                "Check AWS_REGION and BEDROCK_KB_ID in .env."
            ),
        }

    if not BEDROCK_MODEL_ARN:
        return {
            "answer": "",
            "sources": [],
            "backend": "bedrock-error",
            "error": (
                "BEDROCK_MODEL_ARN is not set in .env. "
                "Set it to the Nova Micro model ARN for this KB."
            ),
        }

    try:
        response = bedrock_agent.retrieve_and_generate(
            input={"text": concern},
            retrieveAndGenerateConfiguration={
                "type": "KNOWLEDGE_BASE",
                "knowledgeBaseConfiguration": {
                    "knowledgeBaseId": BEDROCK_KB_ID,
                    "modelArn": BEDROCK_MODEL_ARN,
                },
            },
        )

        answer_text = (response.get("output", {}).get("text", "") or "").strip()

        sources: List[Dict[str, Any]] = []
        for citation in response.get("citations", []):
            for ref in citation.get("retrievedReferences", []):
                sources.append(
                    {
                        "location": ref.get("location", {}),
                        "metadata": ref.get("metadata", {}),
                    }
                )

        if not answer_text:
            return {
                "answer": "",
                "sources": sources,
                "backend": "bedrock-empty",
                "error": "Bedrock returned an empty answer.",
            }

        return {
            "answer": answer_text,
            "sources": sources,
            "backend": "bedrock",
        }

    except ClientError as e:
        return {
            "answer": "",
            "sources": [],
            "backend": "bedrock-error",
            "error": f"Bedrock ClientError: {e}",
        }

    except Exception as e:
        return {
            "answer": "",
            "sources": [],
            "backend": "bedrock-error",
            "error": f"Unexpected error: {e}",
        }


@app.get("/query")
def get_concern(concern: str):
    result = rag_response(concern)
    if not result.get("answer"):
        return JSONResponse(status_code=500, content=result)
    return result


@app.post("/query")
async def post_concern(request: Request):
    data = await request.json()
    concern = data.get("concern")

    if not concern:
        return JSONResponse(status_code=400, content={"error": "Missing 'concern' in request body."})

    result = rag_response(concern)
    if not result.get("answer"):
        return JSONResponse(status_code=500, content=result)
    return result


@app.post("/chat")
async def chat(request: Request):
    data = await request.json()

    user_id = data.get("user_id", "demo_user")
    conversation_id = data.get("conversation_id") or str(uuid.uuid4())
    message = (data.get("message") or "").strip()

    if not message:
        return JSONResponse(status_code=400, content={"error": "Missing 'message' in request body."})

    # RAM memory capture ("remember ...")
    mem = parse_memory_instruction(message)
    if mem:
        store.append(user_id, conversation_id, "memory", mem)
        store.append(user_id, conversation_id, "user", message)
        return {
            "answer": "Got it — I’ll remember that for this conversation.",
            "sources": [],
            "backend": "memory",
            "conversation_id": conversation_id,
        }

    history = store.get(user_id, conversation_id)
    context_text = build_context_text(history, message)
    result = rag_response(context_text)

    # NEW: human-readable per-tip citations + encouragement
    if result.get("answer"):
        result["answer"] = format_answer_for_humans(result["answer"], result.get("sources", []))

    store.append(user_id, conversation_id, "user", message)
    if result.get("answer"):
        store.append(user_id, conversation_id, "assistant", result["answer"])
    else:
        store.append(user_id, conversation_id, "assistant", result.get("error", "No answer returned."))

    out = dict(result)
    out["conversation_id"] = conversation_id
    return out


def run_cli():
    user_id = "demo_user"

    def yn(prompt: str, default: str = "y") -> bool:
        default = default.lower().strip()
        suffix = " [Y/n]: " if default == "y" else " [y/N]: "
        while True:
            ans = input(prompt + suffix).strip().lower()
            if ans == "":
                ans = default
            if ans in {"y", "yes"}:
                return True
            if ans in {"n", "no"}:
                return False
            print("Please type y or n.\n")

    def require_id(prompt: str) -> str:
        while True:
            cid = input(prompt).strip()
            if cid:
                return cid
            print("Conversation ID cannot be blank.\n")

    def pick_conversation_id() -> str:
        known = sorted([cid for (uid, cid) in store._data.keys() if uid == user_id])
        if known:
            print("\nExisting conversations:")
            for i, cid in enumerate(known, 1):
                print(f"  {i}) {cid}")
        else:
            print("\nNo existing conversations found yet.")

        if yn("\nContinue an existing conversation?", default="y") and known:
            return require_id("Enter an existing conversation ID (copy/paste): ")

        return require_id("Create a NEW conversation ID (you choose): ")

    def show_default_care_intro_if_first_time(cid: str) -> None:
        history = store.get(user_id, cid)
        if history:
            return

        intro_prompt = (
            "Give a short welcome message for a dementia-support chatbot and 4-6 basic, practical tips "
            "for caregivers/family. Keep it concise."
        )
        context_text = build_context_text([], intro_prompt)
        result = rag_response(context_text)

        if result.get("answer"):
            result["answer"] = format_answer_for_humans(result["answer"], result.get("sources", []))

        store.append(user_id, cid, "user", intro_prompt)
        if result.get("answer"):
            store.append(user_id, cid, "assistant", result["answer"])
            print("\nBot:")
            print(result["answer"])
        else:
            store.append(user_id, cid, "assistant", result.get("error", "No answer returned."))
            print("\nBot:")
            print(result.get("error", "No answer returned."))
        print()

    conversation_id = pick_conversation_id()
    show_default_care_intro_if_first_time(conversation_id)

    while True:
        msg = input("You: ").strip()
        if not msg:
            continue
        if msg.lower() in {"exit", "quit"}:
            break

        mem = parse_memory_instruction(msg)
        if mem:
            store.append(user_id, conversation_id, "memory", mem)
            store.append(user_id, conversation_id, "user", msg)
            print("\nBot:")
            print("Got it — I’ll remember that for this conversation.\n")
            continue

        history = store.get(user_id, conversation_id)
        context_text = build_context_text(history, msg)
        result = rag_response(context_text)

        if result.get("answer"):
            result["answer"] = format_answer_for_humans(result["answer"], result.get("sources", []))

        store.append(user_id, conversation_id, "user", msg)
        if result.get("answer"):
            store.append(user_id, conversation_id, "assistant", result["answer"])
        else:
            store.append(user_id, conversation_id, "assistant", result.get("error", "No answer returned."))

        print("\nBot:")
        print(result.get("answer") or result.get("error", "No answer returned."))
        print()

        if yn("Continue this conversation?", default="y"):
            continue

        if yn("Switch to another conversation?", default="y"):
            conversation_id = pick_conversation_id()
            show_default_care_intro_if_first_time(conversation_id)
            continue

        break


if __name__ == "__main__":
    run_cli()