import json
import boto3
import logging
import os
import uuid
from datetime import datetime, timezone
import re
from textwrap import dedent

logger = logging.getLogger()
logger.setLevel(logging.INFO)


AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")


ALLOWED_AGENT_ID = os.getenv("ALLOWED_AGENT_ID")
ALLOWED_AGENT_ALIAS = os.getenv("ALLOWED_AGENT_ALIAS")

HARM_AGENT_ID = os.getenv("HARM_AGENT_ID")
HARM_AGENT_ALIAS = os.getenv("HARM_AGENT_ALIAS")

LEGAL_AGENT_ID = os.getenv("LEGAL_AGENT_ID")
LEGAL_AGENT_ALIAS= os.getenv("LEGAL_AGENT_ALIAS")

DOSAGE_DIAGNOSIS_AGENT_ID = os.getenv("DOSAGE_DIAGNOSIS_AGENT_ID")
DOSAGE_DIAGNOSIS_AGENT_ALIAS = os.getenv("DOSAGE_DIAGNOSIS_AGENT_ALIAS")

NON_RELATED_AGENT_ID = os.getenv("NON_RELATED_AGENT_ID")
NON_RELATED_AGENT_ALIAS = os.getenv("NON_RELATED_AGENT_ALIAS")

DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME")

MAID_EUTHANASIA_TEMPLATE = dedent(
    """
    I'm not able to respond to this request, please consult your physician.

    I am here to help with general dementia caregiving strategies, tips for daily routines, or behavioural support.
    
    Would you like guidance on any of those topics?
    """
).strip()

PROMPT_ATTACK_TEMPLATE = dedent(
    """
    I’m here to support questions specifically related to dementia caregiving. I’m not able to provide guidance outside of that scope.

    If you have questions about caring for someone with dementia, daily routines, behavioral management, or support resources, I’d be happy to help.

    Is there a specific caregiving topic you’d like guidance on?
    """
).strip()

UNSUPPORTED_QUERY_TEMPLATE = dedent(
    """
    I don't have the necessary information on that subject at the moment. My knowledge base is constantly improving, so please ask me again at a later date.

    If you have any other questions about caring for someone with dementia, daily routines, behavioral management, or support resources, I’d be happy to help.

    Is there a specific dementia-related topic you’d like guidance on?
    """
).strip()

IRRELEVANT_RESPONSE_TEMPLATE = dedent(
    """
    I am unable to provide a relevant response to this query. Please try again and/or try re-phrasing your question.

    If you have any other questions about caring for someone with dementia, daily routines, behavioral management, or support resources, I’d be happy to help.

    Is there a specific dementia-related topic you’d like guidance on?
    """
).strip()

harm_priority_order = [
    "Self_Harm_High",
    "Patient_Aggression_High",
    "Caregiver_Burnout_High",
    "Self_Harm_Low",
    "Patient_Aggression_Low",
    "Caregiver_Burnout_Low"
]

non_harm_priority_order = [
    "Medication_Dosing_Changes",
    "Medical_Diagnosis_Interpretation",
    "Legal_High_Stakes_Financial_Execution",
    "Dementia_Related"
]

output_checked_topics = [
    "Allowed",
    # "Self_Harm_Low",
    # "Patient_Aggression_Low",
    # "Caregiver_Burnout_Low",
    "Medical_Education_Inquiry",
    # "Caregiver_Burnout_High",
    # "Medication_Dosing_Changes",
    # "Medical_Diagnosis_Interpretation",
    # "Legal_High_Stakes_Financial_Execution",
    "Dementia_Related"
]

greeting_words = ["hi", "hello", "hey", "good morning", "good afternoon", "greetings"]
pattern = r"\b(" + "|".join(greeting_words) + r")\b"

def greeting_check(query):
    if re.search(pattern, query, re.IGNORECASE):
        return True
    else:
        return False
    
