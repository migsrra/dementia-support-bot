# import json
# import re
# from collections import defaultdict

# INPUT_FILE = "agent_results4.json"
# OUTPUT_FILE = "agent_report41.txt"

# def extract_from_raw(raw_text):
#     """Bypasses JSON parsing to extract metrics using Regex patterns."""
#     if not raw_text or raw_text == "N/A":
#         return None
        
#     extracted = {"scores": {}, "override_status": "Unknown", "grounding_validation": "N/A"}
    
#     # 1. Extract Override Status
#     status_match = re.search(r'"override_status":\s*"([^"]+)"', raw_text)
#     if status_match:
#         extracted["override_status"] = status_match.group(1)
        
#     # 2. Extract Grounding Validation
#     ground_match = re.search(r'"grounding_validation":\s*"([^"]+)"', raw_text)
#     if ground_match:
#         extracted["grounding_validation"] = ground_match.group(1)

#     # 3. Extract Scores (Handles both 0-2 and 0.0-1.0 scales)
#     metrics = ["empathy", "safety", "groundedness", "relevance"]
#     for m in metrics:
#         # Looks for "metric": number
#         score_match = re.search(fr'"{m}":\s*([\d\.]+)', raw_text)
#         if score_match:
#             extracted["scores"][m] = float(score_match.group(1))
            
#     return extracted

# def generate_report(input_file, report_file):
#     with open(input_file, 'r') as f:
#         data = json.load(f)

#     stats = defaultdict(lambda: {
#         "count": 0, 
#         "scores": defaultdict(float), 
#         "overrides": defaultdict(int),
#         "block_types": defaultdict(int)
#     })

#     for entry in data:
#         cat = entry.get("true_category", "Unknown")
#         eval_data = entry.get("evaluation", {})
        
#         # If the evaluation has an error, use our Regex Extractor on the raw_response
#         if "error" in eval_data or eval_data.get("override_status") == "Evaluation Error":
#             raw_res = eval_data.get("raw_response", "")
#             repaired = extract_from_raw(raw_res)
#             if repaired:
#                 eval_data = repaired 

#         stats[cat]["count"] += 1
        
#         # Aggregate Scores
#         for metric, value in eval_data.get("scores", {}).items():
#             stats[cat]["scores"][metric] += float(value)
        
#         # Aggregate Statuses
#         status = eval_data.get("override_status", "Unknown")
#         stats[cat]["overrides"][status] += 1
        
#         g_val = eval_data.get("grounding_validation", "N/A")
#         stats[cat]["block_types"][g_val] += 1

#     # --- Print/Save Logic ---
#     with open(report_file, 'w', encoding='utf-8') as f:
#         def log(text):
#             print(text); f.write(text + "\n")
            
#         log("="*60)
#         log("DEMENTIA BOT EVALUATION SUMMARY (Regex-Extracted)")
#         log("="*60)

#         log("")
#         log("LOGIC DEFINITIONS")
#         log("Informational Gap: guardrail allows + agent allows + kb not enough info -> data failure, not system")
#         log("Correct Adherence: guardrail correct + agent correct + kb has info -> proper alignment of everything")
#         log("Good override:     guardrail incorrect + agent corrected it -> good agent fix")
#         log("Missed override:   guardrail incorrect + agent incorrect -> double failure, BAD, decrease constraints")
#         log("Bad override:      guardrail correct + agent incorrect -> agent over-confident, rogue, tighten constraints")
#         log("Unnecessary/incorrect block: grounding blocking when not necessary -> lower grounding threshold")
#         log("PRIORITY: Missed, bad, unnecessary block, information block")
#         log("")

#         for cat, d in stats.items():
#             count = d["count"]
#             log(f"\nCATEGORY: {cat} ({count} cases)")
#             log("-" * 30)
#             log("Metric Averages:")
#             for metric, total in d["scores"].items():
#                 log(f"  - {metric.capitalize()}: {total/count:.2f}")
            
#             log("\nLogic Status:")
#             for status, s_count in d["overrides"].items():
#                 pct = (s_count / count) * 100
#                 log(f"  - {status}: {s_count} ({pct:.1f}%)")

# if __name__ == "__main__":
#     # Ensure these paths match your environment
#     generate_report(INPUT_FILE, OUTPUT_FILE)


import json
import re
from collections import defaultdict

INPUT_FILE = "agent_results_3.json"
OUTPUT_FILE = "agent_report_3.txt"

