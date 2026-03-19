import argparse
import csv
import json
import math
import os
import random
import re
import statistics
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import boto3
import requests

DEFAULT_REGION = os.getenv("AWS_REGION", "us-east-1")
DEFAULT_LAMBDA_NAME = os.getenv("LAMBDA_NAME", "invokeAgentLambda")
DEFAULT_JUDGE_MODEL_ID = os.getenv("JUDGE_MODEL_ID", "us.amazon.nova-pro-v1:0")
DEFAULT_EMBED_MODEL_ID = os.getenv("EMBED_MODEL_ID", "amazon.titan-embed-text-v2:0")
DEFAULT_MAX_WORKERS = int(os.getenv("MAX_WORKERS", "4"))
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("PIPELINE_TIMEOUT_SECONDS", "30"))
DEFAULT_RELEVANCE_THRESHOLD = float(os.getenv("RELEVANCE_THRESHOLD", "0.70"))
DEFAULT_LATENCY_SLO_SECONDS = float(os.getenv("LATENCY_SLO_SECONDS", "10.0"))
DEFAULT_KB_ID = os.getenv("BEDROCK_KB_ID", "")
DEFAULT_GENERATOR_MODEL_ID = os.getenv("GENERATOR_MODEL_ID", DEFAULT_JUDGE_MODEL_ID)
DEFAULT_INVOKE_MODE = os.getenv("INVOKE_MODE", "lambda")
DEFAULT_INVOKE_AGENT_API_URL = os.getenv("INVOKE_AGENT_API_URL", os.getenv("VITE_INVOKE_AGENT_API_URL", ""))
DEFAULT_API_TIMEOUT_SECONDS = int(os.getenv("API_TIMEOUT_SECONDS", "60"))


@dataclass
class DatasetItem:
    id: str
    question: str
    ground_truth_answer: str = ""
    key_facts: Optional[List[str]] = None
    source_doc: str = ""
    source_excerpt: str = ""


@dataclass
class PipelineResult:
    id: str
    question: str
    session_id: str
    status_code: Optional[int]
    latency_ms: Optional[float]
    answer: str
    context_raw: str
    context_chunks: List[str]
    retrieved_source_docs: List[str]
    raw_body: Dict[str, Any]
    error: str = ""


@dataclass
class JudgedResult:
    id: str
    question: str
    latency_ms: Optional[float]
    answer: str
    context_raw: str
    context_source: str
    faithfulness_score: Optional[float]
    faithfulness_pass: Optional[bool]
    faithfulness_supported_claim_ratio: Optional[float]
    quality_score_1_5: Optional[int]
    empathy_score_1_5: Optional[int]
    precision_at_5_judge: Optional[float]
    precision_at_5_judge_pass: Optional[bool]
    recall_judge_score: Optional[float]
    recall_judge_pass: Optional[bool]
    source_doc_hit_at_10: Optional[bool]
    source_doc_match_count: Optional[int]
    precision_at_5_embedding: Optional[float]
    recall_at_10_embedding: Optional[float]
    latency_pass_r1: Optional[bool]
    quality_reasoning: str = ""
    empathy_reasoning: str = ""
    faithfulness_reasoning: str = ""
    precision_judge_reasoning: str = ""
    recall_judge_reasoning: str = ""
    error: str = ""



def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def safe_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    values = sorted(values)
    k = (len(values) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return values[int(k)]
    d0 = values[f] * (c - k)
    d1 = values[c] * (k - f)
    return d0 + d1


def normalize_text(s: Any) -> str:
    if s is None:
        return ""
    if isinstance(s, str):
        return s.strip()
    return json.dumps(s, ensure_ascii=False)


def join_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def parse_key_facts(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [normalize_text(x) for x in value if normalize_text(x)]
    if isinstance(value, str):
        v = value.strip()
        if not v:
            return []
        if v.startswith("["):
            try:
                parsed = json.loads(v)
                if isinstance(parsed, list):
                    return [normalize_text(x) for x in parsed if normalize_text(x)]
            except Exception:
                pass
        if "|" in v:
            return [x.strip() for x in v.split("|") if x.strip()]
        if "\n" in v:
            return [x.strip("-• \t") for x in v.splitlines() if x.strip()]
        return [v]
    return [normalize_text(value)]


def load_dataset(path: Path) -> List[DatasetItem]:
    if not path.exists():
        raise FileNotFoundError(f"Dataset file not found: {path}")

    items: List[DatasetItem] = []

    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "items" in data:
            rows = data["items"]
        elif isinstance(data, list):
            rows = data
        else:
            raise ValueError("JSON dataset must be a list or an object with 'items'.")
        for idx, row in enumerate(rows, start=1):
            items.append(
                DatasetItem(
                    id=str(row.get("id", f"q{idx:03d}")),
                    question=normalize_text(row.get("question")),
                    ground_truth_answer=normalize_text(row.get("ground_truth_answer", "")),
                    key_facts=parse_key_facts(row.get("key_facts")),
                    source_doc=normalize_text(row.get("source_doc", "")),
                    source_excerpt=normalize_text(row.get("source_excerpt", "")),
                )
            )
    elif path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader, start=1):
                items.append(
                    DatasetItem(
                        id=str(row.get("id") or f"q{idx:03d}"),
                        question=normalize_text(row.get("question")),
                        ground_truth_answer=normalize_text(row.get("ground_truth_answer", "")),
                        key_facts=parse_key_facts(row.get("key_facts")),
                        source_doc=normalize_text(row.get("source_doc", "")),
                        source_excerpt=normalize_text(row.get("source_excerpt", "")),
                    )
                )
    else:
        raise ValueError("Dataset must be .json or .csv")

    cleaned = []
    for item in items:
        if not item.question:
            continue
        if item.key_facts is None:
            item.key_facts = []
        cleaned.append(item)

    if not cleaned:
        raise ValueError("Dataset contains no usable questions.")

    return cleaned


def split_context_into_chunks(context: str) -> List[str]:
    text = normalize_text(context)
    if not text:
        return []

    candidates = [
        r"\n\s*---+\s*\n",
        r"\n\s*\*\*\*+\s*\n",
        r"\n\s*Source\s+\d+[:\-]\s*",
        r"\n\s*Chunk\s+\d+[:\-]\s*",
        r"\n\s*Document\s+\d+[:\-]\s*",
    ]
    for pat in candidates:
        parts = [p.strip() for p in re.split(pat, text) if p.strip()]
        if len(parts) >= 2:
            return parts

    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) >= 2:
        return paras

    chunk_size = 1200
    parts = [text[i:i + chunk_size].strip() for i in range(0, len(text), chunk_size)]
    return [p for p in parts if p]


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        norm = normalize_text(item)
        if not norm:
            continue
        key = re.sub(r"\s+", " ", norm)
        if key in seen:
            continue
        seen.add(key)
        out.append(norm)
    return out