def extract_masked_text(guardrail_output):
    # Regex to find anything inside the custom Bedrock tags
    pattern = r"<amazon-bedrock-guardrails-guardContent_[^>]+>(.*?)</amazon-bedrock-guardrails-guardContent_[^>]+>"
    
    match = re.search(pattern, guardrail_output, re.DOTALL)
    if match:
        # Return the clean text inside the tags, stripped of extra whitespace
        return match.group(1).strip()
    
    # Fallback: if no tags found, return the original (or use a cleaner strip)
    return guardrail_output.strip()

def harm_topic(detected_topics):
    for topic in harm_priority_order:        # return highest priority topic if they exist
        if topic in detected_topics:
            return True
    return False

def non_harm_priority_topic(detected_topics):
    for topic in non_harm_priority_order:       # return highest priority non-harm topic
        if topic in detected_topics:
            return topic
    return None

def _build_session():
    logger.info("Building boto3 session")
    if AWS_PROFILE:
        try:
            return boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
        except Exception as exc:
            logger.warning("Falling back to default session: %s", exc)
    return boto3.Session(region_name=AWS_REGION)

def _success_response(status_code, data):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(data),
    }

def _error_response(status_code, message):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"error": message}),
    }


def _agent_for_routing_mode(routing_mode):
    agent_map = {
        "Allowed": (ALLOWED_AGENT_ID, ALLOWED_AGENT_ALIAS),
        "Medical_Education_Inquiry": (ALLOWED_AGENT_ID, ALLOWED_AGENT_ALIAS),
        "Harm_Detected": (HARM_AGENT_ID, HARM_AGENT_ALIAS),
        # "Self_Harm_High": (SELFHARM_HIGH_AGENT_ID, SELFHARM_HIGH_AGENT_ALIAS),
        # "Self_Harm_Low": (SELFHARM_LOW_AGENT_ID, SELFHARM_LOW_AGENT_ALIAS),
        # "Patient_Aggression_High": (PATIENTAGGRESSION_HIGH_AGENT_ID, PATIENTAGGRESSION_HIGH_AGENT_ALIAS),
        # "Patient_Aggression_Low": (PATIENTAGGRESSION_LOW_AGENT_ID, PATIENTAGGRESSION_LOW_AGENT_ALIAS),
        # "Caregiver_Burnout_High": (CAREGIVERBURNOUT_HIGH_AGENT_ID, CAREGIVERBURNOUT_HIGH_AGENT_ALIAS),
        # "Caregiver_Burnout_Low": (CAREGIVERBURNOUT_LOW_AGENT_ID, CAREGIVERBURNOUT_LOW_AGENT_ALIAS),
        "Medication_Dosing_Changes": (DOSAGE_DIAGNOSIS_AGENT_ID, DOSAGE_DIAGNOSIS_AGENT_ALIAS),
        "Medical_Diagnosis_Interpretation": (DOSAGE_DIAGNOSIS_AGENT_ID, DOSAGE_DIAGNOSIS_AGENT_ALIAS),
        "Legal_High_Stakes_Financial_Execution": (LEGAL_AGENT_ID, LEGAL_AGENT_ALIAS),
        "Non_Dementia_Related_Queries": (NON_RELATED_AGENT_ID, NON_RELATED_AGENT_ALIAS),
    }

    return agent_map.get(routing_mode, (None, None))

