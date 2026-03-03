# ---------------------------------------------------------------
# Amazon Bedrock Guardrails — defense-in-depth for RAG queries
# ---------------------------------------------------------------

resource "aws_bedrock_guardrail" "rag" {
  count                     = var.enable_webui ? 1 : 0
  name                      = "sp-ingest-rag-guardrail"
  blocked_input_messaging   = "Your request was blocked by our content policy. Please rephrase your question."
  blocked_outputs_messaging = "The response was blocked by our content policy. Please try a different question."
  description               = "PII redaction, topic blocking, and content filtering for RAG queries"

  # --- PII Detection: redact sensitive data ---
  sensitive_information_policy_config {
    pii_entities_config {
      action = "ANONYMIZE"
      type   = "US_SOCIAL_SECURITY_NUMBER"
    }
    pii_entities_config {
      action = "ANONYMIZE"
      type   = "CREDIT_DEBIT_CARD_NUMBER"
    }
    pii_entities_config {
      action = "ANONYMIZE"
      type   = "PHONE"
    }
    pii_entities_config {
      action = "ANONYMIZE"
      type   = "EMAIL"
    }
    pii_entities_config {
      action = "ANONYMIZE"
      type   = "US_INDIVIDUAL_TAX_IDENTIFICATION_NUMBER"
    }
  }

  # --- Topic Blocking: deny out-of-scope advice ---
  topic_policy_config {
    topics_config {
      name       = "PersonalMedicalAdvice"
      definition = "Providing personal medical advice, diagnoses, or treatment recommendations"
      type       = "DENY"
      examples   = ["What medicine should I take for my headache?", "Is this mole cancerous?"]
    }
    topics_config {
      name       = "LegalAdvice"
      definition = "Providing legal advice, interpreting laws, or recommending legal actions"
      type       = "DENY"
      examples   = ["Can I sue my employer?", "Is this contract legally binding?"]
    }
    topics_config {
      name       = "InvestmentAdvice"
      definition = "Providing investment or financial planning advice"
      type       = "DENY"
      examples   = ["Should I buy this stock?", "How should I allocate my 401k?"]
    }
  }

  # --- Content Filtering: block harmful content ---
  content_policy_config {
    filters_config {
      input_strength  = "HIGH"
      output_strength = "HIGH"
      type            = "HATE"
    }
    filters_config {
      input_strength  = "HIGH"
      output_strength = "HIGH"
      type            = "INSULTS"
    }
    filters_config {
      input_strength  = "HIGH"
      output_strength = "HIGH"
      type            = "SEXUAL"
    }
    filters_config {
      input_strength  = "HIGH"
      output_strength = "HIGH"
      type            = "VIOLENCE"
    }
    filters_config {
      input_strength  = "HIGH"
      output_strength = "HIGH"
      type            = "MISCONDUCT"
    }
    filters_config {
      input_strength  = "NONE"
      output_strength = "NONE"
      type            = "PROMPT_ATTACK"
    }
  }
}
