import json
import boto3
import logging
import os
import uuid
from datetime import datetime, timezone
import uuid
import re
from textwrap import dedent

logger = logging.getLogger()
logger.setLevel(logging.INFO)


AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_AGENT_ID = os.getenv("BEDROCK_AGENT_ID")
BEDROCK_ALIAS_ID = os.getenv("BEDROCK_ALIAS_ID")
DYNAMODB_TABLE_NAME = os.getenv("DYNAMODB_TABLE_NAME")

MAID_EUTHANESIA_TEMPLATE = dedent(
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

NON_DEMENTIA_TEMPLATE = dedent(
    """
    I’m here to support questions specifically related to dementia caregiving. I’m not able to provide guidance outside of that scope.

    If you have questions about caring for someone with dementia, daily routines, behavioral management, or support resources, I’d be happy to help.

    Is there a specific caregiving topic you’d like guidance on?
    """
).strip()

SELF_HARM_TEMPLATE = dedent(
    """
    I am very concerned about your safety. Please know that you are not alone, and help is available right now.

    Please call or text 9-8-8 (the Canadian Suicide Crisis Helpline) to speak with someone who can support you.

    If you are in immediate danger, please call 9-1-1 or go to the nearest emergency room. 
    
    Remember that your life is important, and there is support for you through this difficult time.
    """
).strip()

PATIENT_AGGRESSION_TEMPLATE = dedent(
    """
    Your physical safety is the first priority. If you are in immediate danger, please call 9-1-1 right away.

    If it is safe to do so, give yourself space by moving to another room. Do not argue with the patient as they cannot be reasoned with at the moment.

    Remove triggers to ensure there are no dangerous objects within their reach.
    
    Once you are safe, please contact your doctor or a local crisis team to discuss these behavioral changes.
    """
).strip()

CAREGIVER_BURNOUT_TEMPLATE = dedent(
    """
    It sounds like you are at a breaking point, and I want to make sure both you and your loved one stay safe.

    Ensure the patient is in a safe place and then step away immediate for space.

    I encourage you to reach out to trusted people or the Alzheimer Society of Canada at 1-800-616-8816 or emailing at info@alzheimer.ca for immediate support.
    
    Caring for someone with dementia is incredibly demanding, and reaching out for help right now is the right thing to do to prevent a crisis.
    """
).strip()

MEDICAL_TEMPLATE = dedent(
    """
    I'm sorry but I cannot provide medical diagnoses, interpret test results, or give advice on changing medications. 
    
    These decisions require a clinical assessment by a healthcare professional who knows your specific situation.

    Please contact your family physician for more information. If this is a medical emergency, please call 9-1-1 immediately.
    
    If you have questions about caring for someone with dementia, daily routines, behavioral management, or support resources, I’d be happy to help.
    """
).strip()

LEGAL_FINANCE_TEMPLATE = dedent(
    """
    I cannot provide legal advice or assist with financial transactions and estate planning.

    These matters are legally complex and require professional expertise to ensure the rights and assets of both the caregiver and the person with dementia are protected.

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
    "Self_Harm_Low",
    "Patient_Aggression_Low",
    "Caregiver_Burnout_Low",
    "Medical_Education_Inquiry"
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

def harm_priority_topic(detected_topics):
    for topic in harm_priority_order:        # return highest priority topic if they exist
        if topic in detected_topics:
            return topic
    return None

def high_harm_template_choose(topic):
    if topic == "Self_Harm_High":
        return SELF_HARM_TEMPLATE
    elif topic == "Patient_Aggression_High":
        return PATIENT_AGGRESSION_TEMPLATE
    else:
        return CAREGIVER_BURNOUT_TEMPLATE
    
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

def lambda_handler(event, context):
    try:
        session = _build_session()
        path_params = event.get("pathParameters") or {}
        session_id = path_params.get("sessionID") if isinstance(path_params, dict) else None
        completion = ""
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

        message = "Allowed"
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
                    message = "Prompt Attack"
                    completion = PROMPT_ATTACK_TEMPLATE
                    bypass_agent = True
            
            # denied topics
            topic_policy = assessment.get("topicPolicy", {})
            topics = topic_policy.get("topics", [])
            flagged_topics = [item['name'] for item in topics]
            print("topics:", flagged_topics)

            high_priority_topic = harm_priority_topic(flagged_topics)   # harm topics prioritized
            low_priority_topic = non_harm_priority_topic(flagged_topics)       # non-crisis topics only, set routing based on priority

            if high_priority_topic:
                if high_priority_topic == "Self_Harm_Low" or high_priority_topic == "Patient_Aggression_Low" or high_priority_topic == "Caregiver_Burnout_Low":
                    routing_mode = high_priority_topic
                else:
                    message = high_priority_topic
                    completion = high_harm_template_choose(high_priority_topic)
                    bypass_agent = True
            elif "MAID_Euthanesia" in flagged_topics:
                message = "MAID_Euthanesia"
                completion = MAID_EUTHANESIA_TEMPLATE
                bypass_agent = True
            elif flagged_topics:   
                if "Dementia_Related" == low_priority_topic:        # only dementia_related flagged, therefore allowed
                    routing_mode = "Allowed"
                    greeting_query = greeting_check(body_str)     # set greeting flag to guide grounding check later
                elif "Medication_Dosing_Changes" == low_priority_topic or "Medical_Diagnosis_Interpretation" == low_priority_topic:
                    # if also flagged education, verify if it is actually just educational and dementia related
                    word_policy = assessment.get("wordPolicy", {})
                    words = word_policy.get("customWords", [])
                    print("custom words found:", words)
                    
                    if not words and "Medical_Education_Inquiry" in flagged_topics: # and "Dementia_Related" in flagged_topics:    # educational and dementia related      
                        routing_mode = "Medical_Education_Inquiry"
                    else:           # medical advice -> hard refusal
                        message = low_priority_topic
                        completion = MEDICAL_TEMPLATE
                        bypass_agent = True
                elif "Legal_High_Stakes_Financial_Execution" == low_priority_topic:
                    message = low_priority_topic
                    completion = LEGAL_FINANCE_TEMPLATE
                    bypass_agent = True
                elif not low_priority_topic and "Medical_Education_Inquiry" in flagged_topics and "Dementia_Related" in flagged_topics:  # catch queries that are medical education and related to dementia
                    routing_mode = "Medical_Education_Inquiry"      # education question, not medical advice
                else:
                    message = "Non_Dementia_Related_Queries"     
                    completion = NON_DEMENTIA_TEMPLATE
                    bypass_agent = True
            else:
                greeting_query = greeting_check(body_str)
                if greeting_query:     # if the query is simply a greeting (so wouldn't trigger dementia-related), allow it
                    routing_mode = "Allowed"            
                else:               # not dementia related nor a crisis or non-crisis topic that is dementia related
                    message = "Non_Dementia_Related_Queries"     
                    completion = NON_DEMENTIA_TEMPLATE
                    bypass_agent = True    
        
            # Sensitive info check
            sensitiveInformationPolicy = assessment.get("sensitiveInformationPolicy", {})
            piiEntities = sensitiveInformationPolicy.get("piiEntities", [])

            if piiEntities:     # extract masked query and replace original string in query
                raw_text = guardrail_response["outputs"][0]["text"]
                safe_text = extract_masked_text(raw_text)
                body_str = safe_text
            
        # Setting Routing
        session_state = {
            "sessionAttributes": {
                "routing_mode": routing_mode
            }
        }

        attribution = None

        # Start Bedrock KB ingestion job
        if not BEDROCK_AGENT_ID or not BEDROCK_ALIAS_ID:
            logger.error("BEDROCK_AGENT_ID or BEDROCK_ALIAS_ID not configured")
            return _error_response(500, "Configuration details missing. Failed to invoke agent.")
        elif not session_id:
            logger.error("sessionID not received via path parameter")
            return _error_response(400, "Missing sessionID.")
        elif bypass_agent is False:
            try:
                bedrock_client = session.client("bedrock-agent-runtime")
                print("Attempting invocation")

                response = bedrock_client.invoke_agent(
                    agentAliasId=BEDROCK_ALIAS_ID, 
                    agentId=BEDROCK_AGENT_ID, 
                    enableTrace=True,
                    endSession=False,
                    sessionState=session_state,
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
                for event in response.get("completion"):
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

                # Create a list of lines, strip whitespace, and use a set to unique them
                unique_lines = list(dict.fromkeys([line.strip() for line in retrieved_context.split("\n") if line.strip()]))
                clean_context = "\n".join(unique_lines)

                # print("DEBUG RESPONSE", completion)
                if routing_mode in output_checked_topics:
                    print("DEBUG CONTEXT", clean_context)       # would only collect references if allowed/low risk topic

                # Grounding and Relevance check
                if clean_context and routing_mode in output_checked_topics and not greeting_query:         # if allowed or low risk topic and not a greeting (normal query)
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
                                    print("Low grounding detected. Agent might be hallucinating.")
                                    send_to_db = True
                                elif filter_type == "RELEVANCE" and relevance_action == "BLOCKED":
                                    print("Low relevance detected.")
                                    if not send_to_db:     # grounding passed, so set irrelevant template response
                                        completion = IRRELEVANT_RESPONSE_TEMPLATE

                    except Exception as e:
                        print(f"Error calling Guardrail API: {e}")

                if send_to_db or (not clean_context and routing_mode in output_checked_topics and not greeting_query):       # if did not find references for an allowed/low risk topic
                    print("No references returned, grounding failed. Forwarding to physician")
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
                                    "deleted": False,
                                    "query_text": body_str,
                                }
                            )
                    except Exception as e:
                        logger.error(f"Failed to save unsupported prompt to DynamoDB: {e}")
                    print("prompt sent to db successfully")
                elif not clean_context and routing_mode == "Allowed" and greeting_query:        # for testing that we properly leave greeting cases, no grounding
                    print("query with greeting and nothing else")
            
                print(f"Amount of events: {eventLen}")
                if attribution_citations:
                    attribution = {"citations": attribution_citations}

            except Exception as e:
                logger.error(f"Failed to invoke agent: {e}")
                return _error_response(500, "Failed to invoke Bedrock Agent")

            message = routing_mode

        # response_body = {
        #     "message": "Agent invoked and returned response",
        #     "response": completion
        # }

        # response for testing, includes guardrail info for analysis
        response_body = {
            "message": message,
            "response": completion,
            # "routing_mode": routing_mode,
            # "non_risk_categories": non_risk_categories
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