def extract_from_raw(raw_text):
    """Bypasses JSON parsing to extract metrics using Regex patterns."""
    if not raw_text or raw_text == "N/A":
        return None
        
    extracted = {"scores": {}, "override_status": "Unknown", "grounding_validation": "N/A"}
    
    status_match = re.search(r'"override_status":\s*"([^"]+)"', raw_text)
    if status_match:
        extracted["override_status"] = status_match.group(1)
        
    ground_match = re.search(r'"grounding_validation":\s*"([^"]+)"', raw_text)
    if ground_match:
        extracted["grounding_validation"] = ground_match.group(1)

    metrics = ["empathy", "safety", "groundedness", "relevance"]
    for m in metrics:
        score_match = re.search(fr'"{m}":\s*([\d\.]+)', raw_text)
        if score_match:
            extracted["scores"][m] = float(score_match.group(1))
            
    return extracted

def generate_report(input_file, report_file):
    with open(input_file, 'r') as f:
        data = json.load(f)

    stats = defaultdict(lambda: {
        "count": 0, 
        "scores": defaultdict(float), 
        "overrides": defaultdict(int),
        "block_types": defaultdict(int)
    })

    total_incorrect_blocks = 0

    for entry in data:
        cat = entry.get("true_category", "Unknown")
        eval_data = entry.get("evaluation", {})
        
        if "error" in eval_data or eval_data.get("override_status") == "Evaluation Error":
            raw_res = eval_data.get("raw_response", "")
            repaired = extract_from_raw(raw_res)
            if repaired:
                eval_data = repaired 

        stats[cat]["count"] += 1
        
        for metric, value in eval_data.get("scores", {}).items():
            stats[cat]["scores"][metric] += float(value)
        
        status = eval_data.get("override_status", "Unknown")
        stats[cat]["overrides"][status] += 1
        
        g_val = eval_data.get("grounding_validation", "N/A")
        stats[cat]["block_types"][g_val] += 1
        
        # Track global count for the final footer alert
        if g_val in ["Incorrect Ground Block", "Unnecessary Ground Block"]:
            total_incorrect_blocks += 1

    with open(report_file, 'w', encoding='utf-8') as f:
        def log(text):
            print(text); f.write(text + "\n")
            
        log("="*60)
        log("DEMENTIA BOT EVALUATION SUMMARY (Regex-Extracted)")
        log("="*60)

        log("")
        log("LOGIC DEFINITIONS")
        log("Informational Gap: guardrail allows + agent allows + kb not enough info for allowed topic -> data failure")
        log("Correct Adherence: guardrail correct + agent correct + kb has info -> proper alignment")
        log("Good override:     guardrail incorrect + agent corrected it -> good agent fix")
        log("Missed override:   guardrail incorrect + agent incorrect -> double failure (Safety Risk)")
        log("Bad override:      guardrail correct + agent incorrect -> rogue agent (Safety Risk)")
        log("PRIORITY: Missed, bad, unnecessary block, information block")
        log("")

        for cat, d in stats.items():
            count = d["count"]
            log(f"\nCATEGORY: {cat} ({count} cases)")
            log("-" * 30)
            log("Metric Averages:")
            for metric, total in d["scores"].items():
                log(f"  - {metric.capitalize()}: {total/count:.2f}")
            
            log("\nLogic Status:")
            for status, s_count in d["overrides"].items():
                pct = (s_count / count) * 100
                log(f"  - {status}: {s_count} ({pct:.1f}%)")

            # --- ADDED ALERTS SECTION ---
            bad_blocks = d["block_types"].get("Incorrect Ground Block", 0) + \
                         d["block_types"].get("Unnecessary Ground Block", 0)
            if bad_blocks > 0:
                log(f"  [!] ALERT: {bad_blocks} unnecessary/incorrect blocks detected in this category.")

        log("\n" + "="*60)
        if total_incorrect_blocks > 0:
            log(f"FINAL AUDIT: {total_incorrect_blocks} TOTAL FALSE POSITIVES (Incorrect Blocks).")
            log("ACTION: Consider lowering grounding threshold or improving RAG retrieval.")
        else:
            log("FINAL AUDIT: No unnecessary blocks detected. Grounding system is highly precise.")
        log("="*60)

if __name__ == "__main__":
    generate_report(INPUT_FILE, OUTPUT_FILE)