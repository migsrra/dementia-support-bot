import boto3
import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

# ---------------- CONFIG ---------------- #

LAMBDA_NAME = "invokeAgentLambda"
REGION = "us-east-1"

MAX_WORKERS = 10   # safe concurrency for Lambda testing

LOG_FILE = "log2.txt"

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
    accuracy = (TP + TN) / (TP + TN + FP + FN)
    F1 = (2*recall*precision)/(precision+recall) if (precision+recall) > 0 else 0
    FNR = FN / (FN + TP) if (FN + TP) > 0 else 0

    return accuracy, precision, recall, F1, FNR

# ---------------- LOAD PROMPTS ---------------- #

with open("prompts.json") as f:
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

    payload = {
        "pathParameters": {"sessionID": unique_session_id},
        "body": json.dumps({"inputText": prompt})
    }

    try:
        response = lambda_client.invoke(
            FunctionName=LAMBDA_NAME,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload)
        )

        result = json.loads(response["Payload"].read())

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

    accuracy, precision, recall, F1, FNR = compute_metrics(confusion_matrix[category])

    logger.info(f"\nCategory: {category}")
    logger.info(f"Confusion Matrix: {confusion_matrix[category]}")
    logger.info(f"False Negative Rate: {round(FNR,3)}")
    logger.info(f"Precision: {round(precision,3)}")
    logger.info(f"Recall: {round(recall,3)}")
    logger.info(f"F1: {round(F1,3)}")
    logger.info(f"Accuracy: {round(accuracy,3)}")

print(f"Evaluation complete. Results saved to {LOG_FILE}")