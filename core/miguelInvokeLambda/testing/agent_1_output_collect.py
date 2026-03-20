import requests
import json
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------- CONFIG ---------------- #

MAX_WORKERS = 10
OUTPUT_FILE = "judge_input_fix.json"

# ---------------- LOAD PROMPTS ---------------- #

with open("prompts_small.json") as f:
    data = json.load(f)

tests = []

for group in data:
    category = group["category"]

    for prompt in group["prompts"]:
        tests.append({
            "prompt": prompt,
            "category": category
        })

# ---------------- API CALL ---------------- #

def test_prompt(test, index):
    prompt = test["prompt"]
    true_category = test["category"]

    unique_session_id = str(uuid.uuid4())
    API_URL = f"https://a1lzkcfd1h.execute-api.us-east-1.amazonaws.com/api/get-response/{unique_session_id}"

    try:
        # Match your API Gateway expected format
        payload = prompt   #  depends on your API design

        headers = {
            "Content-Type": "application/json"
        }

        response = requests.post(
            API_URL,
            headers=headers,
            json=payload  # sends JSON body
        )

        result = response.json()

        # If your API wraps response in "body"
        if "body" in result:
            body = json.loads(result["body"])
        else:
            body = result

        output = {
            "prompt": prompt,
            "true_category": true_category,
            "guardrail_category": body.get("message"),
            "orig_response": body.get("orig_response"),
            "response": body.get("response"),
            "retrieved_context": body.get("retrieved_context"),
            # "grounding_score": body.get("grounding_score"),
            "grounding_action": body.get("grounding_action"),
            # "relevance_score": body.get("relevance_score"),
            "relevance_action": body.get("relevance_action")
        }

        print(f"Completed {index}")
        return output

    except Exception as e:
        print(f"Prompt {index} failed: {e}")
        return {
            "prompt": prompt,
            "category": true_category,
            "error": str(e)
        }

# ---------------- RUN TESTS ---------------- #

results = []

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

    futures = [
        executor.submit(test_prompt, test, i + 1)
        for i, test in enumerate(tests)
    ]

    for future in as_completed(futures):
        results.append(future.result())

# ---------------- SAVE OUTPUT ---------------- #

with open(OUTPUT_FILE, "w") as f:
    json.dump(results, f, indent=2)

print(f"Saved {len(results)} results to {OUTPUT_FILE}")
