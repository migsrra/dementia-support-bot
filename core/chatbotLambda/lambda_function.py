import json
import boto3
import logging
import os

logger = logging.getLogger()
logger.setLevel(logging.INFO)


AWS_PROFILE = os.getenv("AWS_PROFILE")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
BEDROCK_AGENT_ID = os.getenv("BEDROCK_AGENT_ID")
BEDROCK_ALIAS_ID = os.getenv("BEDROCK_ALIAS_ID")

MEDICAL_REDIRECT_TEMPLATE = """
    I’m not able to provide medical diagnoses, interpret symptoms, or give advice on adjusting medications. 
    For health concerns, please consult a qualified healthcare professional. 

    I can, however, help with general dementia caregiving strategies, tips for daily routines, or behavioral support. 
    Would you like guidance on any of those topics?
    """

LEGAL_REDIRECT_TEMPLATE = """
    I’m not able to provide legal advice, draft legal documents, or guide financial decisions like wills, power of attorney, etc. 

    However, I can support you with caregiving-related questions, like managing day-to-day care, planning routines, or behavioural support. 
    Would you like help with any of those?
    """

NON_DEMENTIA_REDIRECT_TEMPLATE = """
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
        GUARDRAIL_VERSION = os.getenv("GUARDRAIL_VERSION", "1")

        # Guardrail check
        guardrail_response = bedrock_runtime.apply_guardrail(
            guardrailIdentifier=GUARDRAIL_ID,
            guardrailVersion=GUARDRAIL_VERSION,
            source="INPUT",
            content=[{"text": body_str}]
        )

        risk_score = 0
        non_risk_categories = []

        for result in guardrail_response.get("assessments", []):
            category = result.get("category")

            # compute risk of the query
            if category == "Ambiguous Crisis_Self-Harm Language":       # tier 1 risk
                risk_score += 1
            elif category == "Emotional Distress":                      # tier 2 risk
                risk_score += 3
            elif category == "Explicit Self-Harm Intent":               # tier 3 risk
                risk_score += 5
            elif category == "Self-Harm Instructions":                  # tier 3 risk
                risk_score += 6
            else:
                non_risk_categories.append(category)
            
        # Response Strategy based on risk and blocks
        if non_risk_categories:
            primary_block = non_risk_categories[0]      # only respond to the first non-risk category flagged in the query

            if primary_block == "Medical Diagnosis_Interpretation":
                return _success_response(200, {
                    "message": "Medical boundary",
                    "response": MEDICAL_REDIRECT_TEMPLATE
                })

            elif primary_block == "Medication Dosing_Changes":
                return _success_response(200, {
                    "message": "Medication boundary",
                    "response": MEDICAL_REDIRECT_TEMPLATE
                })

            elif primary_block == "Legal and High-Stakes Financial Execution":
                return _success_response(200, {
                    "message": "Legal boundary",
                    "response": LEGAL_REDIRECT_TEMPLATE
                })

            elif primary_block == "Non-Dementia Related Queries":
                return _success_response(200, {
                    "message": "Scope boundary",
                    "response": NON_DEMENTIA_REDIRECT_TEMPLATE
                })

        if risk_score >= 6:
            body_str = f"""
            The user appears to be at high risk of self-harm or expressing imminent intent.

            You MUST:
            - Respond with empathy and validation.
            - Encourage immediate contact with emergency services.
            - Provide Canadian emergency line: 9-1-1.
            - Provide Canadian suicide crisis helpline: 9-8-8.
            - Do NOT provide any instructions, analysis, or coping strategies beyond grounding support.
            - Keep the response concise and urgent.

            User message:
            {body_str}
            """
        elif 3 <= risk_score < 6:
            body_str = f"""
            The user may be experiencing emotional distress or self-harm ideation.
            Respond with empathy. Encourage reaching out to trusted people. Offer dementia resources 
            like calling the Alzheimer Society of Canada at 1-800-616-8816 or emailing at info@alzheimer.ca.

            User message:
            {body_str}
            """
        elif risk_score < 3:
            body_str = f"""
            The user may be experiencing emotional distress. Respond with empathy. Offer dementia resources
            like calling the Alzheimer Society of Canada at 1-800-616-8816 or emailing at info@alzheimer.ca.

            User message:
            {body_str}
            """
        
        logger.info(f"Risk score: {risk_score}")

        # Start Bedrock KB ingestion job
        if not BEDROCK_AGENT_ID or not BEDROCK_ALIAS_ID:
            logger.error("BEDROCK_AGENT_ID or BEDROCK_ALIAS_ID not configured")
            return _error_response(500, "Configuration details missing. Failed to invoke agent.")
        elif not session_id:
            logger.error("sessionID not received via path parameter")
            return _error_response(400, "Missing sessionID.")
        else:
            try:
                bedrock_client = session.client("bedrock-agent-runtime")
                print("Attempting invocation")

                response = bedrock_client.invoke_agent(
                    agentAliasId=BEDROCK_ALIAS_ID, 
                    agentId=BEDROCK_AGENT_ID, 
                    enableTrace=True,
                    endSession=False,
                    inputText=body_str, 
                    sessionId=session_id,
                    streamingConfigurations = { 
                        "streamFinalResponse" : False
                    }
                )
                completion = ""
                eventLen = 0
                for event in response.get("completion"):
                    #Collect agent output.
                    eventLen += 1
                    if 'chunk' in event:
                        chunk = event.get("chunk")
                        completion += chunk["bytes"].decode()
                        print(f"chunk: {chunk}")
                    
                    # Log trace output.
                    if 'trace' in event:
                        trace_event = event.get("trace")
                        print(f"trace: {trace_event}")
                
                print(f"Amount of events: {eventLen}")
                        
            except Exception as e:
                logger.error(f"Failed to invoke agent: {e}")
                return _error_response(500, "Failed to invoke Bedrock Agent")

        response_body = {
            "message": "Agent invoked and returned response",
            "response": completion
        }

        return _success_response(200, response_body)
    
    except Exception as exc:
        logger.info("Unexpected error")
        return _error_response(500, f"Internal server error: {str(exc)}")