def basename_from_source(value: str) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    text = text.split("?")[0]
    text = text.rstrip("/")
    return os.path.basename(text)

def extract_retrieved_source_docs(result_body: Dict[str, Any]) -> List[str]:
    docs: List[str] = []

    # Direct KB fallback retrieval results
    retrieval_results = result_body.get("__retrieval_results__", [])
    if isinstance(retrieval_results, list):
        for r in retrieval_results:
            if not isinstance(r, dict):
                continue
            meta = r.get("metadata", {}) or {}
            loc = r.get("location", {}) or {}
            candidates = [
                meta.get("source_url", ""),
                meta.get("x-amz-bedrock-kb-source-uri", ""),
                ((loc.get("s3Location") or {}).get("uri") if isinstance(loc, dict) else ""),
                ((loc.get("webLocation") or {}).get("url") if isinstance(loc, dict) else ""),
            ]
            for c in candidates:
                b = basename_from_source(normalize_text(c))
                if b:
                    docs.append(b)

    attribution = result_body.get("attribution")
    if isinstance(attribution, dict):
        citations = attribution.get("citations", [])
        if isinstance(citations, list):
            for citation in citations:
                if not isinstance(citation, dict):
                    continue
                refs = citation.get("retrievedReferences", [])
                if not isinstance(refs, list):
                    continue
                for ref in refs:
                    if not isinstance(ref, dict):
                        continue
                    meta = ref.get("metadata", {}) or {}
                    loc = ref.get("location", {}) or {}
                    candidates = [
                        meta.get("source_url", ""),
                        meta.get("x-amz-bedrock-kb-source-uri", ""),
                        ((loc.get("s3Location") or {}).get("uri") if isinstance(loc, dict) else ""),
                        ((loc.get("webLocation") or {}).get("url") if isinstance(loc, dict) else ""),
                    ]
                    for c in candidates:
                        b = basename_from_source(normalize_text(c))
                        if b:
                            docs.append(b)

    return _dedupe_preserve_order(docs)


def extract_attribution_context(result_body: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    Pull retrieved reference text from Bedrock-style attribution payloads such as:
      attribution.citations[].retrievedReferences[].content.text
    Returns (context_raw, context_chunks).
    """
    attribution = result_body.get("attribution")
    if not isinstance(attribution, dict):
        return "", []

    citations = attribution.get("citations")
    if not isinstance(citations, list):
        return "", []

    chunks: List[str] = []

    for citation in citations:
        if not isinstance(citation, dict):
            continue
        refs = citation.get("retrievedReferences")
        if not isinstance(refs, list):
            continue
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            content = ref.get("content")
            text = ""
            if isinstance(content, dict):
                text = normalize_text(content.get("text", ""))
            elif isinstance(content, str):
                text = normalize_text(content)
            if text:
                chunks.append(text)

    chunks = _dedupe_preserve_order(chunks)
    context_raw = "\n\n---\n\n".join(chunks) if chunks else ""
    return context_raw, chunks


def extract_pipeline_fields(result_body: Dict[str, Any]) -> Tuple[str, str, List[str]]:
    """
    Robustly extracts:
      - answer
      - context_raw
      - context_chunks

    Priority for answer:
      completion -> answer -> response -> output -> message

    Priority for context:
      clean_context/context-like fields first
      then Bedrock attribution citations/retrievedReferences fallback
    """
    candidate_answer_keys = [
        "completion",
        "answer",
        "response",
        "output",
        "message",
    ]
    candidate_context_keys = [
        "clean_context",
        "context",
        "retrieved_context",
        "supporting_context",
        "cleanContext",
        "retrievedContext",
        "supportingContext",
    ]

    answer = ""
    context_raw = ""
    context_chunks: List[str] = []

    for k in candidate_answer_keys:
        if k in result_body and result_body[k] is not None:
            answer = normalize_text(result_body[k])
            if answer:
                break

    for k in candidate_context_keys:
        if k not in result_body or result_body[k] is None:
            continue
        value = result_body[k]
        if isinstance(value, list):
            raw_chunks = [normalize_text(x) for x in value if normalize_text(x)]
            raw_chunks = _dedupe_preserve_order(raw_chunks)
            if raw_chunks:
                context_chunks = raw_chunks
                context_raw = "\n\n---\n\n".join(raw_chunks)
                break
        else:
            candidate_context = normalize_text(value)
            if candidate_context:
                context_raw = candidate_context
                context_chunks = split_context_into_chunks(candidate_context)
                break

    # Attribution fallback when no explicit clean_context exists.
    if not context_raw:
        att_context_raw, att_chunks = extract_attribution_context(result_body)
        if att_context_raw:
            context_raw = att_context_raw
            context_chunks = att_chunks

    return answer, context_raw, context_chunks


def pretty_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def try_parse_json_from_text(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if match:
        candidate = match.group(0)
        return json.loads(candidate)
    raise ValueError(f"Could not parse JSON from model output: {text[:300]}")


def cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class AwsEvalClients:
    def __init__(self, region: str):
        self.region = region
        self.lambda_client = boto3.client("lambda", region_name=region)
        self.bedrock_runtime = boto3.client("bedrock-runtime", region_name=region)
        self.cloudwatch = boto3.client("cloudwatch", region_name=region)
        self.bedrock_agent_runtime = boto3.client("bedrock-agent-runtime", region_name=region)

    def invoke_lambda_sync(self, function_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        response = self.lambda_client.invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode("utf-8"),
        )
        payload_bytes = response["Payload"].read()
        parsed = json.loads(payload_bytes.decode("utf-8"))
        parsed["__http_status__"] = response.get("StatusCode")
        return parsed

    def invoke_api_sync(
        self,
        api_url: str,
        session_id: str,
        prompt: str,
        headers: Optional[Dict[str, str]] = None,
        timeout_seconds: int = 60,
    ) -> Dict[str, Any]:
        if not api_url:
            raise ValueError("API URL is required for invoke-mode=api")
        url = join_url(api_url, requests.utils.quote(session_id, safe=""))
        req_headers = {"Content-Type": "text/plain"}
        if headers:
            req_headers.update(headers)
        resp = requests.post(
            url,
            data=prompt.encode("utf-8"),
            headers=req_headers,
            timeout=timeout_seconds,
        )
        try:
            parsed = resp.json()
        except Exception:
            parsed = {"raw_body_text": resp.text}
        parsed["__http_status__"] = resp.status_code
        return parsed

    def converse_json(self, model_id: str, system_prompt: str, user_prompt: str,
                      temperature: float = 0.0, max_tokens: int = 800) -> Dict[str, Any]:
        resp = self.bedrock_runtime.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_prompt}]}],
            inferenceConfig={
                "temperature": temperature,
                "maxTokens": max_tokens,
            },
        )
        output_message = resp["output"]["message"]
        text_parts = []
        for part in output_message.get("content", []):
            if "text" in part:
                text_parts.append(part["text"])
        text = "\n".join(text_parts).strip()
        return try_parse_json_from_text(text)

    def embed_text(self, model_id: str, text: str) -> List[float]:
        body = {"inputText": text}
        resp = self.bedrock_runtime.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )
        payload = json.loads(resp["body"].read())
        embedding = payload.get("embedding", [])
        if not isinstance(embedding, list):
            return []
        return embedding

    def get_lambda_metrics(self, function_name: str, hours_back: int = 2) -> Dict[str, Any]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=hours_back)
        metric_names = ["Duration", "Invocations", "Errors", "Throttles"]
        out = {}
        for metric_name in metric_names:
            stat = "Average" if metric_name == "Duration" else "Sum"
            response = self.cloudwatch.get_metric_statistics(
                Namespace="AWS/Lambda",
                MetricName=metric_name,
                Dimensions=[{"Name": "FunctionName", "Value": function_name}],
                StartTime=start,
                EndTime=end,
                Period=300,
                Statistics=[stat],
            )
            datapoints = sorted(response.get("Datapoints", []), key=lambda x: x["Timestamp"])
            out[metric_name] = datapoints
        return out

    def retrieve_kb_chunks(self, kb_id: str, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        if not kb_id:
            return []
        resp = self.bedrock_agent_runtime.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
        )
        results = resp.get("retrievalResults", [])
        out = []
        for r in results:
            content = r.get("content", {})
            text = ""
            if isinstance(content, dict):
                text = normalize_text(content.get("text", ""))
            score = r.get("score")
            out.append({
                "text": text,
                "score": score,
                "location": r.get("location", {}),
                "metadata": r.get("metadata", {}),
            })
        return out


FAITHFULNESS_SYSTEM = """You are a strict evaluator for a healthcare-support RAG system.
Return valid JSON only. Do not include markdown fences.
"""

def faithfulness_user_prompt(answer: str, context: str) -> str:
    return f"""
Evaluate whether the answer is faithful to the retrieved context.

Retrieved context:
{context}

Answer:
{answer}

Instructions:
1) Identify the factual claims in the answer.
2) For each claim, mark whether it is supported by the retrieved context.
3) Compute supported_claim_ratio = supported_claims / total_claims.
4) Set pass = true if supported_claim_ratio >= 0.85 and there are no materially unsupported claims that could mislead a caregiver.
5) Return JSON with exactly these keys:
{{
  "claims": [
    {{
      "claim": "string",
      "supported": true,
      "evidence": "short supporting quote or empty string"
    }}
  ],
  "supported_claim_ratio": 0.0,
  "score": 0.0,
  "pass": true,
  "reasoning": "short explanation"
}}

