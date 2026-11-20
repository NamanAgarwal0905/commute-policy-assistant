# Evaluation Report - Commute Policy Assistant

## 1. Executive summary

The project has a fixed 26-case evaluation set and an automated test suite covering authentication, tenant isolation, ingestion/versioning, retrieval, question handling and evaluation-set integrity.

Latest recorded results:
- **Real-provider evaluation:** 26/26 cases passed.
- **Configuration:** Groq `openai/gpt-oss-20b`, MiniLM embeddings, section-aware chunking.
- **Latency:** p50 969.4 ms; p95 1,963.9 ms over 26 requests.
- **Safety counters:** tenant leakage 0; version leakage 0.
- **LLM calls:** average 1.0; maximum 1.
- **Automated tests:** 95 passed in 371.74 seconds.

The observed p95 is below the 3-second target in this run. With 26 latency samples, it is a useful measured result, not a robust production latency guarantee.

## 2. Evaluation method

- **Set:** `evaluation/questions.jsonl`, 26 cases.
- **Coverage:** direct policy questions across Acme, Northstar and Orbit; paraphrases; cross-company traps; adversarial/prompt-injection cases; and topics not covered by policy.
- **Gold checks:** `tests/test_eval_set.py` validates expected facts against actual policy sections, checks forbidden facts, and confirms not-covered topics are absent.
- **Execution:** each run builds isolated temporary SQLite and Chroma storage, seeds policies, authenticates as the case's company user, and calls the question service through FastAPI's TestClient.
- **Pass criteria:** required facts and citation section for answerable cases; transport-desk direction for not-covered cases; no forbidden facts; zero tenant/version leakage; at most three citations; at most two LLM calls; citations must match retrieved/stored metadata.
- **Latency:** measured per question, including the configured LLM call when used.

## 3. Latest real-provider results

| Metric | Latest result |
|---|---:|
| Provider / model | Groq / `openai/gpt-oss-20b` |
| Embedding | MiniLM (`minilm`) |
| Chunking | Section-aware |
| Cases / passed | **26 / 26** |
| Answer correctness | **1.000** |
| Retrieval hit@3 | **1.000** |
| Citation validity / coverage | **1.000 / 1.000** |
| Abstention precision / recall | **1.000 / 1.000** |
| Tenant leakage / version leakage | **0 / 0** |
| Latency samples | 26 |
| Latency p50 / p95 | 969.4 ms / 1,963.9 ms |
| Average / maximum LLM calls | 1.0 / 1 |
| Indexed chunks | 25 |

These are results from the recorded run, not a guarantee that future provider responses will be identical.

## 4. Automated test suite

Command:

```bash
docker compose exec api pytest --durations=15 -q
```

Latest result:

```text
95 passed in 371.74s (0:06:11)
```

Slowest tests:

| Test | Duration |
|---|---:|
| `test_runner_is_repeatable_and_leak_free` | 210.49 s |
| `test_runner_with_fake_llm_that_always_abstains` | 104.70 s |
| Remaining slowest setup | approximately 1.3–2.0 s each |

The suite passes completely. Most runtime is concentrated in two evaluation-runner tests that execute repeated end-to-end evaluation work. This is a test-suite performance issue, not a failing test or runtime API failure. Pytest uses fake LLMs and offline embeddings; it does not require a live provider.

## 5. Reproduction

```bash
# Automated tests
docker compose exec api pytest -q

# Real-provider 26-case evaluation
docker compose exec api python -m scripts.evaluate

# More latency samples
docker compose exec api python -m scripts.evaluate --repeat 5

# Fixed-vs-section-aware retrieval experiment
docker compose exec api python -m scripts.evaluate --compare
```

Keep provider/model, embedding, chunking configuration and policy fixtures fixed when comparing runs. Store `evaluation/results.jsonl` and `evaluation/metrics.json` from the same run together.

## 6. Bonus: section-aware chunking experiment

The comparison uses the same 26 questions, policy files, hash embedder, extractive stand-in and relevance threshold. Only chunking changes.

| Metric | Fixed windows | Section-aware |
|---|---:|---:|
| Cases passed | 8/26 | **20/26** |
| Answer correctness | 0.308 | **0.769** |
| Retrieval hit@3 | 0.158 | **0.895** |
| Citation validity / coverage | 1.000 / 1.000 | 1.000 / 1.000 |
| Abstention precision / recall | 0.375 / 0.750 | 0.667 / 0.500 |
| Indexed chunks | 15 | 25 |
| Offline latency p95 | 6.2 ms | 7.3 ms |
| Tenant / version leakage | 0 / 0 | 0 / 0 |

**Interpretation:** section-aware chunking keeps policy rules and headings together, improving retrieval and section-level citation correctness in this offline experiment. It adds ten chunks and a heading parser. The improvement is specific to the lexical hash embedder and extractive stand-in; it should not be treated as the expected gain with MiniLM and a real LLM.

## 7. Trade-offs and limitations

1. **Section-aware chunks over fixed windows:** better rule/heading alignment in the measured offline experiment; more vectors and a parser to maintain.
2. **Lenient threshold plus LLM abstention:** protects recall when answerable and not-covered distance ranges overlap; costs an LLM call on some questions that a perfect threshold could reject without generation.
3. **No automatic LLM retry:** avoids doubling latency and quota; malformed output or provider failures return a retry response.
4. **Embedded ChromaDB:** simplifies one-command deployment; the API process owns the vector index, limiting horizontal scaling.
5. **Demo JWT identity store:** self-contained for the case study; production should integrate the organisation's identity provider.
6. **Thread-based timeouts:** a timed-out worker may continue running; production should use cancellable async clients and resource monitoring.

Other limitations include English-only Markdown policies, independent questions without conversation memory, and a finite synthetic evaluation set. Passing tests and evaluation does not prove the system is free of every possible security or answer-quality defect.
