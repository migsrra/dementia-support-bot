import json
import boto3
import logging
import os
import uuid

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
        GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "6")

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

        risk_score = 0
        non_risk_categories = []
        message = ""
        response = ""
        bypass_agent = False

        assessments = guardrail_response.get("assessments", [])

        for assessment in assessments:
            # denied topics
            topic_policy = assessment.get("topicPolicy", {})
            topics = topic_policy.get("topics", [])

            for topic in topics:        
                name = topic.get("name")
                # compute risk of the query
                if name == "T1_Emotional Distress":                        # tier 1 risk
                    risk_score += 1
                elif name == "T2_Ambiguous Crisis_Self-Harm Language":     # tier 2 risk
                    risk_score += 3
                elif name == "T3_Explicit Self-Harm Intent":               # tier 3 risk
                    risk_score += 5
                elif name == "Self-Harm Instructions":                  # tier 3 risk
                    risk_score += 5
                elif name == "Harming_others":                  # tier 3 risk
                    risk_score += 5
                elif name == "MAID_euthanesia":
                    message = "MAID_euthanesia boundary"
                    response = MAID_EUTHANESIA_TEMPLATE
                    bypass_agent = True
                else:
                    non_risk_categories.append(name)   

            # content filters
            content_policy = assessment.get("contentPolicy", {})
            filters = content_policy.get("filters", [])

            for filter in filters:
                type = filter.get("type")
                if type == "PROMPT_ATTACK":
                    message = "Prompt Attack"
                    response = PROMPT_ATTACK_TEMPLATE
                    bypass_agent = True

        # logger.info(f"Risk score: {risk_score}")
        # logger.info(f"non_risk_categories: {non_risk_categories}")
        
        # Response Strategy based on risk and blocks, only if not hard refusal
        if bypass_agent is False:
            routing_mode = None

            # crisis routing
            if risk_score >= 10:
                routing_mode = "crisis_tier3"
            elif risk_score >= 5:
                routing_mode = "crisis_tier2"
            elif risk_score > 0:
                routing_mode = "crisis_tier1"

            # non-risk routing
            if non_risk_categories and risk_score == 0:
                primary_block = non_risk_categories[0]      # only respond to the first non-risk category flagged in the query

                if primary_block == "Medical Diagnosis_Interpretation":
                    routing_mode = "Medical_boundary"
                elif primary_block == "Medication Dosing_Changes":
                    routing_mode = "Medical_boundary"
                elif primary_block == "Legal and High-Stakes Financial Execution":
                    routing_mode = "Legal_boundary"
                elif primary_block == "Non-Dementia Related Queries":
                    routing_mode = "Scope_boundary"

            if routing_mode:        # set routing mode parameter if crisis flagged
                session_state = {
                    "sessionAttributes": {
                        "routing_mode": routing_mode
                    }
                }
            else:           # reset parameter if no crisis flag (so state doesn't persist from the past)
                session_state = {
                    "sessionAttributes": {}
                }
            
            # logger.info(f"routing: {routing_mode}")

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
                attribution = None
                attribution_citations = []
                eventLen = 0
                for event in response.get("completion"):
                    #Collect agent output.
                    eventLen += 1
                    if 'chunk' in event:
                        chunk = event.get("chunk")
                        completion += chunk["bytes"].decode()
                        chunk_attribution = chunk.get("attribution")
                        if chunk_attribution and isinstance(chunk_attribution, dict):
                            citations = chunk_attribution.get("citations")
                            if isinstance(citations, list):
                                attribution_citations.extend(citations)
                        print(f"chunk: {chunk}")
                    
                    # Log trace output.
                    if 'trace' in event:
                        trace_event = event.get("trace")
                        print(f"trace: {trace_event}")
                
                message = routing_mode
                response = completion
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
            "response": response,
            "risk_score": risk_score,
            "routing_mode": routing_mode,
            "non_risk_categories": non_risk_categories
        }

        if attribution is not None:
            response_body["attribution"] = attribution

        return _success_response(200, response_body)
    
    except Exception as exc:
        logger.info("Unexpected error")
        return _error_response(500, f"Internal server error: {str(exc)}")
