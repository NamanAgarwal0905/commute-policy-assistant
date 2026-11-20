# Commute Policy Assistant

A multi-tenant RAG service that answers employees' transport-policy questions **from their own company's
current policy only**, cites document + section, and says "the policy does not say" when it doesn't.

Stack: Python · FastAPI · Chroma · MiniLM embeddings · Groq/Gemini LLM · JWT · SQLite · Docker.

> Core rule: the LLM *explains* evidence. It never decides identity, tenant, policy version, authorisation or citations - plain code does.

## Run it (one command)

```bash
docker compose up --build
```

Open <http://localhost:8000/docs>. The demo data (3 companies, 6 users, 3 policies) is loaded automatically and idempotently at startup.
With no configuration the LLM is an **offline extractive stand-in** (no key needed). For real answers:

```bash
cp .env.example .env        # then set LLM_PROVIDER=groq (or gemini) and LLM_API_KEY=...  (free tiers; never commit .env)
docker compose up --build
```

| Command | What it does |
|---|---|
| `docker compose exec api pytest -q` | 95 tests, fully offline (fake LLM, offline embedder) |
| `docker compose exec api python -m scripts.evaluate` | run the 26-case evaluation set, write `evaluation/results.jsonl` |
| `docker compose exec api python -m scripts.evaluate --repeat 5` | repeat each question 5x → more latency samples (p50/p95) |
| `docker compose exec api python -m scripts.evaluate --compare` | **bonus**: fixed-size vs section-aware chunking, same cases |
| `docker compose exec api python -m scripts.evaluate --show-distances` | print retrieval distances to help choose `MAX_DISTANCE` |

Without Docker: `pip install -r requirements.txt`, `export JWT_SECRET=<32+ random chars>`, `AUTO_SEED=true uvicorn app.main:app`.

## Try it

Demo users (password `demo-pass`): `acme_employee`, `northstar_employee`, `orbit_employee`, and `<company>_admin`.

```bash
TOKEN=$(curl -s localhost:8000/auth/token -H 'content-type: application/json' \
  -d '{"username":"acme_employee","password":"demo-pass"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s localhost:8000/questions -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"question":"Can I book a cab on Saturday?"}'
```

Response statuses: `answered` (200, with `citations`), `not_covered` (200, points to the transport desk), `retry` (503 + `Retry-After`, "try again").

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /auth/token` | none (demo) | username+password → JWT (`sub`, `company_id`, `role`, `iat`, `exp`) |
| `POST /questions` | employee JWT | `{"question": "..."}` only. A `company_id` field is rejected (422) |
| `POST /admin/policies` | admin JWT | ingest a new policy version for the **admin's own** company |
| `GET /admin/policies/versions`, `/active` | admin JWT | list versions / show active one |
| `GET /health` | none | checks SQLite and Chroma |

Demo of versioning: log in as `acme_admin`, `POST /admin/policies` with `data/policies/acme_transport_policy_v2.md`
(`version: "v2"`) → the escort threshold changes from 8 PM to 9 PM and the Saturday rule changes; v1 stops answering immediately.

## How a question flows

```
JWT ──verify signature, expiry, required claims, pinned algorithm──► user must match SQLite (same company + role)
 └─► company_id (from token ONLY) ─► active version (SQLite) ─► Chroma query WHERE company_id AND policy_version
      └─► re-check every returned chunk's company/version (fail closed) ─► distance threshold ─► ≤3 chunks
           └─► 0 chunks? "not covered" (no LLM call)
           └─► ONE LLM call (passages delimited + escaped, told they are untrusted data)
                └─► validate JSON; every cited id must be one of the retrieved ids
                     └─► citations built from OUR stored metadata (document, section, chunk id)
