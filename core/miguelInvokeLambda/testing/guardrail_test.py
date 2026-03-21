import boto3
import json
import logging
import uuid
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------- CONFIG ---------------- #

LAMBDA_NAME = "miguelInvokeLambda"
REGION = "us-east-1"

MAX_WORKERS = 8   # safe concurrency for Lambda testing

INPUT_FILE = "prompts_reduced.json"
LOG_FILE = "guardrail_V23_reduced.txt"

# ---------------- LOGGING SETUP ---------------- #
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(message)s",
)

logger = logging.getLogger()

# ---------------- AWS CLIENT ---------------- #

lambda_client = boto3.client("lambda", region_name=REGION)

# ---------------- METRICS ---------------- #

def compute_metrics(cm):
    TP = cm["TP"]
    FP = cm["FP"]
    FN = cm["FN"]
    TN = cm["TN"]

    precision = TP / (TP + FP) if (TP + FP) > 0 else 0
    recall = TP / (TP + FN) if (TP + FN) > 0 else 0
    F1 = (2*recall*precision)/(precision+recall) if (precision+recall) > 0 else 0
    
    tpr = TP / (TP + FN) if (TP + FN) > 0 else 0  # True Positive Rate (Recall)
    tnr = TN / (TN + FP) if (TN + FP) > 0 else 0  # True Negative Rate (Specificity)
    fpr = FP / (FP + TN) if (FP + TN) > 0 else 0  # False Positive Rate
    fnr = FN / (FN + TP) if (FN + TP) > 0 else 0  # False Negative Rate

    return precision, recall, F1, tpr, tnr, fpr, fnr

# ---------------- LOAD PROMPTS ---------------- #

with open(INPUT_FILE) as f:
    data = json.load(f)

tests = []

for group in data:
    category = group["category"]

    for prompt in group["prompts"]:
        tests.append({
            "prompt": prompt,
            "category": category
        })

CATEGORIES = [group["category"] for group in data]
print(CATEGORIES)

# ---------------- LAMBDA CALL ---------------- #

def test_prompt(test, index):

    prompt = test["prompt"]
    true_category = test["category"]

    unique_session_id = str(uuid.uuid4())
    API_URL = f"https://a1lzkcfd1h.execute-api.us-east-1.amazonaws.com/api/get-response/{unique_session_id}"


    try:
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

        if "body" in result:
            body = json.loads(result["body"])
        else:
            body = result

        predicted_category = body.get("message")

        logger.info(
            f"Prompt {index} | True: {true_category} | Predicted: {predicted_category} | Prompt: {prompt}"
        )

        return true_category, predicted_category

    except Exception as e:
        logger.error(f"Prompt {index} failed: {e}")
        return true_category, None

# ---------------- RUN TESTS IN PARALLEL ---------------- #

results = []

with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:

    futures = [
        executor.submit(test_prompt, test, i + 1)
        for i, test in enumerate(tests)
    ]

    for future in as_completed(futures):
        results.append(future.result())

# ---------------- CONFUSION MATRIX ---------------- #

confusion_matrix = {
    category: {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
    for category in CATEGORIES
}

for true_category, predicted_category in results:

    for category in CATEGORIES:

        if predicted_category == category and true_category == category:
            confusion_matrix[category]["TP"] += 1

        elif predicted_category == category and true_category != category:
            confusion_matrix[category]["FP"] += 1

        elif predicted_category != category and true_category == category:
            confusion_matrix[category]["FN"] += 1

        else:
            confusion_matrix[category]["TN"] += 1

# ---------------- METRICS OUTPUT ---------------- #

for category in CATEGORIES:

    precision, recall, F1, tpr, tnr, fpr, fnr = compute_metrics(confusion_matrix[category])

    logger.info(f"\nCategory: {category}")
    logger.info(f"Confusion Matrix: {confusion_matrix[category]}")
    logger.info(f"Matrix Rates: 'TPR': {round(tpr,3)}, 'FPR': {round(fpr,3)}, 'FNR': {round(fnr,3)}, 'TNR': {round(tnr,3)}")
    logger.info(f"Precision: {round(precision,3)}")
    logger.info(f"Recall: {round(recall,3)}")
    logger.info(f"F1: {round(F1,3)}")

print(f"Evaluation complete. Results saved to {LOG_FILE}")