def lambda_handler(event, context):
    try:
        session = _build_session()
        path_params = event.get("pathParameters") or {}
        session_id = path_params.get("sessionID") if isinstance(path_params, dict) else None
        completion = ""
        orig_response = ""
        attribution = None

        body = event.get("body")
        body_str = body if isinstance(body, str) else None
        print(f"body received as {body_str}")
        print(f"sessionId received as {session_id}")
        if body_str is None:
            logger.error("Missing or poorly formatted query body")
            return _error_response(400, "Missing query input text")
        

        # Guardrail Safety Layer
        bedrock_runtime = session.client("bedrock-runtime")

        GUARDRAIL_ID = os.getenv("GUARDRAIL_ID")
        GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "20")

        # add tag around query to aid guardrail processing
        suffix = str(uuid.uuid4())[:8]
        guardrail_input = f"""
            <amazon-bedrock-guardrails-guardContent_{suffix}>
            {body_str}
            </amazon-bedrock-guardrails-guardContent_{suffix}>
            """

        # Guardrail check
        guardrail_response = bedrock_runtime.apply_guardrail(
            guardrailIdentifier=GUARDRAIL_ID,
            guardrailVersion=GUARDRAIL_VERSION,
            source="INPUT",
            content=[
                {"text": {
                    "text": guardrail_input
                    }
                }
            ]
        )
      
        logger.info(json.dumps(guardrail_response, indent=2))

        routing_mode = "Allowed"
        greeting_query = False      # if greeting, allow and don't check groundedness
        bypass_agent = False
        grounding_score = None
        grounding_action = None
        relevance_score = None
        relevance_action = None
        send_to_db = False

        assessments = guardrail_response.get("assessments", [])

        for assessment in assessments:
            # content filters, check for prompt attack first
            content_policy = assessment.get("contentPolicy", {})
            filters = content_policy.get("filters", [])

            for filter in filters:
                type = filter.get("type")
                if type == "PROMPT_ATTACK":     # hard refusal, bypass agent
                    routing_mode = "Prompt_Attack"
                    completion = PROMPT_ATTACK_TEMPLATE
                    bypass_agent = True
            
            # denied topics
            topic_policy = assessment.get("topicPolicy", {})
            topics = topic_policy.get("topics", [])
            flagged_topics = [item['name'] for item in topics]
            print("topics:", flagged_topics)

            low_priority_topic = non_harm_priority_topic(flagged_topics)       # non-crisis topics only, set routing based on priority

            if "MAID_Euthanasia" in flagged_topics:
                routing_mode = "MAID_Euthanasia"
                completion = MAID_EUTHANASIA_TEMPLATE
                bypass_agent = True
            elif "Harm_Detected" in flagged_topics:
                routing_mode = "Harm_Detected"
            elif flagged_topics:   
                if "Dementia_Related" == low_priority_topic:        # only dementia_related flagged, therefore allowed
                    routing_mode = "Allowed"
                    greeting_query = greeting_check(body_str)     # set greeting flag to guide grounding check later
                elif "Medication_Dosing_Changes" == low_priority_topic or "Medical_Diagnosis_Interpretation" == low_priority_topic:
                    # if also flagged education, verify if it is actually just educational and dementia related
                    word_policy = assessment.get("wordPolicy", {})
                    words = word_policy.get("customWords", [])
                    # print("custom words found:", words)

                    if not words and "Medical_Education_Inquiry" in flagged_topics:    # educational and dementia related      
                        routing_mode = "Medical_Education_Inquiry"
                    else:           # medical advice -> hard refusal
                        routing_mode = low_priority_topic
                elif "Legal_High_Stakes_Financial_Execution" == low_priority_topic:
                    routing_mode = low_priority_topic
                elif not low_priority_topic and "Medical_Education_Inquiry" in flagged_topics and "Dementia_Related" in flagged_topics:  # catch queries that are medical education and related to dementia
                    routing_mode = "Medical_Education_Inquiry"      # education question, not medical advice
                else:
                    routing_mode = "Non_Dementia_Related_Queries"
            else:
                greeting_query = greeting_check(body_str)
                if greeting_query:     # if the query is simply a greeting (so wouldn't trigger dementia-related), allow it
                    routing_mode = "Allowed"            
                else:               # not dementia related nor a crisis or non-crisis topic that is dementia related
                    routing_mode = "Non_Dementia_Related_Queries"   
                    
            sensitiveInformationPolicy = assessment.get("sensitiveInformationPolicy", {})
            piiEntities = sensitiveInformationPolicy.get("piiEntities", [])

            if piiEntities:     # extract masked query and replace original string in query
                raw_text = guardrail_response["outputs"][0]["text"]
                safe_text = extract_masked_text(raw_text)
                body_str = safe_text
            
        print(f"routing mode: {routing_mode}")
        selected_agent_id, selected_agent_alias = _agent_for_routing_mode(routing_mode)


        # Agent TESTING needs
        clean_context = None
        orig_response = completion     # update lower with agent response too

        # Start Bedrock KB ingestion job
        if not session_id:
            logger.error("sessionID not received via path parameter")
            return _error_response(400, "Missing sessionID.")
        elif bypass_agent is False:
            if not selected_agent_id or not selected_agent_alias:
                logger.error(
                    "Missing Bedrock agent config for routing_mode=%s (agent_id=%s, alias=%s)",
                    routing_mode,
                    bool(selected_agent_id),
                    bool(selected_agent_alias),
                )
                return _error_response(500, f"Configuration details missing for routing mode: {routing_mode}")

            try:
                bedrock_client = session.client("bedrock-agent-runtime")
                print("Attempting invocation")

                response = bedrock_client.invoke_agent(
                    agentAliasId=selected_agent_alias,
                    agentId=selected_agent_id,
                    enableTrace=True,
                    inputText=body_str, 
                    sessionId=session_id,
                    streamingConfigurations = { 
                        "streamFinalResponse" : False
                    }
                )
                
                completion = ""
                retrieved_context = ""
                attribution_citations = []
                eventLen = 0

                for event in response.get("completion"):        # compile agent response and retrieved context
                    #Collect agent output.
                    eventLen += 1
                    if 'chunk' in event:
                        chunk = event.get("chunk")
                        completion += chunk["bytes"].decode()

                        chunk_attribution = chunk.get("attribution", {})
                        if chunk_attribution and isinstance(chunk_attribution, dict):
                            citations = chunk_attribution.get("citations", [])
                            if isinstance(citations, list) and len(citations) > 0:
                                attribution_citations.extend(citations)
                                
                                if routing_mode in output_checked_topics:       # only get references if allowed topic to perform grounding and relevance
                                    for cit in citations:
                                        refs = cit.get("retrievedReferences", [])
                                        # print(f"DEBUG: Found {len(refs)} references in this citation")
                                        
                                        for ref in refs:
                                            content = ref.get("content", {})
                                            text = content.get("text", "")
                                            if text:
                                                retrieved_context += f"\n{text}"
                                                # print(f"DEBUG: Added {len(text)} chars to context")
                                        
                        print(f"chunk: {chunk}")
                    
                    # Log trace output.
                    if 'trace' in event:
                        trace_event = event.get("trace")
                        print(f"trace: {trace_event}")
                
                # if names found and masked, remove from response as well
                if " {NAME}" in completion:      
                    completion = completion.replace(" {NAME}", "").strip()
                orig_response = completion      # agent TESTING, response before grounding overwrites

                # Clean up retrieved context, no repeats
                unique_lines = list(dict.fromkeys([line.strip() for line in retrieved_context.split("\n") if line.strip()]))
                clean_context = "\n".join(unique_lines)

                # print("DEBUG RESPONSE", completion)
                print("CONTEXT:", clean_context)       # would only collect references if allowed/low risk topic

                # Grounding and Relevance check
                if clean_context and routing_mode in output_checked_topics and not greeting_query:         # if allowed and not a greeting (normal query)
                    try:
                        guardrail_check = bedrock_runtime.apply_guardrail(
                            guardrailIdentifier=GUARDRAIL_ID,
                            guardrailVersion=GUARDRAIL_VERSION,
                            source="OUTPUT",
                            content=[
                                {
                                    "text": {
                                        "text": body_str, 
                                        "qualifiers": ["query"]
                                    }
                                },
                                {
                                    "text": {
                                        "text": clean_context, 
                                        "qualifiers": ["grounding_source"]
                                    }
                                },
                                {
                                    "text": {
                                        "text": completion, 
                                        "qualifiers": ["guard_content"]
                                    }
                                }
                            ]
                        )
        
                        # Extract specific scores for dementia bot logic
                        for assessment in guardrail_check.get("assessments", []):
                            grounding_policy = assessment.get("contextualGroundingPolicy", {})
                            print(f"assessment result: {grounding_policy}")

                            for filter_obj in grounding_policy.get("filters", []):
                                score = filter_obj.get("score")
                                filter_type = filter_obj.get("type") # 'GROUNDING' or 'RELEVANCE'
                                filter_action = filter_obj.get("action")

                                if filter_type == "GROUNDING":
                                    grounding_score = score
                                    grounding_action = filter_action
                                else:
                                    relevance_score = score
                                    relevance_action = filter_action
                                
                                # Custom threshold logic (optional)
                                if filter_type == "GROUNDING" and grounding_action == "BLOCKED":
                                    # print("Low grounding detected. Agent might be hallucinating.")
                                    send_to_db = True
                                elif filter_type == "RELEVANCE" and relevance_action == "BLOCKED":
                                    # print("Low relevance detected.")
                                    if not send_to_db:     # grounding passed, so set irrelevant template response
                                        completion = IRRELEVANT_RESPONSE_TEMPLATE

                    except Exception as e:
                        print(f"Error calling Guardrail API: {e}")

                if send_to_db and clean_context and routing_mode in output_checked_topics and not greeting_query:       # if did not find references for an allowed topic
                    print("Grounding failed. Forwarding to physician")
                    completion = UNSUPPORTED_QUERY_TEMPLATE
                    send_to_db = True
                    try:
                        if not DYNAMODB_TABLE_NAME:
                            logger.error("DYNAMODB_TABLE_NAME not configured")
                        else:
                            dynamodb_table = session.resource("dynamodb").Table(DYNAMODB_TABLE_NAME)
                            timestamp_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                            dynamodb_table.put_item(
                                Item={
                                    "query_id": str(uuid.uuid4()),
                                    "timestamp": timestamp_utc,
                                    "pk_all": "ALL",
                                    "deleted": False,
                                    "query_text": body_str,
                                }
                            )
                    except Exception as e:
                        logger.error(f"Failed to save unsupported prompt to DynamoDB: {e}")
                    print("prompt sent to db successfully")
                elif not clean_context and routing_mode == "Allowed" and greeting_query:        # for testing that we properly leave greeting cases, no grounding
                    print("query with greeting and nothing else")
            
                # if NO context is found, we are trusting the agent to respond accordingly
                # reason #1: harm categories should not seek advice from the knowledge base, so they wouldn't get context in the first place. the agent responds itself reliably
                # reason #2: for guardrail flags that are in output_checked_topics but didn't find context, the agent reliably says that it doesn't know enough?
                # reason #3: often guardrail flags are wrong, so if incorrectly flagged as output_checked_topics but didn't find context, agent understands thats ok but lambda code can't handle that
            
                print(f"Amount of events: {eventLen}")
                if attribution_citations:
                    attribution = {"citations": attribution_citations}

            except Exception as e:
                logger.error(f"Failed to invoke agent: {e}")
                return _error_response(500, "Failed to invoke Bedrock Agent")

        # response for testing, includes guardrail info for analysis
        response_body = {
            "message": routing_mode,
            "orig_response": orig_response,
            "response": completion,
            "retrieved_context": clean_context,
            "grounding_score": grounding_score,
            "grounding_action": grounding_action,
            "relevance_score": relevance_score,
            "relevance_action": relevance_action
        }

        if send_to_db:
            response_body["sent_for_review"] = True
        elif attribution is not None: # don't want to add whatever irrelevant source to response if grounding failed
            response_body["attribution"] = attribution
        return _success_response(200, response_body)
    
    except Exception as exc:
        logger.info("Unexpected error")
        return _error_response(500, f"Internal server error: {str(exc)}")
