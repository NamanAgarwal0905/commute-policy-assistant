# Evaluation Report

## 1. What was measured, and what was not

| Configuration | Status |
|---|---|
| Offline stand-ins: `EMBEDDING_PROVIDER=hash`, `LLM_PROVIDER=extractive` | **Measured** (Section 4). Reproducible with no network or key. |
| Real models: `EMBEDDING_PROVIDER=minilm` + Groq/Gemini | **Not measured in the build environment** (network to model hosts is blocked there). Fill in Section 6 by running the commands in the README on your machine. |

The offline numbers are **not** a claim about real quality. The extractive stand-in never abstains by itself and the hash embedder is lexical, so they mostly demonstrate the harness, the isolation guarantees and the retrieval-structure experiment. I did not tune either stand-in to the evaluation set.

## 2. Method

- **Set:** `evaluation/questions.jsonl`, 26 cases, fields `id, input, expected, actual, pass, notes`. The committed file has `actual`/`pass` = null; the runner writes the filled copy to `evaluation/results.jsonl` (the gold file is never rewritten).
- **Mix:** 8 Acme, 6 Northstar, 3 Orbit direct questions; 3 paraphrases (P01-P03, plus A02); 3 cross-company traps (X01-X03); 2 adversarial/injection cases (SEC01-SEC02); 4 "not covered" cases (A05 pets, N06 errands, O03 airport, NC04 EV charging).
- **Gold is verified:** `tests/test_eval_set.py` checks that every required fact appears in the cited section of the actual policy file, that "forbidden" values do not appear in the asker's own policy, and that not-covered topics are really absent.
- **Execution:** each run builds a fresh temp SQLite + Chroma from `data/policies/`, logs in through `/auth/token` as the case's company user, and calls `POST /questions` (body = question only). The HTTP layer is FastAPI's in-process TestClient.
- **Judging (a case passes only if all hold):** status is allowed; for `answered`: every required fact is in the answer (case/number-normalised: "24-hour"="24 hours", "INR 1,500"="1500"), a cited section matches, ≥1 citation; for `not_covered`: message mentions the transport desk; no forbidden text; no cross-tenant or old-version chunk retrieved; ≤3 citations; ≤2 LLM calls; every citation matches stored metadata.
- **Metrics:** answer correctness (pass rate); retrieval hit@3 (a gold section is among the ≤3 chunks given to the LLM, answerable cases); citation validity and coverage; abstention precision/recall; tenant and version leakage counts (must be 0); latency p50/p95 (client-side, per request, includes LLM when one is used); LLM calls average/max.

## 3. Reproduce

```bash
docker compose exec api python -m scripts.evaluate --repeat 5      # main run
docker compose exec api python -m scripts.evaluate --compare       # bonus before/after
docker compose exec api pytest -q                                    # 95 tests, offline
```
Offline numbers below: `EMBEDDING_PROVIDER=hash LLM_PROVIDER=extractive python -m scripts.evaluate --repeat 5` (Linux sandbox, Python 3.13, 130 latency samples).

## 4. Results (offline stand-ins, section-aware chunking)

| Metric | Value |
|---|---|
| Cases / passed | 26 / 20 |
| Answer correctness | 0.769 |
| Retrieval hit@3 | 0.895 |
| Citation validity / coverage | 1.0 / 1.0 |
| Abstention precision / recall | 0.667 / 0.50 |
| **Tenant leakage / version leakage** | **0 / 0** |
| LLM calls avg / max | 0.88 / 1 (max allowed 2) |
| Latency p50 / p95 | 5.6 ms / 6.8 ms (no network LLM: measures our own pipeline only) |

Test suite: 95 passed (auth, isolation, ingestion/versioning/failed ingestion, retrieval, question handling incl. timeouts and malformed output, evaluation-set verification and repeatability). I also sabotaged the safety code on purpose (removed the post-retrieval tenant check; removed the company filter in Chroma) and confirmed tests failed; removing the explicit cited-id check was *not* caught by a test because a second layer (citation lookup) fails closed with identical behaviour.

## 5. Bonus: chunking experiment (same 26 cases, same policies/prompt/embedder/threshold/machine)

Baseline = fixed 500-char windows with 100 overlap, ignoring headings (section label = last heading before the window). Candidate = one chunk per policy section with the heading in the text.

| Metric | Fixed (before) | Section-aware (after) |
|---|---|---|
| Answer correctness | 0.308 (8/26) | **0.769 (20/26)** |
| Retrieval hit@3 | 0.158 | **0.895** |
| Citation validity | 1.0 | 1.0 |
| Abstention precision / recall | 0.375 / 0.75 | 0.667 / 0.50 |
| Chunks in index (3 policies) | 15 | 25 |
| Latency p95 (offline) | 6.2 ms | 7.3 ms |
| Tenant / version leakage | 0 / 0 | 0 / 0 |

**Reading it honestly.** The gap is large partly because the offline embedder is lexical and fixed windows blur topics together, and because fixed chunks often get the *wrong section label* (e.g. an airport rule labelled as "Ad-hoc trips"), which fails the citation check even when the text is right. With a real embedding model the gap will likely shrink; re-run `--compare` to see. The abstention numbers *worsen* for section chunking because small mismatched chunks no longer fall below the threshold by accident - abstention is the LLM's job (see trade-off 2).
**Cost:** +10 chunks (more vectors), slightly higher retrieval latency in this run, and one more concept (heading parser) to maintain. The parser is ~15 lines and fails loudly on a policy with no `## ` headings.

## 6. Real-model results - TO FILL IN after running on your machine

| Metric | Value |
|---|---|
| Provider / model / embedding | e.g. groq / llama-3.1-8b-instant / minilm |
| Machine / network | |
| Cases passed | / 26 |
| Retrieval hit@3 | |
| Abstention precision / recall | |
| Latency p50 / **p95** (n ≥ 100, use `--repeat 5`) | / ms (target ≤ 3000) |
| LLM calls avg / max | |
| `MAX_DISTANCE` chosen via `--show-distances` | |

Do not claim the 3-second target until this row is filled with a measured number.

## 7. Known failures (offline run) and why

| Case | Failure | Cause |
|---|---|---|
| A05 pets, O03 airport | answered instead of "not covered" | Extractive stand-in cannot abstain; word overlap ("trip", "approval") puts a chunk under the threshold. A real LLM is expected to return `answerable:false`; verify in Section 6. |
| P03 "reserve a ride" | wrongly `not_covered` | Hash embedder has no synonyms ("reserve" ≠ "book"): best distance 0.97 > threshold. MiniLM should handle it. |
| A08 cost cap, N04 no-show fee, X02 | wrong section retrieved | Lexical embedder ranks a chunk with more shared common words above the right one. |

Other limits: English only; Markdown only; each question is independent; contradictory statements *inside* one policy are not detected; `extractive` quality says nothing about real hallucination rate; thread timeouts abandon rather than cancel stuck calls; worst-case failure-path latency is retrieval 0.8 s + LLM guard 2.5 s ≈ 3.3 s (lower `LLM_TIMEOUT_SECONDS` to tighten).

## 8. Trade-off chosen (required)

*Threshold lenient + LLM abstention, rather than a strict relevance threshold.* `--show-distances` shows answerable and not-covered questions overlapping in distance (offline: answerable max 0.965, not-covered min 0.775), so a strict cut-off would either refuse valid questions or let silent topics through. A lenient cut-off protects recall; the LLM must then abstain, and the evaluation tracks abstention precision/recall to measure that. Cost: one LLM call on silent-topic questions that a perfect threshold would have answered with zero calls.