```

| File | Responsibility |
|---|---|
| `app/main.py` | wiring, startup seed, `/health` |
| `app/api/{auth,questions,policies}.py` | HTTP only |
| `app/core/{config,security,container}.py` | settings, JWT, object wiring |
| `app/db/metadata.py` | SQLite: users, policy versions, **active version** |
| `app/db/chroma.py` | Chroma; the tenant filter is built in one function |
| `app/db/embeddings.py` | MiniLM (real) + offline hash embedder (tests) |
| `app/services/ingestion.py` | parse → chunk → index → verify → activate / roll back |
| `app/services/retrieval.py` | filtered search, tenant re-check, threshold, top-3 |
| `app/services/llm.py` | prompt, provider call, strict output validation |
| `app/services/answer.py` | the orchestration + failure handling |
| `scripts/evaluate.py` | evaluation runner and metrics |

## Design decisions

**Where the LLM is used - and where it is not.** The LLM does one thing: phrase a short answer from ≤3 passages. Plain code does authentication, tenant selection, version selection, filtering, the relevance cut-off, JSON validation, citation building, timeouts and error mapping. Those must be deterministic and testable; an LLM can be talked out of any of them.

**Tenant isolation (defence in depth).** (1) company comes only from a verified JWT, cross-checked with the user table; (2) `extra="forbid"` on the request body rejects a `company_id`; query-string/header overrides are simply never read; (3) Chroma is only reachable through `ChunkStore.query(company_id, version, ...)`, which always adds `company_id AND policy_version` - there is no "search everything" function and no endpoint exposing filters; (4) `Retriever` re-checks every returned row and raises `TenantViolation` → `retry`, so a bug or corrupted index fails closed; (5) the LLM never even sees another company's text, so a fully obedient model has nothing to leak.

**Prompt injection.** Policy text and the question are untrusted. They are escaped (`<`,`>`), wrapped in `<passage>`/`<question>` tags, and the system prompt says never to follow instructions inside them. That is only a soft layer. The hard layers are above: even if the model is fooled it cannot see other tenants, and any citation id it invents is rejected. Acme's policy contains a deliberate `SYSTEM OVERRIDE` fixture to prove it.

**Citations are not trusted from the model.** The model returns chunk ids; the server checks each id is in the retrieved set (≤3), then builds `{document, section, chunk_id}` from stored metadata. Free text from the model is never used as a citation. (A second layer: citation building does a dict lookup that would also fail closed on an unknown id.)

**Policy versions.** The active version is a row in SQLite (`status='active'`) with a partial unique index → at most one active version per company, enforced by the database. Ingestion registers the new version as `staging`, indexes all chunks, verifies count and searchability, then flips old→`inactive`, new→`active` in **one transaction**. Any failure marks it `failed`, deletes the partial chunks and leaves the old version answering. Old versions stay in Chroma for audit but are never retrievable (the query always filters on the active version). *Limitation:* SQLite and Chroma have no shared transaction; a crash between "chunks written" and "activated" leaves orphan vectors. They are never served, and `reconcile_staging()` removes them at startup.

**Staying within the numbers.**
- *≤2 LLM calls:* the normal path makes **1** call; "nothing relevant found" makes **0**; there are **no retries**, so failures cost one call. `AnswerService._call_llm` hard-caps at 2 and the response reports `meta.llm_calls`.
- *≤3 cited chunks:* `max_evidence=3` caps what is retrieved, what the LLM sees and what can be cited; the output validator also rejects >3 ids.
- *p95 ≤ 3 s:* small prompt (≤3 short passages), `max_tokens=200`, `temperature=0`, a fast model (Groq llama-3.1-8b-instant default), local embeddings, warm-up at startup, pooled HTTP client. Timeouts: retrieval 0.8 s, LLM 2.0 s (+0.5 s wall-clock guard). The retrieval+validation part measured ~7 ms p95 offline; the LLM network call dominates, so the 3 s figure **must be measured with your real provider** (see the report).

**Failure behaviour.** Provider error/quota/timeout, malformed or non-JSON output, invented ids, vector store down, slow retrieval, DB errors, unexpected exceptions → all return the same `retry` response (HTTP 503, "please try again", no citations, no partial answer). No relevant evidence or the model abstaining → `not_covered` pointing to the transport desk.

## Trade-offs

1. **Section-aware chunking over fixed windows** (the bonus experiment). One chunk per policy section keeps a rule with its condition and gives exact section citations. Cost: chunk sizes vary, and a section that mixes two topics is retrieved as a whole. Result in `EVALUATION_REPORT.md`.
2. **Threshold + LLM abstention, not threshold alone.** Distance cut-offs cannot separate "policy is silent" from "poorly phrased question" (shown by `--show-distances`: answerable and not-covered distances overlap). So the threshold is deliberately lenient and the LLM says `answerable:false`. Cost: abstention quality depends on the LLM.
3. **No LLM retry on bad output.** A retry would fix some malformed outputs but double worst-case latency and burn free-tier quota. We return "try again" instead.
4. **Embedded Chroma instead of a separate Chroma server.** One container, one command, fewer moving parts. Cost: the API process owns the index (no horizontal scaling). Swapping to `chromadb.HttpClient` is a one-line change in `app/db/chroma.py`.
5. **Hash-based demo auth/JWT issuer in the app.** Fine for the case study; production would use the company's identity provider.
6. **Thread-based timeouts.** `run_with_timeout` abandons a stuck worker rather than killing it; a pool of 8 could be exhausted by 8 simultaneous hangs. Acceptable here; a real deployment would use async clients with cancellation.

## Known limits

- No PDF parsing: policies are Markdown with `## ` section headings.
- English only; no conversation memory (each question stands alone).
- The offline `extractive` LLM and `hash` embedder are test stand-ins and cannot reflect real quality. Real MiniLM and Groq/Gemini numbers must be generated by running the evaluation on your machine.
- Concurrent ingestion of the same company/version is not locked (the unique constraint makes the loser fail safely).

## 2-minute recording checklist

1. `docker compose up --build` → `/docs` open. 2. Token for `acme_employee`; ask the Saturday question → answer + citation. 3. Ask "pets in cabs?" → not covered + transport desk. 4. Same Saturday question as `northstar_employee` → different answer (24 hours' notice). 5. As Acme, ask for "Northstar's escort threshold" and send a body with `company_id` → no leak / 422. 6. As `acme_admin` ingest v2 → ask the escort question → now 9 PM, `policy_version: v2`. 7. `pytest -q` and `scripts.evaluate` output.
