# Commute Policy Assistant

A multi-tenant, Retrieval-Augmented Generation (RAG) application that answers employees' commute and transport-policy questions using **only their own company's active policy**.

The assistant provides answers grounded in policy documents, cites the relevant document and section, and explicitly says when a question is not covered.

**Core principle:** The LLM explains the evidence. Authentication, tenant isolation, policy-version selection, retrieval filtering, and citation validation are handled by application code.

## 1. Quick Start — Run the Entire Application

### Prerequisites

- Docker Desktop installed and running
- Git

You do not need to install Python, create a virtual environment, or configure an API key to run the offline demo.

### Step 1: Clone the repository

```bash
git clone https://github.com/NamanAgarwal0905/commute-policy-assistant.git
cd commute-policy-assistant
```

### Step 2: Start the application

Run this **single command**:

```bash
docker compose up --build
```

That's it!

Docker builds the application, installs its dependencies, and starts the FastAPI service. Demo data is automatically initialized at startup.

### Step 3: Verify that it is running

Open:

**http://localhost:8000/docs**

This opens the interactive Swagger UI, where you can explore and test the API endpoints.

You can also check the health endpoint:

```text
http://localhost:8000/health
```

The application includes demo data for three fictional companies: Acme, Northstar, and Orbit.

> **Default mode:** The application uses an offline extractive LLM stand-in when no provider is configured. No API key is required for the basic demo.

---

## 2. Configure a Real LLM (Optional)

The application supports Groq and Gemini in addition to the offline extractive provider.

If you want to use a real LLM:

### Step 1: Create your environment file

**Windows PowerShell:**

```powershell
Copy-Item .env.example .env
```

**Linux/macOS:**

```bash
cp .env.example .env
```

### Step 2: Update `.env`

For Groq:

```dotenv
LLM_PROVIDER=groq
LLM_API_KEY=your_groq_api_key
LLM_MODEL=
```

For Gemini:

```dotenv
LLM_PROVIDER=gemini
LLM_API_KEY=your_gemini_api_key
LLM_MODEL=
```

Leave `LLM_MODEL` empty to use the provider-specific default documented in `.env.example`.

Never commit `.env` or expose your API key.

### Step 3: Restart the application

```bash
docker compose up --build
```

The application will now use the configured provider.

---

## 3. Run Automated Tests

Keep the application running and open a **second terminal** in the project directory.

Run:

```bash
docker compose exec api pytest -q
```

The automated test suite covers:

- Authentication and JWT validation
- Tenant isolation and cross-company access prevention
- Policy ingestion and version management
- Retrieval correctness
- Question-answering behaviour
- Evaluation-set validation

The tests use controlled test doubles and offline components where appropriate, so they do not require a real LLM API key.

---

## 4. Run the Evaluation Suite

The project includes a 26-question evaluation dataset.

Run:

```bash
docker compose exec api python -m scripts.evaluate
```

The evaluation measures:

| Metric | What it measures |
|---|---|
| Answer correctness | Whether the response matches the expected answer |
| Retrieval Hit@3 | Whether relevant evidence appears in the top 3 retrieved chunks |
| Citation validity | Whether citations refer to retrieved evidence |
| Citation coverage | Whether required sources are cited |
| Abstention precision | Whether abstentions are appropriate |
| Abstention recall | Whether unsupported questions are correctly rejected |
| Tenant leakage | Whether another company's data is exposed |
| Version leakage | Whether inactive policy versions are retrieved |
| p50 / p95 latency | Response-time performance |

### Latest evaluation results

<text>Measured using Groq with `openai/gpt-oss-20b` and MiniLM embeddings.</text>

| Metric | Result |
|---|---:|
| Evaluation cases | 26 |
| Passed | **26 / 26** |
| Answer correctness | 1.00 |
| Retrieval Hit@3 | 1.00 |
| Citation validity | 1.00 |
| Citation coverage | 1.00 |
| Abstention precision | 1.00 |
| Abstention recall | 1.00 |
| Tenant leakage | 0 |
| Version leakage | 0 |
| Average LLM calls | 1 |
| p50 latency | 1,188.2 ms |
| p95 latency | **1,591.5 ms** |

These are results from the measured evaluation run, not a guarantee of performance across all possible questions.

The evaluation generates:

- `evaluation/results.jsonl`
- `evaluation/metrics.json`

### Additional evaluation commands

```bash
# Repeat each question five times for additional latency samples
docker compose exec api python -m scripts.evaluate --repeat 5

# Compare fixed-size and section-aware chunking
docker compose exec api python -m scripts.evaluate --compare

# Inspect retrieval distances
docker compose exec api python -m scripts.evaluate --show-distances
```

---

## 5. Start the Interactive Terminal Chatbot

The project also includes a simple terminal-based chatbot.

Make sure Docker Compose is running, then open another terminal and execute:

```bash
docker compose exec api python -m scripts.chat
```

### Step 1: Log in

Use one of the demo employee accounts:

