"""
TB 2.1 Scaffold Agent

Harbor-compatible agent implementing probe-first, evidence-gated, verifier-gate scaffold.

Harbor interface:
- BaseAgent with async setup() + async run(instruction, environment, context)
- environment.exec(command, env=None, timeout_sec=None) -> ExecResult
- ExecResult: .stdout, .stderr, .return_code
- context: AgentContext — update n_input_tokens, n_output_tokens, cost_usd, metadata

Usage:
  harbor run -d terminal-bench/terminal-bench-2-1 \
    --agent-import-path agent.scaffold_agent:ScaffoldAgent \
    -m openai/anthropic/claude-opus-4-7 \
    -n 4 -k 5 -y
"""

from __future__ import annotations
import asyncio
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import litellm

from harbor.agents.base import BaseAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

# Set LLM_API_BASE + LLM_API_KEY to use any OpenAI-compatible endpoint.
# Example (OpenRouter): LLM_API_BASE=https://openrouter.ai/api/v1 LLM_API_KEY=sk-or-v1-...
_LLM_API_BASE = os.environ.get("LLM_API_BASE", "https://openrouter.ai/api/v1")
_LLM_API_KEY  = os.environ.get("LLM_API_KEY", "")


SYSTEM_PROMPT = """You are a terminal agent completing a task inside a Docker container.
You have bash access. All commands you write in ```bash blocks will be executed.

MANDATORY PROCESS — follow these four phases in order:

## Phase 1: PROBE
Before doing anything else, explore the environment:
- List relevant directories (ls /app, ls /)
- Read any files mentioned in the task
- Check what tools are available for the task
- Print a one-line summary: "ENVIRONMENT: <what I found>"

## Phase 2: PLAN
Write a LEDGER before executing ANY commands:
```
LEDGER:
goal: <specific, observable success criterion>
constraints: <file size limits, exact paths, required formats>
verifier_cmd: <bash command that exits 0 on success, nonzero on failure>
steps:
  1. <first concrete step>
  2. <next step>
  ...
```

VERIFIER QUALITY RULES — verifier_cmd must test the actual requirement:
- Build tasks: test import AND runtime behavior, not just compilation exit code
- Service/server tasks: test actual functionality (HTTP request, mail send), not just process status
- File transform tasks: check specific content at specific position, not just file existence
- Security tasks: test encoded/obfuscated variants, not just literal string match
- Media tasks: check duration, codec, or frame content — not just file size
- Output-matching tasks: verify EXACT output including whitespace, newlines, and ordering — not just semantic equivalence
- If the task description gives a specific verification command or expected output, use it verbatim as your verifier_cmd

## Phase 3: EXECUTE
Execute each step. After each command:
- Check the output
- If something failed, diagnose before retrying
- Do NOT run the same failing command twice unchanged

## Phase 4: VERIFY
CRITICAL: The benchmark has an OFFICIAL external grader that re-evaluates your solution after you finish.
Your verifier_cmd is a self-check only — the external grader may be stricter.

Before writing TASK_COMPLETE:
1. Re-read the task description. List every testable requirement. Does your verifier_cmd cover ALL of them?
2. If the task description names a specific command or output format, test for that EXACTLY (whitespace, encoding, ordering).
3. If you wrote a custom verify script, ask: "What could the external grader check that my script doesn't?"
4. Run one adversarial test: what input or edge case might expose a flaw in your solution?

Only write TASK_COMPLETE when you are confident the external grader will pass — not just your own checks.

RULES:
- Never write TASK_COMPLETE without running verifier_cmd first
- Never retry a command verbatim if it failed
- If you cannot complete the task after 3 fix attempts, write TASK_FAILED: <reason>
"""

