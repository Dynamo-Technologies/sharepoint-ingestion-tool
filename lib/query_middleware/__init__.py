"""Permission-filtered query middleware for the RAG pipeline.

Intercepts every query, resolves user permissions from SAML assertions
and DynamoDB cache, applies mandatory filters to Bedrock KB retrieval,
and ensures the LLM never sees unauthorized content.
"""
