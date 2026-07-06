import os
import json
from pydantic import BaseModel, Field
from openai import AzureOpenAI
from utils.logger import get_logger

log = get_logger("llm_sql_generator")

# We use structured outputs so the LLM must return exactly these fields
class SQLSuggestion(BaseModel):
    sql_query: str = Field(description="The complete SQL query string to run against the database.")
    join_keys: list[str] = Field(description="A list of categorical column names in the SELECT clause used for grouping (empty for KPIs).")
    compare_cols: list[str] = Field(description="A list of numeric/aggregated column names in the SELECT clause used for value comparison (empty for KPIs).")
    confidence: str = Field(description="One of: 'HIGH', 'MEDIUM', 'LOW', or 'NONE', based on how confident you are that the query is perfectly correct.")

def suggest_sql_via_llm(
    visual_type: str,
    visual_title: str,
    chart_headers: list[str],
    schema_ddl: str,
    slicers: list[dict],
    db_driver: str = ""
) -> tuple[str, str, list[str], list[str]]:
    """
    Generate a candidate SQL query using Azure OpenAI.

    Args:
        visual_type: e.g., 'KPI', 'Card', 'Clustered column chart'.
        visual_title: The human-readable title of the visual.
        chart_headers: Column headers extracted from "Show as a table" (if any).
        schema_ddl: String representation of the database schema layout.
        slicers: Detected slicer state from the dashboard.
        db_driver: The SQLAlchemy driver to infer SQL dialect (e.g. postgresql).

    Returns:
        Tuple of (sql_query, confidence, join_keys, compare_cols)
    """
    from config.settings import (
        FOUNDRY_API_KEY, FOUNDRY_ENDPOINT, FOUNDRY_MODEL, FOUNDRY_API_VERSION
    )
    api_key = FOUNDRY_API_KEY
    endpoint = FOUNDRY_ENDPOINT
    api_version = FOUNDRY_API_VERSION
    model = FOUNDRY_MODEL

    if not api_key or not endpoint:
        log.error("FOUNDRY_API_KEY or FOUNDRY_ENDPOINT not found in environment. Returning empty SQL.")
        return "", "NONE", [], []

    try:
        client = AzureOpenAI(
            api_key=api_key,
            api_version=api_version,
            azure_endpoint=endpoint
        )
    except Exception as e:
        log.error(f"Failed to initialize AzureOpenAI client: {e}")
        return "", "NONE", [], []

    # Build the prompt
    dialect_hint = f" Use {db_driver} SQL dialect." if db_driver else ""
    
    slicer_context = ""
    if slicers:
        slicer_context = "Active Dashboard Slicers (these MUST be included as WHERE clauses):\n"
        for s in slicers:
            slicer_context += f"- {s['slicer_title']}: {s.get('expected_value')}\n"

    prompt = f"""
You are an expert Data Engineer writing validation SQL queries for a dashboard testing framework.

Your goal is to write a single SQL query that will output the exact data shown in a specific dashboard visual.
{dialect_hint}

DATABASE SCHEMA:
{schema_ddl}

{slicer_context}

TARGET VISUAL:
- Type: {visual_type}
- Title/Description: {visual_title}
- Extracted Headers: {chart_headers}

INSTRUCTIONS:
1. If the Target Visual is a KPI card (a single scalar number), write an aggregate query (e.g. SELECT SUM(sales) FROM...). `join_keys` and `compare_cols` should be empty lists.
2. If the Target Visual is a Chart/Table, the query MUST group by the categorical headers and aggregate the numeric headers. 
   - Put the exact categorical header names in `join_keys`.
   - Put the exact numeric/aggregated header names in `compare_cols`.
   - Use aliases in the SELECT clause so the output column names match the Extracted Headers EXACTLY (e.g. SELECT market AS "Market").
3. Apply any Active Dashboard Slicers in the WHERE clause. If the slicer says "All" or "[]", you can ignore it or assume no filtering.
4. Set `confidence` to HIGH if the mapping is obvious, MEDIUM if you had to guess aggregations or tables, or LOW/NONE if it's impossible.
"""

    log.debug(f"Calling Azure OpenAI for visual '{visual_title}'...")

    try:
        completion = client.beta.chat.completions.parse(
            model=model,
            messages=[
                {"role": "user", "content": prompt}
            ],
            response_format=SQLSuggestion,
            temperature=0.1
        )
        
        parsed_response = completion.choices[0].message.parsed
        if not parsed_response:
            return "", "NONE", [], []
            
        sql_query = parsed_response.sql_query
        confidence = parsed_response.confidence
        join_keys = parsed_response.join_keys
        compare_cols = parsed_response.compare_cols

        # Filter out empty string placeholders that might sneak in
        if isinstance(join_keys, list):
            join_keys = [k for k in join_keys if k]
        if isinstance(compare_cols, list):
            compare_cols = [c for c in compare_cols if c]

        log.info(f"LLM generated query with {confidence} confidence for '{visual_title}'")
        return sql_query, confidence, join_keys, compare_cols

    except Exception as e:
        log.error(f"Azure OpenAI generation failed: {e}")
        return "", "NONE", [], []