| Company | Username | Password |
|---|---|---|
| Acme | `acme_employee` | `demo-pass` |
| Northstar | `northstar_employee` | `demo-pass` |
| Orbit | `orbit_employee` | `demo-pass` |

Admin accounts are also available:

- `acme_admin`
- `northstar_admin`
- `orbit_admin`

The demo password is `demo-pass`.

### Step 2: Ask questions

For example:

```text
You: Can I book a cab on Saturday?

Assistant (answered):
[Policy-based answer]

Sources:
- acme_transport_policy_v1.md
  Section: Weekend Travel
```

You can also try questions that are not covered by the policy:

```text
You: Are pets allowed in company cabs?

Assistant (not_covered):
The current transport policy does not cover this.
Please contact your transport desk.
```

Type `exit` or `quit` to end the conversation.

---

## 6. API Endpoints

The API is built using FastAPI.

| Endpoint | Authentication | Purpose |
|---|---|---|
| `POST /auth/token` | None (demo) | Authenticate a user and issue a JWT |
| `POST /questions` | Employee JWT | Ask a transport-policy question |
| `POST /admin/policies` | Admin JWT | Ingest a new policy version |
| `GET /admin/policies/versions` | Admin JWT | List policy versions |
| `GET /admin/policies/active` | Admin JWT | View the active policy version |
| `GET /health` | None | Check application health |

### Example: Authentication

```json
{
  "username": "acme_employee",
  "password": "demo-pass"
}
```

The response contains an access token.

### Example: Ask a question

```json
{
  "question": "Can I book a cab on Saturday?"
}
```

Pass the JWT in the request header:

```text
Authorization: Bearer <access_token>
```

**Important:** The question API does not accept a client-provided `company_id`. The company is derived exclusively from the verified JWT.

### Response statuses

| Status | HTTP code | Meaning |
|---|---:|---|
| `answered` | 200 | A grounded answer was generated |
| `not_covered` | 200 | The policy does not provide sufficient evidence |
| `retry` | 503 | A temporary service or processing failure occurred |

---

## 7. Policy Versioning Demo

The application supports policy ingestion and version management.

For example, Acme has two policy versions:

- `acme_transport_policy_v1.md`
- `acme_transport_policy_v2.md`

To demonstrate versioning:

1. Log in using `acme_admin`.
2. Submit a new policy version through `POST /admin/policies`.
3. Ingest Acme's v2 policy.
4. Ask a question about the escort timing.
5. Observe that the response uses v2 instead of v1.

The Acme policy changes its escort threshold from 8 PM to 9 PM in v2, along with its Saturday travel rule.

The previous version becomes inactive and is no longer used for answering questions.

---

## 8. How the System Works

### High-level architecture

```text
                 Employee
                    |
                    v
              FastAPI API
                    |
                    v
              JWT Validation
                    |
                    v
          Identify Company from JWT
                    |
                    v
          Get Active Policy Version
                    |
                    v
          ChromaDB Vector Retrieval
                    |
          Company + Version Filter
                    |
                    v
            Evidence Validation
                    |
             Top 3 Chunks
                    |
                    v
               LLM Answer
                    |
                    v
           Validate LLM Output
                    |
                    v
          Build Trusted Citations
                    |
                    v
              Final Response
```

### Question-answering pipeline

1. The user authenticates and receives a JWT.
2. The API verifies the token signature, expiry, and required claims.
3. The authenticated user is cross-checked against SQLite.
4. The company ID is obtained from the verified token.
5. The active policy version is retrieved from SQLite.
6. ChromaDB searches only within that company and active version.
7. Retrieved chunks are checked again for tenant and version consistency.
8. A relevance threshold is applied, and at most three chunks are selected.
9. The LLM generates a short answer using the retrieved evidence.
10. The server validates the LLM response and constructs citations from stored metadata.

The LLM never determines the user's identity, company, active policy version, or citation metadata.

---

## 9. Project Structure

```text
commute-policy-assistant/
│
├── app/
│   ├── api/
│   │   ├── auth.py
│   │   ├── policies.py
│   │   └── questions.py
│   │
│   ├── core/
│   │   ├── config.py
│   │   ├── container.py
│   │   └── security.py
│   │
│   ├── db/
│   │   ├── chroma.py
│   │   ├── embeddings.py
│   │   └── metadata.py
│   │
│   ├── services/
│   │   ├── answer.py
│   │   ├── ingestion.py
│   │   ├── llm.py
│   │   ├── retrieval.py
│   │   └── seed.py
│   │
│   └── main.py
│
├── data/
│   └── policies/
│
├── evaluation/
│   ├── comparison.json
│   ├── metrics.json
│   ├── questions.jsonl
│   └── results.jsonl
│
├── scripts/
│   ├── chat.py
│   ├── evaluate.py
│   └── seed_data.py
│
├── tests/
│
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
├── EVALUATION_REPORT.md
└── readme.md
```

---

## 10. Design Decisions and Security

