"""
Executor agent implementing the ReAct loop with risk enforcement.
"""
from __future__ import annotations

import json
import logging
import re

from app.agents.base import ActionStep, AgentResponse, AgentStatus
from app.providers.base import LLMMessage
from app.providers.manager import ProviderManager, get_provider_manager
from app.tools.registry import ToolRegistry, get_tool_registry

logger = logging.getLogger("personal_ai.agents.executor")

EXECUTOR_SYSTEM_PROMPT = """You are an Autonomous Executor in Personal AI Mission Control.
You have access to tools to accomplish tasks.

Available Tools:
{tool_descriptions}

FORMAT INSTRUCTIONS:
To use a tool, respond with:
Thought: <what you want to do>
Action: <tool_name>
Action Input: <valid JSON object of arguments>

When you have the final answer or completed the request, respond with:
Thought: I have finished the task.
Final Answer: <your final explanation or result>

IMPORTANT RULES:
1. Always output Thought before Action.
2. Action Input MUST be valid JSON.
3. Be concise and precise.
"""


class ExecutorAgent:
    """Autonomous agent that uses tools to solve tasks safely."""

    def __init__(
        self,
        provider_manager: ProviderManager | None = None,
        tool_registry: ToolRegistry | None = None,
        max_steps: int = 8,
    ):
        self.provider_manager = provider_manager or get_provider_manager()
        self.registry = tool_registry or get_tool_registry()
        self.max_steps = max_steps

    def _build_tool_descriptions(self) -> str:
        lines = []
        for defn in self.registry.list_tools():
            params_str = ", ".join(f"{p.name}: {p.type}" for p in defn.parameters)
            lines.append(f"- {defn.name}({params_str}): {defn.description} [Risk: {defn.risk_level.value.upper()}]")
        return "\n".join(lines)

    async def run(
        self,
        task: str,
        *,
        history: list[ActionStep] | None = None,
        confirmed_step: int | None = None,
        model: str | None = None,
    ) -> AgentResponse:
        steps: list[ActionStep] = history or []
        step_num = len(steps) + 1

        tools_desc = self._build_tool_descriptions()
        system_content = EXECUTOR_SYSTEM_PROMPT.format(tool_descriptions=tools_desc)

        # Build conversation history
        messages: list[LLMMessage] = [
            LLMMessage(role="system", content=system_content),
            LLMMessage(role="user", content=f"Task: {task}"),
        ]

        for s in steps:
            if s.thought:
                messages.append(LLMMessage(role="assistant", content=f"Thought: {s.thought}\nAction: {s.tool_name}\nAction Input: {json.dumps(s.tool_args)}"))
            if s.tool_result is not None:
                obs = s.tool_result.output if s.tool_result.success else f"Error: {s.tool_result.error}"
                messages.append(LLMMessage(role="user", content=f"Observation: {obs}"))

        while step_num <= self.max_steps:
            resp = await self.provider_manager.generate(
                messages,
                task_type="code",
                model=model,
                temperature=0.1,
            )
            raw = resp.text.strip()

            # Check if final answer reached
            if "Final Answer:" in raw:
                parts = raw.split("Final Answer:", 1)
                thought = parts[0].replace("Thought:", "").strip()
                final_answer = parts[1].strip()
                steps.append(ActionStep(step_number=step_num, thought=thought))
                return AgentResponse(
                    final_answer=final_answer,
                    steps=steps,
                    status=AgentStatus.COMPLETED,
                )

            # Parse Thought and Action
            thought_match = re.search(r"Thought:\s*(.*?)(?=\nAction:|$)", raw, re.DOTALL)
            action_match = re.search(r"Action:\s*([a-zA-Z0-9_]+)", raw)
            input_match = re.search(r"Action Input:\s*(.*?)(?=\nObservation:|$)", raw, re.DOTALL)

            thought = thought_match.group(1).strip() if thought_match else ""
            tool_name = action_match.group(1).strip() if action_match else None
            args_raw = input_match.group(1).strip() if input_match else "{}"

            if not tool_name:
                # No action found, treat whole text as response
                return AgentResponse(
                    final_answer=raw,
                    steps=steps,
                    status=AgentStatus.COMPLETED,
                )

            try:
                # Remove code blocks if present
                clean_args = args_raw.strip()
                if clean_args.startswith("```"):
                    lines = clean_args.split("\n")
                    clean_args = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
                tool_args = json.loads(clean_args)
            except Exception:
                tool_args = {"raw_input": args_raw}

            # Check confirmation for high-risk actions
            user_confirmed = (confirmed_step == step_num)
            allowed, reason = self.registry.check_permission(tool_name, user_confirmed=user_confirmed)

            if not allowed:
                # Pause and request confirmation
                pending_step = ActionStep(
                    step_number=step_num,
                    thought=thought,
                    tool_name=tool_name,
                    tool_args=tool_args,
                    requires_confirmation=True,
                )
                steps.append(pending_step)
                return AgentResponse(
                    final_answer="",
                    steps=steps,
                    status=AgentStatus.WAITING_CONFIRMATION,
                    pending_confirmation={
                        "step_number": step_num,
                        "tool_name": tool_name,
                        "tool_args": tool_args,
                        "message": reason,
                    },
                )

            # Execute permitted tool
            res = await self.registry.execute(tool_name, tool_args, user_confirmed=user_confirmed)
            step_record = ActionStep(
                step_number=step_num,
                thought=thought,
                tool_name=tool_name,
                tool_args=tool_args,
                tool_result=res,
            )
            steps.append(step_record)

            obs = res.output if res.success else f"Error: {res.error}"
            messages.append(LLMMessage(role="assistant", content=f"Thought: {thought}\nAction: {tool_name}\nAction Input: {json.dumps(tool_args)}"))
            messages.append(LLMMessage(role="user", content=f"Observation: {obs}"))
            step_num += 1

        return AgentResponse(
            final_answer="Reached maximum iteration steps without concluding.",
            steps=steps,
            status=AgentStatus.FAILED,
        )
