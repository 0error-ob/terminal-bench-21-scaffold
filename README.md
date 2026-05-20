# 0error Ledger — Terminal-Bench 2.1 Scaffold Agent

Scaffold agent for [Terminal-Bench 2.1](https://www.tbench.ai/leaderboard/terminal-bench/2.1).

**Submitted: 66/89 = 74.2% (pass@5, k=5, n=445 trials)** with Claude Opus 4.7 via OpenRouter.

| Rank | Agent | Model | Score |
|------|-------|-------|-------|
| #1 | Codex CLI | GPT-5.5 | 83.4% ±2.2 |
| #2 | Terminus 2 | GPT-5.5 | 78.2% ±2.4 |
| #3 | Terminus 2 | Gemini 3 Pro | 74.4% ±2.6 |
| **→ This** | **0error Ledger** | **Claude Opus 4.7** | **74.2%** |
| #6 | Claude Code (official) | Claude Opus 4.7 | 69.7% ±2.7 |

Same model as Anthropic's Claude Code submission (#6, 69.7%); the +4.5pp is from the scaffold.

## Design

Four-phase loop:

1. **Probe** — read the task, identify acceptance criteria, set up a ledger
2. **Execute** — work incrementally, write ledger entries after each significant action
3. **Verify** — run a verifier command that can fail; check against all stated requirements
4. **Stop** — emit `TASK_COMPLETE` only when the verifier passes

Mechanisms:

- *Verifier gating* — re-read requirements, check exact output format, run an adversarial case before declaring complete
- *Ledger state* — decisions and results in a file, not in the conversation
- *Failure classification* — `_classify_failures` triggers a retry path on known stuck states
- *Cost tracking* — token + USD per task

## Usage

```bash
harbor run \
  -d terminal-bench/terminal-bench-2-1 \
  --agent-import-path agent.scaffold_agent:ScaffoldAgent \
  -m openai/anthropic/claude-opus-4-7 \
  -n 4 \
  -k 5 \
  -y
```

- [Harbor](https://github.com/laude-institute/harbor) `0.6.4`
- `LLM_API_BASE` + `LLM_API_KEY` set to your OpenRouter endpoint and key

## Files

| File | Description |
|------|-------------|
| `agent/scaffold_agent.py` | Agent implementation |
| `agent/__init__.py` | Package marker for `agent.scaffold_agent:ScaffoldAgent` |
| `SUBMISSION_NOTES.md` | Run config, exception breakdown, benchmark-awareness disclosure, code provenance |

## Claim boundary

- No per-task answers, no task-name detection, no trajectory replay
- No Claude-specific branches in code, but only Claude Opus 4.7 is tested here
