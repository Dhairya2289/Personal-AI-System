"""
Research and memory retrieval tools.
"""
from __future__ import annotations

import memory
from app.tools.registry import BaseTool, RiskLevel, ToolResult


class MemorySearchTool(BaseTool):
    name = "memory_search"
    description = "Search personal persistent memory with semantic types, confidence, and BM25 relevance."
    risk_level = RiskLevel.LOW

    async def execute(
        self,
        query: str,
        limit: int = 5,
        mem_type: str | None = None,
        min_confidence: float = 0.0,
    ) -> ToolResult:
        try:
            mem_types = (mem_type,) if mem_type else None
            hits = memory.retrieve(
                query,
                limit=limit,
                mem_types=mem_types,
                min_confidence=min_confidence,
            )
            return ToolResult(success=True, output=hits)
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))


class MemoryRecordTool(BaseTool):
    name = "memory_record"
    description = "Store a new factual or procedural insight into persistent memory."
    risk_level = RiskLevel.MEDIUM

    async def execute(
        self,
        content: str,
        kind: str = "semantic",
        mem_type: str = "fact",
        confidence: float = 0.85,
        tags: str = "",
    ) -> ToolResult:
        try:
            mid = memory.record_memory(
                content=content,
                kind=kind,
                mem_type=mem_type,
                confidence=confidence,
                tags=tags,
                source="agent:executor",
            )
            if mid is not None:
                return ToolResult(success=True, output={"id": mid, "status": "recorded"})
            return ToolResult(success=False, error="Failed to record memory or excluded by policy")
        except Exception as exc:
            return ToolResult(success=False, error=str(exc))