# Task-family keywords → verifier quality hint
_TASK_FAMILY_HINTS: list[tuple[list[str], str]] = [
    (["build", "compile", "cython", "extension", "shared library", "so file"],
     "BUILD TASK: verifier_cmd must test import AND runtime behavior, not just compilation exit code."),
    (["service", "server", "daemon", "nginx", "mailman", "postfix", "systemctl", "socket"],
     "SERVICE TASK: verifier_cmd must test actual functionality via request/response, not just process status."),
    (["insert", "transform", "replace", "modify", "edit", "patch", "fasta", "sequence"],
     "FILE-TRANSFORM TASK: verifier_cmd must check specific content at the correct position, not just file existence."),
    (["xss", "injection", "filter", "sanitize", "escape", "script", "alert"],
     "SECURITY TASK: verifier_cmd must test encoded/obfuscated variants (e.g. &#x3C;script&gt;), not just literal strings."),
    (["video", "mp4", "audio", "ffmpeg", "codec", "frame", "duration"],
     "MEDIA TASK: verifier_cmd must check duration, codec, or content — not just file size or existence."),
    (["toml", "output.toml", "jump", "metric", "analyzer"],
     "METRICS TASK: verifier_cmd must check required keys exist with valid numeric values in the output file."),
]


class ScaffoldAgent(BaseAgent):
    """Harbor external agent with probe-first, verifier-gate scaffold."""

    @staticmethod
    def name() -> str:
        return "scaffold-agent"

    def version(self) -> str | None:
        return "0.4.1"

    async def setup(self, environment: BaseEnvironment) -> None:
        pass

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        model = self.model_name or "openai/anthropic/claude-opus-4-7"
        messages: list[dict] = []
        total_input_tokens = 0
        total_output_tokens = 0
        total_cost = 0.0
        max_turns = 40
        consecutive_failures = 0
        verifier_cmd: str | None = None
        ledger_written = False
        verifier_ever_run = False

        effective_system_prompt = SYSTEM_PROMPT

        # Trace: structured log of every turn for post-run analysis
        trace: list[dict] = []
        started_at = datetime.now(timezone.utc).isoformat()

        # Detect task family from instruction for verifier quality hints
        task_family_hint = _get_task_family_hint(instruction)

        # Initial turn: task + probe instruction
        messages.append({
            "role": "user",
            "content": (
                f"TASK:\n{instruction}\n\n"
                "Start with Phase 1 (PROBE): explore the environment, then output a line "
                "starting with ENVIRONMENT: summarizing what you found."
            ),
        })

        for turn in range(max_turns):
            turn_start = datetime.now(timezone.utc).isoformat()
            turn_input_tokens = 0
            turn_output_tokens = 0
            turn_cost = 0.0

            # LLM call with rate-limit retry (exponential backoff)
            for attempt in range(5):
                try:
                    response = await litellm.acompletion(
                        model=model,
                        messages=[{"role": "system", "content": effective_system_prompt}] + _trim_messages(messages),
                        max_tokens=4096,
                        temperature=0.0,
                        api_base=_LLM_API_BASE,
                        api_key=_LLM_API_KEY,
                    )
                    break
                except litellm.RateLimitError:
                    if attempt == 4:
                        raise
                    wait = 30 * (2 ** attempt)  # 30s, 60s, 120s, 240s
                    await asyncio.sleep(wait)
                except litellm.AuthenticationError:
                    raise
            else:
                raise RuntimeError("Rate limit retry exhausted")

            assistant_text: str = response.choices[0].message.content or ""
            usage = response.usage
            if usage:
                turn_input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                turn_output_tokens = getattr(usage, "completion_tokens", 0) or 0
                total_input_tokens += turn_input_tokens
                total_output_tokens += turn_output_tokens
                try:
                    turn_cost = litellm.completion_cost(completion_response=response) or 0.0
                    total_cost += turn_cost
                except Exception:
                    pass

            messages.append({"role": "assistant", "content": assistant_text})

            # Extract and run bash commands
            commands = _extract_bash_commands(assistant_text)
            cmd_results: list[str] = []
            cmd_details: list[dict] = []
            turn_had_failure = False

            for cmd in commands:
                result = await environment.exec(
                    command=cmd,
                    timeout_sec=300,
                )
                out = result.stdout or ""
                err = result.stderr or ""
                rc = result.return_code

                block = f"$ {cmd}\n{out}"
                if err.strip():
                    block += f"\n[stderr] {err.strip()}"
                if rc != 0:
                    block += f"\n[exit code {rc}]"
                    turn_had_failure = True
                cmd_results.append(block)
                cmd_details.append({"cmd": cmd, "rc": rc, "stdout": out[:2000], "stderr": err[:500]})

            # Extract verifier_cmd from LEDGER if present
            if "verifier_cmd:" in assistant_text:
                for line in assistant_text.split("\n"):
                    if line.strip().startswith("verifier_cmd:"):
                        verifier_cmd = line.split(":", 1)[1].strip()

            # Track whether LEDGER has been written
            if "LEDGER:" in assistant_text:
                ledger_written = True

            # Track whether verifier_cmd has ever been executed
            if verifier_cmd and not verifier_ever_run:
                for detail in cmd_details:
                    if detail["cmd"].strip() == verifier_cmd.strip():
                        verifier_ever_run = True
                        break

            # Line-level signal detection (prevents false positives from "I should not write TASK_COMPLETE")
            _has_complete = bool(re.search(r'^\s*TASK_COMPLETE\s*$', assistant_text, re.MULTILINE | re.IGNORECASE))
            _has_failed = bool(re.search(r'^\s*TASK_FAILED[:\s]', assistant_text, re.MULTILINE | re.IGNORECASE))

            # Detect phase from assistant text
            if "ENVIRONMENT:" in assistant_text:
                phase = "probe"
            elif "LEDGER:" in assistant_text:
                phase = "plan"
            elif _has_complete:
                phase = "verify"
            elif _has_failed:
                phase = "failed"
            else:
                phase = "execute"

            # Record this turn in trace
            trace.append({
                "turn": turn,
                "phase": phase,
                "timestamp": turn_start,
                "assistant_text": assistant_text,
                "commands": cmd_details,
                "verifier_cmd": verifier_cmd,
                "consecutive_failures": consecutive_failures,
                "cost": {"input_tokens": turn_input_tokens, "output_tokens": turn_output_tokens, "usd": turn_cost},
            })

            # Check for completion signal
            if _has_complete:
                if verifier_cmd:
                    vresult = await environment.exec(command=verifier_cmd, timeout_sec=60)
                    if vresult.return_code == 0:
                        trace[-1]["outcome"] = "verified_complete"
                        break
                    else:
                        cmd_results.append(
                            f"[VERIFIER FAILED]\n$ {verifier_cmd}\n"
                            f"{vresult.stdout or ''}\n{vresult.stderr or ''}\n"
                            f"[exit code {vresult.return_code}]\n"
                            "You wrote TASK_COMPLETE but the verifier failed. Fix the issue and re-verify."
                        )
                        trace[-1]["outcome"] = "verifier_failed"
                        consecutive_failures += 1
                else:
                    # No verifier_cmd: warn once, accept on second unverified TASK_COMPLETE
                    unverified_count = sum(
                        1 for t in trace if t.get("outcome") == "unverified_complete_warned"
                    )
                    if unverified_count == 0:
                        trace[-1]["outcome"] = "unverified_complete_warned"
                        cmd_results.append(
                            "[SCAFFOLD] You wrote TASK_COMPLETE but have no verifier_cmd. "
                            "Add a verifier_cmd to your LEDGER and run it to confirm success. "
                            "If you truly cannot verify, write TASK_COMPLETE again."
                        )
                    else:
                        trace[-1]["outcome"] = "unverified_complete"
                        break

            if _has_failed:
                trace[-1]["outcome"] = "task_failed"
                break

            # Failure tracking and taxonomy hint
            if turn_had_failure:
                consecutive_failures += 1
            else:
                consecutive_failures = 0

            failure_hint = ""
            if turn_had_failure and cmd_details:
                failure_hint = _classify_failures(cmd_details)

            retry_hint = ""
            if consecutive_failures >= 2:
                retry_hint = (
                    "\n\n[SCAFFOLD] Two consecutive failures detected. "
                    "Stop and reconsider: what specifically went wrong? "
                    "Try a fundamentally different approach rather than repeating the same commands."
                )
                consecutive_failures = 0

            # LEDGER enforcement: if no LEDGER by turn 3, inject reminder
            ledger_hint = ""
            if not ledger_written:
                if turn == 3:
                    family_qualifier = f" {task_family_hint}" if task_family_hint else ""
                    ledger_hint = (
                        f"\n\n[SCAFFOLD] No LEDGER yet. Write it now (goal/constraints/verifier_cmd/steps).{family_qualifier}"
                    )
                elif turn == 6:
                    ledger_hint = "\n\n[SCAFFOLD] CRITICAL: Still no LEDGER. Write one before continuing."

            # Verifier run enforcement
            verifier_run_hint = ""
            if verifier_cmd and not verifier_ever_run and turn == 20:
                verifier_run_hint = f"\n\n[SCAFFOLD] Run your verifier_cmd now: {verifier_cmd}"

            # Budget-aware routing
            turns_left = max_turns - turn - 1
            budget_hint = ""
            if turns_left <= 3:
                budget_hint = f"\n\n[SCAFFOLD] {turns_left} turns left. Run verifier or write TASK_FAILED now."
            elif turns_left <= 8:
                budget_hint = f"\n\n[SCAFFOLD] {turns_left} turns left. Converge — run verifier_cmd."
            elif turns_left <= 15:
                budget_hint = f"\n\n[SCAFFOLD] {turns_left} turns left. Ensure verifier_cmd is set."

            # Feed results back to agent
            suffix = failure_hint + retry_hint + ledger_hint + verifier_run_hint + budget_hint
            if cmd_results:
                messages.append({
                    "role": "user",
                    "content": "\n\n".join(cmd_results) + suffix,
                })
            else:
                messages.append({
                    "role": "user",
                    "content": "Continue to the next step." + suffix,
                })

        # Save trace to logs_dir
        _save_trace(
            logs_dir=self.logs_dir,
            instruction=instruction,
            model=model,
            started_at=started_at,
            finished_at=datetime.now(timezone.utc).isoformat(),
            messages=messages,
            trace=trace,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            total_cost=total_cost,
            verifier_cmd=verifier_cmd,
        )

        # Update context with usage
        context.n_input_tokens = total_input_tokens
        context.n_output_tokens = total_output_tokens
        context.cost_usd = total_cost if total_cost > 0 else None
        context.metadata = {
            "turns": turn + 1,
            "verifier_cmd": verifier_cmd,
            "ledger_written": ledger_written,
            "verifier_ever_run": verifier_ever_run,
        }