Use score = supported_claim_ratio.
If there are zero factual claims, score whether the answer is still safely grounded. Be conservative.
""".strip()


QUALITY_SYSTEM = """You are a strict evaluator for the practical quality of a dementia caregiver support response.
Return valid JSON only.
"""

def quality_user_prompt(question: str, answer: str, ground_truth_answer: str = "") -> str:
    return f"""
Evaluate the response quality on a 1-5 scale.

Question:
{question}

Answer:
{answer}

Ground truth / ideal answer:
{ground_truth_answer}

Rubric:
1 = poor; does not answer the question, unclear, unsafe, or misleading
2 = weak; partially answers but misses major needs
3 = acceptable; generally helpful but incomplete or generic
4 = strong; clear, useful, appropriate, and addresses the user well
5 = excellent; highly useful, clear, well-targeted, and appropriate

Return JSON exactly:
{{
  "score": 1,
  "reasoning": "short explanation"
}}
""".strip()


EMPATHY_SYSTEM = """You are a strict evaluator for empathy in dementia caregiver support responses.
Return valid JSON only.
"""


PRECISION_SYSTEM = """You are a strict evaluator for retrieval precision in a RAG system.
Return valid JSON only.
"""

def precision_user_prompt(question: str, chunks: List[str]) -> str:
    chunk_lines = []
    for i, chunk in enumerate(chunks[:5], start=1):
        chunk_lines.append(f"Chunk {i}:\n{chunk}")
    joined = "\n\n".join(chunk_lines) if chunk_lines else "(no chunks)"
    return f"""
Evaluate retrieval precision for the top 5 chunks.

Question:
{question}

Retrieved chunks:
{joined}

Instructions:
- For each chunk, decide whether it is directly relevant to answering the question.
- Compute score = relevant_chunks / number_of_chunks_considered.
- Set pass = true if score >= 0.80.

Return JSON exactly:
{{
  "chunk_relevance": [
    {{
      "chunk_index": 1,
      "relevant": true,
      "reason": "short explanation"
    }}
  ],
  "score": 0.0,
  "pass": true,
  "reasoning": "short explanation"
}}
""".strip()

def empathy_user_prompt(question: str, answer: str) -> str:
    return f"""
Rate the empathy of the answer on a 1-5 scale.

Question:
{question}

Answer:
{answer}

Rubric:
1 = cold, dismissive, robotic, or tone-deaf
2 = minimally polite but emotionally flat
3 = somewhat supportive but generic
4 = very empathic; warm, validating, supportive, and appropriate
5 = highly empathic; deeply validating and supportive without exaggeration or unsafe reassurance

Return JSON exactly:
{{
  "score": 1,
  "reasoning": "short explanation"
}}
""".strip()


RECALL_SYSTEM = """You are a strict evaluator for retrieval recall in a RAG system.
Return valid JSON only.
"""

def recall_user_prompt(context: str, ground_truth_answer: str, key_facts: List[str]) -> str:
    key_facts_block = "\n".join(f"- {fact}" for fact in key_facts) if key_facts else "(none provided)"
    return f"""
Evaluate whether the retrieved context contains the important information needed for the ideal answer.

Retrieved context:
{context}

Ground truth answer:
{ground_truth_answer}

Key facts:
{key_facts_block}

Instructions:
- Determine whether the context contains the key facts needed to support the ideal answer.
- Compute score from 0.0 to 1.0 representing proportion of key facts present.
- Set pass = true if score >= 0.80.
- If no key facts are provided, infer them from the ground truth answer conservatively.

Return JSON exactly:
{{
  "score": 0.0,
  "pass": true,
  "reasoning": "short explanation"
}}
""".strip()



DATASET_GEN_SYSTEM = """You create evaluation datasets for a dementia caregiver support chatbot.
Return valid JSON only.
"""

def dataset_generation_user_prompt(source_snippets: str, n_questions: int) -> str:
    return f"""
Using only the source material below, generate {n_questions} caregiver-style questions that should be answerable from the material.
For each item return:
- id
- question
- ground_truth_answer
- key_facts (3-6 short bullet facts)
- source_doc
- source_excerpt

Source material:
{source_snippets}

