import json
import boto3
import logging
import os
import uuid
import re

logger = logging.getLogger()
logger.setLevel(logging.INFO)


AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_AGENT_ID = os.getenv("BEDROCK_AGENT_ID")
BEDROCK_ALIAS_ID = os.getenv("BEDROCK_ALIAS_ID")

MAID_EUTHANESIA_TEMPLATE = """
    I'm not able to respond to this request, please consult your physician.

    I am here to help with general dementia caregiving strategies, tips for daily routines, or behavioral support. 
    Would you like guidance on any of those topics?
"""

PROMPT_ATTACK_TEMPLATE = """
    I’m here to support questions specifically related to dementia caregiving. 
    I’m not able to provide guidance outside of that scope. 

    If you have questions about caring for someone with dementia, daily routines, behavioral management, or support resources, I’d be happy to help. 
    Is there a specific caregiving topic you’d like guidance on?
    """

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
        GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "18")

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
        completion = ""
        routing_mode = "Allowed"
        bypass_agent = False
        grounding_score = None
        grounding_action = None
        relevance_score = None
        relevance_action = None

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
            if high_priority_topic:
                routing_mode = high_priority_topic
            elif "MAID_Euthanesia" in flagged_topics:
                message = "MAID_Euthanesia"
                completion = MAID_EUTHANESIA_TEMPLATE
                bypass_agent = True
            elif flagged_topics:             
                priority_topic = non_harm_priority_topic(flagged_topics)       # non-crisis topics only, set routing based on priority

                if "Dementia_Related" == priority_topic:        # only dementia_related flagged, therefore allowed
                    routing_mode = "Allowed"
                elif "Medical_Education_Inquiry" in flagged_topics and ("Medical_Diagnosis_Interpretation" in flagged_topics or "Medication_Dosing_Changes" in flagged_topics):
                    routing_mode = "Medical_Education_Inquiry"        # ignore medical diagnosis/medication flag if medical education on (they're over-sensitive)
                else:
                    routing_mode = priority_topic
                # print("non-harm:", routing_mode)
            else:
                routing_mode = "Non_Dementia_Related_Queries"       # not dementia related nor a crisis or non-crisis topic that is dementia related
        
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

                        # miguel old references code
                        # chunk_attribution = chunk.get("attribution")
                        # if chunk_attribution and isinstance(chunk_attribution, dict):
                        #     citations = chunk_attribution.get("citations")
                        #     if isinstance(citations, list):
                        #         attribution_citations.extend(citations)

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
                    
                # Create a list of lines, strip whitespace, and use a set to unique them
                unique_lines = list(dict.fromkeys([line.strip() for line in retrieved_context.split("\n") if line.strip()]))
                clean_context = "\n".join(unique_lines)

                # print("DEBUG RESPONSE", completion)
                if routing_mode in output_checked_topics:
                    print("DEBUG CONTEXT", clean_context)       # would only collect references if allowed/low risk topic

                if clean_context and routing_mode in output_checked_topics:         # only check grounding and relevance if allowed or low risk topic
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
        
                        # Extract specific scores for your dementia bot logic
                        for assessment in guardrail_check.get("assessments", []):
                            grounding_policy = assessment.get("contextualGroundingPolicy", {})

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
                                elif filter_type == "RELEVANCE" and relevance_action == "BLOCKED":
                                    print("Low relevance detected.")

                    except Exception as e:
                        print(f"Error calling Guardrail API: {e}")
                elif not clean_context and routing_mode in output_checked_topics:       # if did not find references for an allowed/low risk topic
                    print("No references returned, grounding failed. Should forward to physician")
                    
                # save output values
                # response = completion
                message = routing_mode

                print(f"Amount of events: {eventLen}")
                if attribution_citations:
                    attribution = {"citations": attribution_citations}

            except Exception as e:
                logger.error(f"Failed to invoke agent: {e}")
                return _error_response(500, "Failed to invoke Bedrock Agent")

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

        if attribution is not None:
            response_body["attribution"] = attribution

        return _success_response(200, response_body)
    
    except Exception as exc:
        logger.info("Unexpected error")
        return _error_response(500, f"Internal server error: {str(exc)}")