def _trim_messages(messages: list[dict], keep_pairs: int = 8, truncate_chars: int = 400) -> list[dict]:
    """Sliding window: keep first message (task) + last keep_pairs turn-pairs in full.
    Older messages are truncated to truncate_chars to prevent context window overflow.
    """
    if len(messages) <= 1 + keep_pairs * 2:
        return messages
    first = messages[:1]                          # always keep task instruction
    recent = messages[-(keep_pairs * 2):]         # last N pairs in full
    middle = messages[1:-(keep_pairs * 2)]        # truncate these
    trimmed_middle = []
    for msg in middle:
        content = msg["content"]
        if len(content) > truncate_chars:
            content = content[:truncate_chars] + " ...[truncated]"
        trimmed_middle.append({"role": msg["role"], "content": content})
    return first + trimmed_middle + recent


def _save_trace(
    logs_dir: Path,
    instruction: str,
    model: str,
    started_at: str,
    finished_at: str,
    messages: list[dict],
    trace: list[dict],
    total_input_tokens: int,
    total_output_tokens: int,
    total_cost: float,
    verifier_cmd: str | None,
) -> None:
    """Save full conversation and structured trace to logs_dir."""
    try:
        logs_dir = Path(logs_dir)
        logs_dir.mkdir(parents=True, exist_ok=True)

        # Full conversation (every message, for replay/fine-tuning)
        conv_path = logs_dir / "conversation.jsonl"
        with conv_path.open("w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        # Structured turn-level trace (for failure analysis and routing improvement)
        trace_path = logs_dir / "trace.jsonl"
        with trace_path.open("w", encoding="utf-8") as f:
            for entry in trace:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Summary metadata
        summary = {
            "model": model,
            "started_at": started_at,
            "finished_at": finished_at,
            "n_turns": len(trace),
            "verifier_cmd": verifier_cmd,
            "phases_seen": list(dict.fromkeys(t["phase"] for t in trace)),
            "final_outcome": trace[-1].get("outcome") if trace else None,
            "cost": {
                "input_tokens": total_input_tokens,
                "output_tokens": total_output_tokens,
                "usd": total_cost,
            },
            "instruction_preview": instruction[:300],
        }
        (logs_dir / "trace_summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as e:
        # Don't crash the agent if trace saving fails
        pass


def _classify_failures(cmd_details: list[dict]) -> str:
    """Classify failed commands and return a targeted hint."""
    hints: list[str] = []
    for detail in cmd_details:
        if detail["rc"] == 0:
            continue
        combined = (detail.get("stdout", "") + " " + detail.get("stderr", "")).lower()

        if any(p in combined for p in ("command not found", "not installed", "no module named", "package", "apt-get", "pip install")):
            hints.append("[SCAFFOLD] Missing dependency detected. Install the required package before retrying.")
        elif any(p in combined for p in ("syntax error", "unexpected token", "invalid syntax", "parse error", "bad substitution")):
            hints.append("[SCAFFOLD] Syntax error in command. Check quoting, escaping, and shell syntax before retrying.")
        elif "permission denied" in combined:
            hints.append("[SCAFFOLD] Permission denied. Try with sudo or check file ownership.")
        elif any(p in combined for p in ("no such file", "not found", "cannot find", "does not exist")):
            hints.append("[SCAFFOLD] File or path not found. Re-probe directory structure before retrying.")
        elif any(p in combined for p in ("segmentation fault", "core dumped", "killed", "out of memory")):
            hints.append("[SCAFFOLD] Process crashed or was killed. Try a smaller input, check memory, or use a different approach.")
        elif any(p in combined for p in ("connection refused", "network unreachable", "timed out")):
            hints.append("[SCAFFOLD] Network or service error. Check if the service is running and port is correct.")

    if not hints:
        return ""
    return "\n\n" + "\n".join(dict.fromkeys(hints))  # deduplicate


def _get_task_family_hint(instruction: str) -> str:
    """Return a verifier quality hint based on task-family keyword matching."""
    lower = instruction.lower()
    for keywords, hint in _TASK_FAMILY_HINTS:
        if any(kw in lower for kw in keywords):
            return hint
    return ""


def _extract_bash_commands(text: str) -> list[str]:
    """Extract bash code blocks as whole scripts (preserving heredocs/loops), and bare $ lines."""
    commands: list[str] = []
    lines = text.split("\n")
    in_block = False
    block_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_block:
                script = "\n".join(block_lines).strip()
                if script:
                    commands.append(script)
                block_lines = []
                in_block = False
            else:
                lang = stripped[3:].strip().lower()
                if lang in ("bash", "sh", "shell", ""):
                    in_block = True
            continue

        if in_block:
            block_lines.append(line)
        elif stripped.startswith("$ ") and len(stripped) > 2:
            commands.append(stripped[2:])

    return commands
