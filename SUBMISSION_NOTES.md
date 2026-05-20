# Terminal-Bench 2.1 — Submission Notes

Run `tbench-2.1-k5-v01`.

## Run configuration

| Item | Value |
|------|-------|
| Job name | `tbench-2.1-k5-v01` |
| Dataset | `terminal-bench/terminal-bench-2-1` (89 tasks) |
| k | 5 |
| Total trials | 445 |
| Model | `openai/anthropic/claude-opus-4-7` (Claude Opus 4.7 via OpenRouter) |
| Harbor | 0.6.4 |
| Concurrency | `-n 4` |
| `timeout_multiplier` | 1.0 |
| Agent / verifier / resource overrides | None |
| Agent network access | Not allowed to tbench.ai or TB GitHub repo |
| VM | Nebius L40S, Ubuntu 24.04 |

### Model strings

| Where | String |
|-------|--------|
| `metadata.yaml` `model_name` | `anthropic/claude-opus-4-7` |
| `result.json` `agent.model_name` | `openai/anthropic/claude-opus-4-7` (LiteLLM `openai/` prefix = OpenAI-compatible endpoint) |
| `result.json` `agent_info.model_info.name` | `anthropic/claude-opus-4-7` |

All refer to the same model.

## Result

| Metric | Value |
|--------|-------|
| Tasks pass@5 | **66 / 89 = 74.2%** |
| Passing trials | 177 / 445 |

## Compliance

- [x] `timeout_multiplier = 1.0`
- [x] No `override_timeout_sec` / `max_timeout_sec`
- [x] No `override_cpus` / `override_memory_mb` / `override_storage_mb`
- [x] Agent does not access tbench.ai or TB GitHub repo
- [x] No per-task pre-recorded trajectories
- [x] No task-name detection
- [x] 5 trials per task, 445 result.json total

## Infrastructure exceptions

215 of 445 trials ended with an exception:

| Exception type | Count |
|----------------|-------|
| Docker Hub anonymous pull rate-limit | 165 |
| Command timeout (300s) | 31 |
| Command timeout (60s) | 4 |
| `AgentTimeoutError` (9 × 900s, 1 × 1800s, 1 × 3600s — Harbor task-dependent budget) | 11 |
| LLM provider `InternalServerError` | 3 |
| LLM provider `BadRequestError` | 1 |
| **Total** | **215** |

All 215 are counted as failures in the 66/89 figure.

Every task had at least 2 trials that successfully pulled the Docker image and entered agent execution.

Eight tasks had all 5 trials exception: `caffe-cifar-10`, `compile-compcert`, `extract-moves-from-video`, `fix-ocaml-gc`, `llm-inference-batching-scheduler`, `mteb-leaderboard`, `qemu-alpine-ssh`, `train-fasttext`. For each: 2/5 Docker rate-limit, 3/5 command-timeout or `AgentTimeoutError` (i.e., image pulled, agent ran, ran out of time).

Happy to re-run any subset at maintainer request.

## Code provenance

| Item | Value |
|------|-------|
| Repo | `github.com/0error-ob/terminal-bench-21-scaffold` |
| Tag | `tb21-submission-v1` |
| `agent/scaffold_agent.py` SHA256 | `e51752241ea0a0ab9bdb13390c68d25a573ca416457fe30ee1168eb0895f1339` |
| `agent/__init__.py` SHA256 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` (empty) |

```bash
git clone --branch tb21-submission-v1 https://github.com/0error-ob/terminal-bench-21-scaffold
sha256sum agent/scaffold_agent.py
# e51752241ea0a0ab9bdb13390c68d25a573ca416457fe30ee1168eb0895f1339
```

## Pass list (66)

`adaptive-rejection-sampler`, `bn-fit-modify`, `break-filter-js-from-html`, `build-cython-ext`, `build-pmars`, `build-pov-ray`, `cancel-async-tasks`, `circuit-fibsqrt`, `cobol-modernization`, `code-from-image`, `configure-git-webserver`, `constraints-scheduling`, `crack-7z-hash`, `custom-memory-heap-crash`, `db-wal-recovery`, `distribution-search`, `dna-assembly`, `dna-insert`, `extract-elf`, `feal-differential-cryptanalysis`, `feal-linear-cryptanalysis`, `financial-document-processor`, `fix-code-vulnerability`, `fix-git`, `git-leak-recovery`, `git-multibranch`, `headless-terminal`, `hf-model-inference`, `kv-store-grpc`, `large-scale-text-editing`, `largest-eigenval`, `log-summary-date-ranges`, `mailman`, `mcmc-sampling-stan`, `merge-diff-arc-agi-task`, `modernize-scientific-stack`, `mteb-retrieve`, `multi-source-data-merger`, `nginx-request-logging`, `openssl-selfsigned-cert`, `overfull-hbox`, `password-recovery`, `path-tracing`, `polyglot-c-py`, `polyglot-rust-c`, `portfolio-optimization`, `prove-plus-comm`, `pypi-server`, `pytorch-model-cli`, `pytorch-model-recovery`, `qemu-startup`, `query-optimize`, `regex-log`, `reshard-c4-data`, `rstan-to-pystan`, `sam-cell-seg`, `sanitize-git-repo`, `schemelike-metacircular-eval`, `sparql-university`, `sqlite-db-truncate`, `sqlite-with-gcov`, `torch-pipeline-parallelism`, `torch-tensor-parallelism`, `tune-mjcf`, `vulnerable-secret`, `write-compressor`

## Fail list (23)

`caffe-cifar-10`, `chess-best-move`, `compile-compcert`, `count-dataset-tokens`, `extract-moves-from-video`, `filter-js-from-html`, `fix-ocaml-gc`, `gcode-to-text`, `gpt2-codegolf`, `install-windows-3.11`, `llm-inference-batching-scheduler`, `make-doom-for-mips`, `make-mips-interpreter`, `model-extraction-relu-logits`, `mteb-leaderboard`, `path-tracing-reverse`, `protein-assembly`, `qemu-alpine-ssh`, `raman-fitting`, `regex-chess`, `train-fasttext`, `video-processing`, `winning-avg-corewars`

## Reproducibility

```bash
harbor run -d terminal-bench/terminal-bench-2-1 \
  --agent-import-path agent.scaffold_agent:ScaffoldAgent \
  -m openai/anthropic/claude-opus-4-7 \
  -n 4 -k 5 -y
```

- `LLM_API_BASE=https://openrouter.ai/api/v1`, `LLM_API_KEY=<your OpenRouter key>`
- Cost: $430.08 USD on OpenRouter (May 2026 pricing)
- Wall clock: ~8–10h on 4-CPU / 16-GB VM with `-n 4`
- `docker login` first to avoid Docker Hub anonymous rate-limit
