# Commute Policy Assistant

A multi-tenant RAG service that answers employees' transport-policy questions using only their company's active policy, cites document and section, and abstains when the policy does not cover a topic.

**Stack:** Python 3.11, FastAPI, embedded/persistent ChromaDB, MiniLM embeddings, Groq/Gemini LLM adapters, JWT, SQLite and Docker Compose.

> The LLM explains retrieved evidence. Backend code owns identity, tenant selection, active policy version, retrieval filters and citation construction.

## Run it

```bash
docker compose up --build
```

Open http://localhost:8000/docs and http://localhost:8000/health.

The demo seed loads three fictional companies, six demo users and baseline policies automatically. Startup seeding is idempotent.

### Optional: configure a real LLM

The default uses the offline extractive adapter and needs no API key. To use Groq or Gemini:

1. Copy `.env.example` to `.env`.
2. Set `LLM_PROVIDER`, `LLM_API_KEY` and optionally `LLM_MODEL`.
3. Keep `.env` local; never commit it.
4. Recreate the service with `docker compose up --build -d`.

The measured real-provider configuration for this submission was Groq `openai/gpt-oss-20b`, MiniLM embeddings (`minilm`) and section-aware chunking.

## Test and evaluate

```bash
# Offline automated suite (fake LLM and offline embeddings)
docker compose exec api pytest -q

# Run the 26-case evaluation using the configured providers
docker compose exec api python -m scripts.evaluate

# Repeat each question to collect more latency samples
docker compose exec api python -m scripts.evaluate --repeat 5

# Bonus: compare fixed-size and section-aware chunking
docker compose exec api python -m scripts.evaluate --compare

# Inspect retrieval distances
docker compose exec api python -m scripts.evaluate --show-distances
```

Latest automated test result: **95 passed in 371.74 seconds**. Most runtime is in two evaluation-runner tests; the suite uses fake LLMs and offline embeddings and does not require a live provider.

Latest real-provider evaluation: **26/26 passed**. The measured p50 was 969.4 ms and p95 was 1,963.9 ms over 26 requests. This is a run-specific measurement, not a statistically stable production SLO. See `EVALUATION_REPORT.md` and `evaluation/metrics.json`.

## Try the API

Demo password: `demo-pass`

- Employees: `acme_employee`, `northstar_employee`, `orbit_employee`
- Admins: `acme_admin`, `northstar_admin`, `orbit_admin`

```bash
TOKEN=$(curl -s http://localhost:8000/auth/token \
  -H 'content-type: application/json' \
  -d '{"username":"acme_employee","password":"demo-pass"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s http://localhost:8000/questions \
  -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"question":"Can I book a cab on Saturday?"}'
```

Question outcomes:
- `answered`: grounded answer with citations.
- `not_covered`: policy does not specify the requested rule; employee is directed to the transport desk.
- `retry`: dependency failure or timeout; retryable response without a partial answer.

## API surface

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | None | Health check |
| `POST /auth/token` | None (demo issuer) | Demo credentials to signed JWT |
| `POST /questions` | Employee JWT | Ask a policy question; body contains only `question` |
| `POST /admin/policies` | Admin JWT | Ingest a new version for the admin's company |
| `GET /admin/policies/versions` | Admin JWT | List the caller's company policy versions |
| `GET /admin/policies/active` | Admin JWT | Inspect the caller's active version |

The company is derived from the verified JWT and checked against the identity store. A client-supplied company identifier is never an authorization input.

## Architecture and trust boundaries

```text
JWT -> verify signature/expiry/claims/algorithm
    -> cross-check user, company and role in SQLite
    -> read active policy version from SQLite
    -> Chroma query filtered by company_id AND policy_version
    -> re-check every returned chunk (fail closed)
    -> relevance threshold and maximum 3 evidence chunks
    -> no evidence: not_covered (0 LLM calls)
    -> evidence: one bounded LLM call
    -> validate structured output and cited chunk IDs
    -> build citations from trusted stored metadata
```

### Main components

| Component | Responsibility |
|---|---|
| FastAPI routes | Request validation, auth dependencies and HTTP responses |
| JWT/security | Verify token and resolve trusted identity |
| SQLite | Companies, users, policy versions and active-version state |
| ChromaDB | Persistent vector storage and similarity search, not authorization |
| Ingestion service | Parse, chunk, index, verify, activate or clean up staged versions |
| Retrieval service | Tenant/version filters, post-query checks, threshold and top-3 evidence cap |
| LLM adapter | Bounded provider call and structured-output parsing |
| Answer service | Retrieval, abstention, generation, citation validation and failure handling |
| Evaluation runner | Fixed JSONL cases and quality/safety/latency metrics |

## Design decisions

### Tenant isolation
1. Company comes only from a verified JWT and is cross-checked against the user record.
2. The question request does not accept a company override.
3. Chroma queries always filter by company and active policy version.
4. Retrieved chunks are checked again before reaching the LLM.
5. The LLM never receives another tenant's policy text.

### Prompt injection and citations
Policy text and user questions are untrusted data. Prompt instructions tell the model not to follow embedded instructions, but prompt wording is not the security boundary: tenant filtering prevents cross-company content from entering the prompt. The model returns chunk IDs; the server validates them against retrieved evidence and constructs citations from stored metadata.

### Policy versioning
A new version is staged and indexed before activation. After index verification, SQLite updates the active-version state in a transaction. If ingestion fails, the old active version remains active and staged chunks are cleaned up where possible.

**Trade-off:** SQLite and ChromaDB do not share a distributed transaction. A crash can leave orphaned staged vectors, but they are not eligible for retrieval because the active-version filter is authoritative. Startup reconciliation handles interrupted staging work.

### Bounded work and latency
- At most 3 evidence chunks are sent to the model.
- Normal answer path: 1 LLM call; no-evidence path: 0 calls.
- No automatic LLM retry, avoiding extra latency and quota use.
- Retrieval and LLM calls have configured timeouts.
- Latest measured p95: 1,963.9 ms over 26 requests (see the sample-size caveat above).

## Retrieval experiment

The bonus experiment compared fixed-size chunks (500 characters with 100-character overlap) against section-aware chunks. On the same 26-case offline hash/extractive evaluation, section-aware chunking improved correctness from 8/26 (0.308) to 20/26 (0.769) and retrieval hit@3 from 0.158 to 0.895. The index increased from 15 to 25 chunks. See `EVALUATION_REPORT.md`.

## Known limitations

- Demo identity provider and credentials are for this self-contained case study, not production authentication.
- Ingestion targets Markdown with section headings; PDF parsing is not included.
- English-only policies; each question is independent (no conversation memory).
- The evaluation set is finite and synthetic; passing it does not prove the absence of all hallucinations or security defects.
- Thread-based timeouts cannot forcibly terminate a blocked worker; production should use cancellable async clients and monitoring.
- Embedded ChromaDB simplifies deployment but the API process owns the index, limiting horizontal scaling.

## Repository hygiene

- Do not commit `.env`, local virtual environments or runtime storage.
- Keep `evaluation/questions.jsonl` as the fixed gold set.
- `evaluation/results.jsonl` is generated by the runner; do not hand-edit outcomes.
- Keep `evaluation/metrics.json` and `evaluation/results.jsonl` from the same run.
- Do not run `docker compose down -v` unless you intentionally want to delete persistent storage.