### Tenant isolation

Tenant isolation is enforced at multiple layers:

1. The company is obtained from a verified JWT and checked against the user record.
2. The question request rejects unexpected fields such as `company_id`.
3. ChromaDB retrieval always filters by both `company_id` and `policy_version`.
4. Retrieved records are rechecked before being passed to the LLM.
5. The LLM never receives another company's policy content.

### Prompt injection protection

Policy documents and user questions are treated as untrusted input.

- Retrieved content is escaped and clearly delimited.
- The system prompt instructs the model not to follow instructions contained in policy documents.
- The model cannot access other tenants' policy content.
- Invented citation IDs are rejected.

Acme's policy contains a deliberate prompt-injection fixture to test this behaviour.

### Citation validation

The LLM returns chunk IDs rather than trusted citation text.

The server verifies that each ID belongs to the retrieved evidence and builds the final citations using its own stored metadata.

### Atomic policy version activation

Policy ingestion follows this sequence:

1. Register the new version as `staging`.
2. Parse and chunk the policy.
3. Index its chunks in ChromaDB.
4. Verify the indexed content.
5. Activate the new version and deactivate the previous version in a SQLite transaction.

If ingestion fails, the new version is marked failed, partial chunks are deleted, and the previous active version remains available.

SQLite and ChromaDB do not share a transaction. A crash between indexing and activation can leave orphan vectors, but these are never served and are reconciled at startup.

### LLM call and latency constraints

- Normal answering requires one LLM call.
- Questions with no relevant evidence require zero LLM calls.
- The application does not retry failed LLM responses.
- At most three evidence chunks are passed to the LLM.
- Provider failures and invalid responses produce a controlled retry response.

The evaluation report contains measured latency and retrieval results.

---

## 11. Technology Stack

| Component | Technology |
|---|---|
| Backend | Python, FastAPI |
| Vector database | ChromaDB (embedded) |
| Embeddings | Sentence Transformers, MiniLM |
| Offline embedding stand-in | Hash embedder |
| LLM providers | Groq, Gemini |
| Offline LLM stand-in | Extractive provider |
| Metadata database | SQLite |
| Authentication | JWT |
| Containerization | Docker, Docker Compose |
| Testing | Pytest |

ChromaDB runs in embedded mode inside the API container. A separate ChromaDB server is not required.

---

## 12. Trade-offs

1. **Section-aware chunking:** Preserves policy sections and improves citation quality, but produces variable-sized chunks.
2. **Relevance threshold plus LLM abstention:** A lenient threshold preserves potentially relevant evidence, while the LLM decides whether it supports an answer. Abstention quality therefore depends partly on the model.
3. **No LLM retries:** Avoids doubling latency and consuming additional API quota, at the cost of returning a retry response for some recoverable model failures.
4. **Embedded ChromaDB:** Simplifies deployment and enables one-command startup, but the API process owns the index and is not designed for horizontal scaling as-is.
5. **Demo authentication:** Uses an application-managed JWT issuer for the case study. A production deployment should integrate with the company's identity provider.
6. **Thread-based timeouts:** Prevents a request from waiting indefinitely, but stuck workers cannot be forcibly terminated and may consume worker capacity.

---

## 13. Known Limitations

- Policy ingestion currently supports Markdown documents with `##` section headings; PDF parsing is not implemented.
- English-language policies and questions are assumed.
- Conversation memory is not maintained between questions.
- Offline extractive LLM and hash embedding modes are demonstration/test stand-ins, not substitutes for real model quality.
- Concurrent ingestion of the same company and policy version is not explicitly locked.

---

## 14. Useful Docker Commands

| Command | Purpose |
|---|---|
| `docker compose up --build` | Build and start the application |
| `docker compose up -d` | Start in the background |
| `docker compose logs -f api` | View application logs |
| `docker compose exec api pytest -q` | Run automated tests |
| `docker compose exec api python -m scripts.evaluate` | Run evaluation |
| `docker compose exec api python -m scripts.chat` | Launch terminal chatbot |
| `docker compose down` | Stop the application |

**Note:** Avoid `docker compose down -v` unless you intentionally want to delete the persistent SQLite and ChromaDB storage.

---

## 15. Submission Demo Checklist

For a quick demonstration:

1. Start the application with `docker compose up --build`.
2. Open Swagger UI at `/docs`.
3. Authenticate as `acme_employee`.
4. Ask a question covered by the Acme policy and show its citation.
5. Ask an unsupported question and show the `not_covered` response.
6. Authenticate as `northstar_employee` and demonstrate that its policy produces a different answer.
7. Try a cross-company question and verify that no other company's policy is exposed.
8. Authenticate as `acme_admin` and ingest v2.
9. Ask about the changed escort threshold and verify that v2 is active.
10. Run the automated tests and evaluation suite.

---

**Repository:** https://github.com/NamanAgarwal0905/commute-policy-assistant

**API Documentation:** http://localhost:8000/docs