Return JSON exactly in this form:
{{
  "items": [
    {{
      "id": "q001",
      "question": "...",
      "ground_truth_answer": "...",
      "key_facts": ["..."],
      "source_doc": "...",
      "source_excerpt": "..."
    }}
  ]
}}
""".strip()


def build_lambda_payload(question: str, session_id: str) -> Dict[str, Any]:
    return {
        "pathParameters": {"sessionID": session_id},
        "body": json.dumps(question),
    }


def invoke_single_question(
    clients: AwsEvalClients,
    function_name: str,
    item: DatasetItem,
    kb_id: str = "",
    fallback_retrieve_top_k: int = 0,
    invoke_mode: str = DEFAULT_INVOKE_MODE,
    api_url: str = DEFAULT_INVOKE_AGENT_API_URL,
    api_headers: Optional[Dict[str, str]] = None,
    api_timeout_seconds: int = DEFAULT_API_TIMEOUT_SECONDS,
) -> PipelineResult:
    session_id = str(uuid.uuid4())

    start = time.perf_counter()
    try:
        if invoke_mode == "api":
            response = clients.invoke_api_sync(
                api_url=api_url,
                session_id=session_id,
                prompt=item.question,
                headers=api_headers,
                timeout_seconds=api_timeout_seconds,
            )
        else:
            payload = build_lambda_payload(item.question, session_id)
            response = clients.invoke_lambda_sync(function_name, payload)
        latency_ms = (time.perf_counter() - start) * 1000.0
        status_code = response.get("__http_status__")

        if "body" in response:
            body = response["body"]
            if isinstance(body, str):
                try:
                    body = json.loads(body)
                except Exception:
                    body = {"raw_body_text": body}
        else:
            body = response

        if invoke_mode == "api" and not isinstance(body, dict):
            body = {"raw_body_text": normalize_text(body)}

        answer, context_raw, context_chunks = extract_pipeline_fields(body)
        retrieved_source_docs = extract_retrieved_source_docs(body)

        if not context_chunks and kb_id and fallback_retrieve_top_k > 0:
            try:
                retrieved = clients.retrieve_kb_chunks(kb_id, item.question, top_k=fallback_retrieve_top_k)
                fallback_chunks = _dedupe_preserve_order([normalize_text(x.get("text", "")) for x in retrieved])
                if fallback_chunks:
                    context_chunks = fallback_chunks
                    context_raw = "\n\n---\n\n".join(fallback_chunks)
                    body["__context_source__"] = "kb_retrieve_fallback"
                    body["__retrieval_results__"] = retrieved
                    retrieved_source_docs = extract_retrieved_source_docs(body)
            except Exception as retrieve_error:
                body["__context_source__"] = "none"
                body["__retrieval_error__"] = str(retrieve_error)

        if context_chunks and "__context_source__" not in body:
            body["__context_source__"] = "pipeline"
        elif not context_chunks and "__context_source__" not in body:
            body["__context_source__"] = "none"

        return PipelineResult(
            id=item.id,
            question=item.question,
            session_id=session_id,
            status_code=status_code,
            latency_ms=latency_ms,
            answer=answer,
            context_raw=context_raw,
            context_chunks=context_chunks,
            retrieved_source_docs=retrieved_source_docs,
            raw_body=body,
            error="",
        )

    except Exception as e:
        latency_ms = (time.perf_counter() - start) * 1000.0
        return PipelineResult(
            id=item.id,
            question=item.question,
            session_id=session_id,
            status_code=None,
            latency_ms=latency_ms,
            answer="",
            context_raw="",
            context_chunks=[],
            retrieved_source_docs=[],
            raw_body={},
            error=str(e),
        )


def judge_single_result(
    clients: AwsEvalClients,
    judge_model_id: str,
    embed_model_id: str,
    item: DatasetItem,
    pipeline_result: PipelineResult,
    relevance_threshold: float,
) -> JudgedResult:
    try:
        if pipeline_result.error:
            return JudgedResult(
                id=item.id,
                question=item.question,
                latency_ms=pipeline_result.latency_ms,
                answer=pipeline_result.answer,
                context_raw=pipeline_result.context_raw,
                context_source=normalize_text(pipeline_result.raw_body.get("__context_source__", "none")),
                faithfulness_score=None,
                faithfulness_pass=None,
                faithfulness_supported_claim_ratio=None,
                quality_score_1_5=None,
                empathy_score_1_5=None,
                recall_judge_score=None,
                recall_judge_pass=None,
                source_doc_hit_at_10=None,
                source_doc_match_count=None,
                precision_at_5_embedding=None,
                recall_at_10_embedding=None,
                latency_pass_r1=(pipeline_result.latency_ms is not None and pipeline_result.latency_ms / 1000.0 <= DEFAULT_LATENCY_SLO_SECONDS),
                error=f"Pipeline error: {pipeline_result.error}",
            )

        faith = clients.converse_json(
            model_id=judge_model_id,
            system_prompt=FAITHFULNESS_SYSTEM,
            user_prompt=faithfulness_user_prompt(pipeline_result.answer, pipeline_result.context_raw),
            temperature=0.0,
            max_tokens=1200,
        )
        quality = clients.converse_json(
            model_id=judge_model_id,
            system_prompt=QUALITY_SYSTEM,
            user_prompt=quality_user_prompt(item.question, pipeline_result.answer, item.ground_truth_answer),
            temperature=0.0,
            max_tokens=400,
        )
        empathy = clients.converse_json(
            model_id=judge_model_id,
            system_prompt=EMPATHY_SYSTEM,
            user_prompt=empathy_user_prompt(item.question, pipeline_result.answer),
            temperature=0.0,
            max_tokens=400,
        )

        precision_judge_score = None
        precision_judge_pass = None
        precision_judge_reasoning = ""
        if pipeline_result.context_chunks:
            precision_judge = clients.converse_json(
                model_id=judge_model_id,
                system_prompt=PRECISION_SYSTEM,
                user_prompt=precision_user_prompt(item.question, pipeline_result.context_chunks[:5]),
                temperature=0.0,
                max_tokens=700,
            )
            precision_judge_score = float(precision_judge.get("score")) if precision_judge.get("score") is not None else None
            precision_judge_pass = bool(precision_judge.get("pass")) if precision_judge.get("pass") is not None else None
            precision_judge_reasoning = normalize_text(precision_judge.get("reasoning", ""))

        recall_score = None
        recall_pass = None
        recall_reasoning = ""
        if item.ground_truth_answer or item.key_facts:
            recall = clients.converse_json(
                model_id=judge_model_id,
                system_prompt=RECALL_SYSTEM,
                user_prompt=recall_user_prompt(
                    pipeline_result.context_raw,
                    item.ground_truth_answer,
                    item.key_facts or [],
                ),
                temperature=0.0,
                max_tokens=500,
            )
            recall_score = float(recall.get("score")) if recall.get("score") is not None else None
            recall_pass = bool(recall.get("pass")) if recall.get("pass") is not None else None
            recall_reasoning = normalize_text(recall.get("reasoning", ""))

        expected_doc = basename_from_source(item.source_doc)
        retrieved_docs = [basename_from_source(x) for x in (pipeline_result.retrieved_source_docs or [])]
        source_doc_match_count = sum(1 for d in retrieved_docs if d.lower() == expected_doc.lower()) if expected_doc else None
        source_doc_hit_at_10 = (source_doc_match_count > 0) if expected_doc else None

        precision_at_5, recall_at_10 = compute_embedding_retrieval_scores(
            clients=clients,
            embed_model_id=embed_model_id,
            question=item.question,
            context_chunks=pipeline_result.context_chunks,
            key_facts=item.key_facts or [],
            ground_truth_answer=item.ground_truth_answer,
            relevance_threshold=relevance_threshold,
        )

        faith_ratio = faith.get("supported_claim_ratio")
        faith_score = float(faith.get("score")) if faith.get("score") is not None else (
            float(faith_ratio) if faith_ratio is not None else None
        )
        faith_pass = bool(faith.get("pass")) if faith.get("pass") is not None else (
            faith_score is not None and faith_score >= 0.85
        )
        quality_score = int(quality.get("score")) if quality.get("score") is not None else None
        empathy_score = int(empathy.get("score")) if empathy.get("score") is not None else None
        latency_pass = None
        if pipeline_result.latency_ms is not None:
            latency_pass = (pipeline_result.latency_ms / 1000.0) <= DEFAULT_LATENCY_SLO_SECONDS

        return JudgedResult(
            id=item.id,
            question=item.question,
            latency_ms=pipeline_result.latency_ms,
            answer=pipeline_result.answer,
            context_raw=pipeline_result.context_raw,
            context_source=normalize_text(pipeline_result.raw_body.get("__context_source__", "none")),
            faithfulness_score=faith_score,
            faithfulness_pass=faith_pass,
            faithfulness_supported_claim_ratio=float(faith_ratio) if faith_ratio is not None else None,
            quality_score_1_5=quality_score,
            empathy_score_1_5=empathy_score,
            precision_at_5_judge=precision_judge_score,
            precision_at_5_judge_pass=precision_judge_pass,
            recall_judge_score=recall_score,
            recall_judge_pass=recall_pass,
            source_doc_hit_at_10=source_doc_hit_at_10,
            source_doc_match_count=source_doc_match_count,
            precision_at_5_embedding=precision_at_5,
            recall_at_10_embedding=recall_at_10,
            latency_pass_r1=latency_pass,
            quality_reasoning=normalize_text(quality.get("reasoning", "")),
            empathy_reasoning=normalize_text(empathy.get("reasoning", "")),
            faithfulness_reasoning=normalize_text(faith.get("reasoning", "")),
            precision_judge_reasoning=precision_judge_reasoning,
            recall_judge_reasoning=recall_reasoning,
            error="",
        )
    except Exception as e:
        return JudgedResult(
            id=item.id,
            question=item.question,
            latency_ms=pipeline_result.latency_ms,
            answer=pipeline_result.answer,
            context_raw=pipeline_result.context_raw,
            context_source=normalize_text(pipeline_result.raw_body.get("__context_source__", "none")),
            faithfulness_score=None,
            faithfulness_pass=None,
            faithfulness_supported_claim_ratio=None,
            quality_score_1_5=None,
            empathy_score_1_5=None,
            precision_at_5_judge=None,
            precision_at_5_judge_pass=None,
            recall_judge_score=None,
            recall_judge_pass=None,
            source_doc_hit_at_10=None,
            source_doc_match_count=None,
            precision_at_5_embedding=None,
            recall_at_10_embedding=None,
            latency_pass_r1=(pipeline_result.latency_ms is not None and pipeline_result.latency_ms / 1000.0 <= DEFAULT_LATENCY_SLO_SECONDS),
            error=str(e),
        )


def compute_embedding_retrieval_scores(
    clients: AwsEvalClients,
    embed_model_id: str,
    question: str,
    context_chunks: List[str],
    key_facts: List[str],
    ground_truth_answer: str,
    relevance_threshold: float,
) -> Tuple[Optional[float], Optional[float]]:
    if not context_chunks:
        return None, None

    q_emb = clients.embed_text(embed_model_id, question)
    top10_chunks = context_chunks[:10]
    chunk_embs = [clients.embed_text(embed_model_id, chunk) for chunk in top10_chunks]
    sims = [cosine_similarity(q_emb, emb) for emb in chunk_embs]

    top5 = sims[:5]
    precision_at_5 = (sum(1 for s in top5 if s >= relevance_threshold) / len(top5)) if top5 else None

    if key_facts:
        fact_embs = [clients.embed_text(embed_model_id, fact) for fact in key_facts]
        present = 0
        for fact_emb in fact_embs:
            best = 0.0
            for ch_emb in chunk_embs:
                best = max(best, cosine_similarity(fact_emb, ch_emb))
            if best >= relevance_threshold:
                present += 1
        recall_at_10 = present / len(fact_embs) if fact_embs else None
    elif ground_truth_answer:
        gt_emb = clients.embed_text(embed_model_id, ground_truth_answer)
        best = 0.0
        for ch_emb in chunk_embs:
            best = max(best, cosine_similarity(gt_emb, ch_emb))
        recall_at_10 = 1.0 if best >= relevance_threshold else 0.0
    else:
        recall_at_10 = None

    return precision_at_5, recall_at_10


def write_manual_review_templates(
    out_dir: Path,
    dataset: List[DatasetItem],
    pipeline_results: Dict[str, PipelineResult],
) -> None:
    random.seed(42)
    ids = [item.id for item in dataset]
    sample_50 = set(random.sample(ids, min(50, len(ids))))

    path_resp = out_dir / "manual_review_responses_r2_r5.csv"
    with path_resp.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id",
            "question",
            "answer",
            "context_raw",
            "expert_faithful_binary",
            "expert_empathy_score_1_5",
            "expert_notes",
        ])
        for item in dataset:
            if item.id not in sample_50:
                continue
            pr = pipeline_results[item.id]
            writer.writerow([item.id, item.question, pr.answer, pr.context_raw, "", "", ""])

    path_p = out_dir / "manual_review_precision_r3.csv"
    with path_p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        header = [
            "id", "question",
            "chunk_1", "chunk_1_relevant",
            "chunk_2", "chunk_2_relevant",
            "chunk_3", "chunk_3_relevant",
            "chunk_4", "chunk_4_relevant",
            "chunk_5", "chunk_5_relevant",
            "expert_notes",
        ]
        writer.writerow(header)
        for item in dataset:
            if item.id not in sample_50:
                continue
            pr = pipeline_results[item.id]
            chunks = (pr.context_chunks + [""] * 5)[:5]
            row = [item.id, item.question]
            for ch in chunks:
                row.extend([ch, ""])
            row.append("")
            writer.writerow(row)

    path_r = out_dir / "manual_review_recall_r4.csv"
    with path_r.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "id",
            "question",
            "ground_truth_answer",
            "key_facts",
            "top10_context_chunks",
            "all_relevant_present_binary",
            "expert_notes",
        ])
        for item in dataset:
            if item.id not in sample_50:
                continue
            pr = pipeline_results[item.id]
            writer.writerow([
                item.id,
                item.question,
                item.ground_truth_answer,
                json.dumps(item.key_facts or [], ensure_ascii=False),
                json.dumps(pr.context_chunks[:10], ensure_ascii=False),
                "",
                "",
            ])

def compute_summary(
    judged_results: List[JudgedResult],
    pipeline_results_by_id: Optional[Dict[str, PipelineResult]] = None,
    exclude_fallback_from_scores: bool = False,
    exclude_medical_refusal_from_scores: bool = False,
    exclude_grounding_blocked_from_scores: bool = False,
) -> Dict[str, Any]:
    fallback_phrase = "i don't have the necessary information on that subject at the moment"

    def is_excluded(r: JudgedResult) -> bool:
        if r.error:
            return True
        if not exclude_fallback_from_scores and not exclude_medical_refusal_from_scores and not exclude_grounding_blocked_from_scores:
            return False
        pr = pipeline_results_by_id.get(r.id) if pipeline_results_by_id else None
        body = (pr.raw_body or {}) if pr else {}
        msg = normalize_text(body.get("message", ""))
        grounding_action = normalize_text(body.get("grounding_action", ""))
        ans = normalize_text(r.answer).lower()
        if exclude_fallback_from_scores and fallback_phrase in ans:
            return True
        if exclude_medical_refusal_from_scores and msg == "Medical_Diagnosis_Interpretation":
            return True
        if exclude_grounding_blocked_from_scores and grounding_action == "BLOCKED":
            return True
        return False

    usable = [r for r in judged_results if not r.error]
    scored = [r for r in usable if not is_excluded(r)]

    latencies = [r.latency_ms for r in usable if r.latency_ms is not None]
    faith_scores = [r.faithfulness_score for r in scored if r.faithfulness_score is not None]
    quality_scores = [r.quality_score_1_5 for r in scored if r.quality_score_1_5 is not None]
    empathy_scores = [r.empathy_score_1_5 for r in scored if r.empathy_score_1_5 is not None]
    precision_scores = [r.precision_at_5_embedding for r in scored if r.precision_at_5_embedding is not None]
    recall_scores_embed = [r.recall_at_10_embedding for r in scored if r.recall_at_10_embedding is not None]
    recall_scores_judge = [r.recall_judge_score for r in scored if r.recall_judge_score is not None]
    source_doc_hits = [r.source_doc_hit_at_10 for r in scored if r.source_doc_hit_at_10 is not None]

    return {
        "n_total": len(judged_results),
        "n_usable": len(usable),
        "n_errors": len([r for r in judged_results if r.error]),
        "n_scored_for_r2_r5": len(scored),
        "excluded_from_r2_r5": len(usable) - len(scored),
        "R1_latency": {
            "threshold_seconds": DEFAULT_LATENCY_SLO_SECONDS,
            "avg_ms": statistics.mean(latencies) if latencies else None,
            "median_ms": statistics.median(latencies) if latencies else None,
            "p95_ms": percentile(latencies, 95) if latencies else None,
            "max_ms": max(latencies) if latencies else None,
            "pass_rate": (
                sum(1 for r in usable if r.latency_pass_r1 is True) / len(usable)
                if usable else None
            ),
            "requirement_met_all": all((r.latency_pass_r1 is True) for r in usable) if usable else None,
        },
        "R2_faithfulness": {
            "threshold": 0.85,
            "avg_score": statistics.mean(faith_scores) if faith_scores else None,
            "pass_rate": (
                sum(1 for r in scored if r.faithfulness_pass is True) / len(scored)
                if scored else None
            ),
            "requirement_met_avg": (statistics.mean(faith_scores) >= 0.85) if faith_scores else None,
        },
        "R3_precision_at_5_embedding_proxy": {
            "threshold": 0.80,
            "avg_score": statistics.mean(precision_scores) if precision_scores else None,
            "requirement_met_avg": (statistics.mean(precision_scores) >= 0.80) if precision_scores else None,
        },
        "R4_recall_at_10": {
            "threshold": 0.80,
            "avg_judge_score": statistics.mean(recall_scores_judge) if recall_scores_judge else None,
            "avg_embedding_proxy": statistics.mean(recall_scores_embed) if recall_scores_embed else None,
            "source_doc_hit_rate_at_10": (sum(1 for x in source_doc_hits if x) / len(source_doc_hits)) if source_doc_hits else None,
            "requirement_met_avg_judge": (statistics.mean(recall_scores_judge) >= 0.80) if recall_scores_judge else None,
            "requirement_met_avg_embedding_proxy": (statistics.mean(recall_scores_embed) >= 0.80) if recall_scores_embed else None,
            "requirement_met_source_doc_hit_rate": ((sum(1 for x in source_doc_hits if x) / len(source_doc_hits)) >= 0.80) if source_doc_hits else None,
        },
        "R5_empathy": {
            "threshold_avg": 4.0,
            "avg_score": statistics.mean(empathy_scores) if empathy_scores else None,
            "requirement_met_avg": (statistics.mean(empathy_scores) >= 4.0) if empathy_scores else None,
            "score_4_or_5_rate": (
                sum(1 for x in empathy_scores if x >= 4) / len(empathy_scores)
                if empathy_scores else None
            ),
        },
        "quality_score_1_5": {
            "avg_score": statistics.mean(quality_scores) if quality_scores else None,
            "score_4_or_5_rate": (
                sum(1 for x in quality_scores if x >= 4) / len(quality_scores)
                if quality_scores else None
            ),
        },
    }


def run_prechecks(
    clients: AwsEvalClients,
    function_name: str,
    judge_model_id: str,
    embed_model_id: str,
    invoke_mode: str = DEFAULT_INVOKE_MODE,
    api_url: str = DEFAULT_INVOKE_AGENT_API_URL,
    api_headers: Optional[Dict[str, str]] = None,
    api_timeout_seconds: int = DEFAULT_API_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    checks = {
        "judge_model_ok": False,
        "embedding_model_ok": False,
        "lambda_ok": False,
        "notes": [],
    }

    try:
        resp = clients.converse_json(
            model_id=judge_model_id,
            system_prompt="Return JSON only.",
            user_prompt='Return exactly {"ok": true}',
            temperature=0.0,
            max_tokens=50,
        )
        if resp.get("ok") is True:
            checks["judge_model_ok"] = True
    except Exception as e:
        checks["notes"].append(f"Judge model check failed: {e}")

    try:
        emb = clients.embed_text(embed_model_id, "hello world")
        if isinstance(emb, list) and len(emb) > 0:
            checks["embedding_model_ok"] = True
    except Exception as e:
        checks["notes"].append(f"Embedding model check failed: {e}")

    try:
        probe = DatasetItem(id="smoke", question="Hello, how can you help a dementia caregiver?")
        pr = invoke_single_question(
            clients,
            function_name,
            probe,
            invoke_mode=invoke_mode,
            api_url=api_url,
            api_headers=api_headers,
            api_timeout_seconds=api_timeout_seconds,
        )
        if pr.error:
            checks["notes"].append(f"Lambda smoke failed: {pr.error}")
        elif not pr.answer:
            checks["notes"].append("Lambda smoke returned no answer.")
        else:
            checks["lambda_ok"] = True
            if not pr.context_raw:
                checks["notes"].append(
                    "Lambda smoke returned no extracted retrieval context. Faithfulness/precision/recall evaluation may be weak for responses without attribution references."
                )
    except Exception as e:
        checks["notes"].append(f"Lambda smoke check exception: {e}")

    return checks



def generate_dataset_from_kb(
    clients: AwsEvalClients,
    kb_id: str,
    generator_model_id: str,
    out_path: Path,
    n_questions: int = 30,
    seed_topics: Optional[List[str]] = None,
    top_k_per_topic: int = 4,
) -> List[DatasetItem]:
    if not kb_id:
        raise ValueError("BEDROCK_KB_ID / --kb-id is required for dataset generation.")
    topics = seed_topics or [
        "sundowning agitation dementia caregiver",
        "wandering home safety dementia",
        "bathing hygiene resistance dementia",
        "sleep issues dementia caregiver",
        "nutrition dehydration dementia",
        "caregiver burnout support dementia",
        "communication repetitive questions dementia",
        "hallucinations paranoia dementia caregiver",
    ]
    snippets = []
    for topic in topics:
        results = clients.retrieve_kb_chunks(kb_id, topic, top_k=top_k_per_topic)
        for r in results:
            txt = normalize_text(r.get("text", ""))
            if not txt:
                continue
            meta = r.get("metadata", {}) or {}
            src = normalize_text(meta.get("source_url", meta.get("x-amz-bedrock-kb-chunk-id", "")))
            snippets.append(f"SOURCE: {src}\nTEXT: {txt[:1600]}")
    snippets = _dedupe_preserve_order(snippets)[:25]
    if not snippets:
        raise ValueError("Could not retrieve any KB snippets for dataset generation.")
    payload = "\n\n====\n\n".join(snippets)
    gen = clients.converse_json(
        model_id=generator_model_id,
        system_prompt=DATASET_GEN_SYSTEM,
        user_prompt=dataset_generation_user_prompt(payload, n_questions=n_questions),
        temperature=0.2,
        max_tokens=4000,
    )
    items_json = gen.get("items", []) if isinstance(gen, dict) else []
    if not isinstance(items_json, list) or not items_json:
        raise ValueError("Generator model did not return dataset items.")
    out_path.write_text(json.dumps({"items": items_json}, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_dataset(out_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Full pipeline evaluator for AWS RAG chatbot")
    parser.add_argument("--dataset", required=True, help="Path to dataset CSV or JSON")
    parser.add_argument("--out-dir", default=f"eval_outputs/run_{utc_now_str()}", help="Output directory")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--lambda-name", default=DEFAULT_LAMBDA_NAME)
    parser.add_argument("--invoke-mode", choices=["lambda", "api"], default=DEFAULT_INVOKE_MODE, help="Whether to invoke the backend through direct Lambda invoke or the exposed HTTP API")
    parser.add_argument("--api-url", default=DEFAULT_INVOKE_AGENT_API_URL, help="Base exposed API URL used when --invoke-mode api")
    parser.add_argument("--api-timeout-seconds", type=int, default=DEFAULT_API_TIMEOUT_SECONDS, help="HTTP timeout for API mode")
    parser.add_argument("--api-header", action="append", default=[], help="Extra HTTP header for API mode in the form Key: Value. Can be repeated.")
    parser.add_argument("--judge-model-id", default=DEFAULT_JUDGE_MODEL_ID)
    parser.add_argument("--embed-model-id", default=DEFAULT_EMBED_MODEL_ID)
    parser.add_argument("--max-workers", type=int, default=DEFAULT_MAX_WORKERS)
    parser.add_argument("--relevance-threshold", type=float, default=DEFAULT_RELEVANCE_THRESHOLD)
    parser.add_argument("--skip-judge", action="store_true", help="Only run pipeline + latency, skip judging")
    parser.add_argument("--sample-size", type=int, default=0, help="If >0, randomly sample this many questions")
    parser.add_argument("--include-cloudwatch", action="store_true", help="Fetch recent CloudWatch Lambda metrics")
    parser.add_argument("--kb-id", default=DEFAULT_KB_ID, help="Bedrock Knowledge Base ID for dataset generation or retrieval fallback")
    parser.add_argument("--fallback-retrieve-top-k", type=int, default=0, help="If pipeline returns no context, retrieve top-k chunks directly from the KB for proxy metrics")
    parser.add_argument("--generator-model-id", default=DEFAULT_GENERATOR_MODEL_ID, help="Model ID used for dataset generation")
    parser.add_argument("--generate-dataset-out", default="", help="If set, generate a dataset JSON from KB material and write it here before evaluation")
    parser.add_argument("--generate-dataset-size", type=int, default=30, help="Number of generated questions when using --generate-dataset-out")
    parser.add_argument("--exclude-fallback-from-scores", action="store_true", help="Exclude fallback responses from R2-R5 and quality scoring. R1 latency still uses all successful responses.")
    parser.add_argument("--exclude-medical-refusal-from-scores", action="store_true", help="Exclude medical-refusal responses from R2-R5 and quality scoring.")
    parser.add_argument("--exclude-grounding-blocked-from-scores", action="store_true", help="Exclude grounding-blocked responses from R2-R5 and quality scoring.")
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    out_dir = Path(args.out_dir)
    safe_mkdir(out_dir)

    clients = AwsEvalClients(args.region)

    api_headers: Dict[str, str] = {}
    for raw_header in args.api_header:
        if ":" not in raw_header:
            raise ValueError(f"Invalid --api-header value: {raw_header}. Expected 'Key: Value'.")
        key, value = raw_header.split(":", 1)
        api_headers[key.strip()] = value.strip()

    if args.generate_dataset_out:
        generated_path = Path(args.generate_dataset_out)
        dataset = generate_dataset_from_kb(
            clients=clients,
            kb_id=args.kb_id,
            generator_model_id=args.generator_model_id,
            out_path=generated_path,
            n_questions=args.generate_dataset_size,
        )
        dataset_path = generated_path
    else:
        dataset = load_dataset(dataset_path)
    if args.sample_size and args.sample_size > 0:
        random.seed(42)
        dataset = random.sample(dataset, min(args.sample_size, len(dataset)))

    (out_dir / "dataset_used.json").write_text(
        json.dumps([asdict(x) for x in dataset], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    prechecks = run_prechecks(
        clients=clients,
        function_name=args.lambda_name,
        judge_model_id=args.judge_model_id,
        embed_model_id=args.embed_model_id,
        invoke_mode=args.invoke_mode,
        api_url=args.api_url,
        api_headers=api_headers,
        api_timeout_seconds=args.api_timeout_seconds,
    )
    (out_dir / "prechecks.json").write_text(pretty_json(prechecks), encoding="utf-8")
    print("Prechecks:", pretty_json(prechecks))

    pipeline_results_by_id: Dict[str, PipelineResult] = {}
    pipeline_jsonl_path = out_dir / "pipeline_results.jsonl"

    with pipeline_jsonl_path.open("w", encoding="utf-8") as f_out:
        with ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = {
                executor.submit(
                    invoke_single_question,
                    clients,
                    args.lambda_name,
                    item,
                    args.kb_id,
                    args.fallback_retrieve_top_k,
                    args.invoke_mode,
                    args.api_url,
                    api_headers,
                    args.api_timeout_seconds,
                ): item
                for item in dataset
            }
            for future in as_completed(futures):
                item = futures[future]
                result = future.result()
                pipeline_results_by_id[item.id] = result
                f_out.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
                status = "OK" if not result.error else "ERR"
                latency_str = f"{result.latency_ms:.1f}" if result.latency_ms is not None else "NA"
                print(f"[pipeline {status}] {item.id} latency_ms={latency_str} ctx_chunks={len(result.context_chunks)} error={result.error}")

    write_manual_review_templates(out_dir, dataset, pipeline_results_by_id)

    judged_results: List[JudgedResult] = []
    if args.skip_judge:
        judged_results = [
            JudgedResult(
                id=pr.id,
                question=pr.question,
                latency_ms=pr.latency_ms,
                answer=pr.answer,
                context_raw=pr.context_raw,
                context_source=normalize_text(pr.raw_body.get("__context_source__", "none")),
                faithfulness_score=None,
                faithfulness_pass=None,
                faithfulness_supported_claim_ratio=None,
                quality_score_1_5=None,
                empathy_score_1_5=None,
                recall_judge_score=None,
                recall_judge_pass=None,
                source_doc_hit_at_10=None,
                source_doc_match_count=None,
                precision_at_5_embedding=None,
                recall_at_10_embedding=None,
                latency_pass_r1=(pr.latency_ms is not None and pr.latency_ms / 1000.0 <= DEFAULT_LATENCY_SLO_SECONDS),
                error=pr.error,
            )
            for pr in pipeline_results_by_id.values()
        ]
    else:
        judged_jsonl_path = out_dir / "judged_results.jsonl"
        with judged_jsonl_path.open("w", encoding="utf-8") as f_out:
            for item in dataset:
                pr = pipeline_results_by_id[item.id]
                jr = judge_single_result(
                    clients=clients,
                    judge_model_id=args.judge_model_id,
                    embed_model_id=args.embed_model_id,
                    item=item,
                    pipeline_result=pr,
                    relevance_threshold=args.relevance_threshold,
                )
                judged_results.append(jr)
                f_out.write(json.dumps(asdict(jr), ensure_ascii=False) + "\n")
                status = "OK" if not jr.error else "ERR"
                print(f"[judge {status}] {item.id} faith={jr.faithfulness_score} quality={jr.quality_score_1_5} empathy={jr.empathy_score_1_5}")

    csv_path = out_dir / "judged_results.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(asdict(judged_results[0]).keys()) if judged_results else [])
        if judged_results:
            writer.writeheader()
            for row in judged_results:
                writer.writerow(asdict(row))

    summary = compute_summary(
        judged_results,
        pipeline_results_by_id=pipeline_results_by_id,
        exclude_fallback_from_scores=args.exclude_fallback_from_scores,
        exclude_medical_refusal_from_scores=args.exclude_medical_refusal_from_scores,
        exclude_grounding_blocked_from_scores=args.exclude_grounding_blocked_from_scores,
    )
    pipeline_outcomes = {
        "allowed_count": 0,
        "medical_refusal_count": 0,
        "grounding_blocked_count": 0,
        "fallback_response_count": 0,
        "with_context_count": 0,
        "kb_retrieve_fallback_context_count": 0,
        "pipeline_context_count": 0,
        "no_context_count": 0,
    }
    fallback_phrase = "i don't have the necessary information on that subject at the moment"
    for pr in pipeline_results_by_id.values():
        body = pr.raw_body or {}
        msg = normalize_text(body.get("message", ""))
        if msg == "Allowed":
            pipeline_outcomes["allowed_count"] += 1
        if msg == "Medical_Diagnosis_Interpretation":
            pipeline_outcomes["medical_refusal_count"] += 1
        if normalize_text(body.get("grounding_action", "")) == "BLOCKED":
            pipeline_outcomes["grounding_blocked_count"] += 1
        if fallback_phrase in normalize_text(pr.answer).lower():
            pipeline_outcomes["fallback_response_count"] += 1
        if pr.context_chunks:
            pipeline_outcomes["with_context_count"] += 1
        if normalize_text(body.get("__context_source__", "")) == "kb_retrieve_fallback":
            pipeline_outcomes["kb_retrieve_fallback_context_count"] += 1
    summary["pipeline_outcomes"] = pipeline_outcomes
    summary["scoring_scope"] = {
        "invoke_mode": args.invoke_mode,
        "api_url": args.api_url if args.invoke_mode == "api" else "",
        "latency_r1_uses_all_successful_responses": True,
        "exclude_fallback_from_scores": args.exclude_fallback_from_scores,
        "exclude_medical_refusal_from_scores": args.exclude_medical_refusal_from_scores,
        "exclude_grounding_blocked_from_scores": args.exclude_grounding_blocked_from_scores,
        "note": "R2, R3, R4, R5 and quality can be restricted to non-fallback / non-refusal / non-grounding-blocked responses when the corresponding flags are enabled. R1 latency always uses all successful responses."
    }

    if args.include_cloudwatch:
        try:
            summary["cloudwatch_lambda_metrics_recent"] = clients.get_lambda_metrics(args.lambda_name, hours_back=2)
        except Exception as e:
            summary["cloudwatch_lambda_metrics_recent_error"] = str(e)

    summary_path = out_dir / "summary.json"
    summary_path.write_text(pretty_json(summary), encoding="utf-8")

    print("\nDone.")
    print(f"Outputs saved to: {out_dir}")
    print(pretty_json(summary))


if __name__ == "__main__":
    main()
