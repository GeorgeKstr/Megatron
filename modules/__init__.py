"""
Megatron Module System

Each module provides:
  - name: str
  - description: str
  - tool_defs: list[dict] — OpenAI tool schemas for the LLM
  - execute(tool_name: str, args: dict) -> dict
  - route_score(user_prompt: str) -> float  — how relevant this module is

The router picks the highest-scoring module and delegates.
"""
