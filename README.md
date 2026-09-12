# Secure RAG with Role-Based Access Control

A document-question-answering API that checks access permissions **before retrieval** and before any source text is sent to a language model.

Python · FastAPI · SQLite · Qdrant · FastEmbed · Gemini

Authenticate → filter permitted documents → retrieve → recheck permissions → generate a cited answer

## Overview

Standard retrieval-augmented generation (RAG) retrieves relevant documents and places their text in a model prompt. In an organization, relevance alone is not enough: a document may be relevant but unavailable to the person asking. Filtering only the final answer is too late if confidential text has already entered the prompt.

This system stores document permissions in SQLite and applies role and document-ID constraints inside vector search. It then reads the selected chunks from SQLite and checks permissions again before preparing the prompt. The API supports three roles—`Employee`, `HR`, and `Admin`—and uses fictional documents to demonstrate that an Employee can retrieve handbook information but not salary records.

## Features

- Password-protected users with Argon2 hashing and 30-minute database-backed bearer sessions.
- Admin controls for creating users, changing roles, and activating or deactivating accounts.
- Admin-only PDF and UTF-8 TXT upload with text extraction, chunking, and role metadata.
- Keyword search and semantic search, with permissions applied during retrieval.
- Local Qdrant vector search and FastEmbed embeddings using `BAAI/bge-small-en`.
- A permission-aware semantic-result cache that is cleared after managed access changes.
- A context-preview endpoint to inspect permitted excerpts and citations without calling Gemini.
- A Gemini-backed answer endpoint that skips the provider when no accessible source is found.
- Additional access checks before returning search results, before provider dispatch, and before returning an answer.
- Automated security tests, offline evaluation, live answer checks, and a synthetic RBAC-filter benchmark.

## How it works

```text
User question + bearer token
             ↓
Read the user's current role from SQLite
             ↓
Find currently permitted document IDs
             ↓
Search Qdrant with role and document-ID filters
             ↓
Read matched chunks from SQLite and recheck permissions
             ↓
If no permitted source: return a refusal without calling Gemini
If permitted sources exist: send only those excerpts to Gemini
             ↓
Recheck access before returning the answer and citations
```

The vector index helps find relevant chunks, but SQLite remains the authority for users, roles, document permissions, and source text. A stale or altered vector payload is not trusted as prompt content.

## Technology stack

| Technology | Purpose |
| --- | --- |
| Python and FastAPI | API routes, request validation, and interactive API documentation |
| SQLite and SQLAlchemy | Users, sessions, documents, chunks, and permissions |
| Qdrant (local mode) | Permission-filtered vector search |
| FastEmbed | Local text embeddings for semantic retrieval |
| Gemini Interactions API | Answer generation from permitted excerpts |
| Pytest | Automated behavior and security checks |

## Project structure

```text
RAG-with-RBAC/
├── app/                 API, authentication, ingestion, retrieval, RAG, evaluations
├── tests/               Automated tests with isolated test data
├── sample_documents/    Fictional handbook and salary files for the demo
├── .gitignore
├── README.md
└── requirements.txt
```

The application creates `data/` for its local database, uploads, and vector index. Evaluations create `reports/`; setup creates `.venv/`. These are local-only and ignored by Git.

## Prerequisites

- Python and `pip`.
- A Gemini API key only if you want generated answers. Retrieval and context preview do not need one.
- Git if you want to clone or contribute to the repository.

## Installation

Open a Windows PowerShell terminal in the project directory. If you downloaded or cloned a fresh copy, create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Create the first Admin account once. The command asks for a password without displaying it:

```powershell
.\.venv\Scripts\python.exe -m app.bootstrap_admin --username admin
```

Start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open [the interactive API documentation](http://127.0.0.1:8000/docs). The root endpoint is available at [http://127.0.0.1:8000](http://127.0.0.1:8000).

## Usage

1. Call `POST /auth/login` in `/docs` with your Admin credentials. Copy the returned access token into the **Authorize** dialog.
2. Call `POST /admin/users` to create a user named `alice` with the `Employee` role and a password of your choice.
3. Upload `sample_documents/employee_handbook.txt` as **Employee Handbook**, with `allowed_roles` set to `Employee,HR,Admin`.
4. Upload `sample_documents/salary_records.txt` as **Salary Records**, with `allowed_roles` set to `HR,Admin`. Uploading indexes each document automatically.
5. Log in as Alice and ask, “How many remote work days are permitted?” through `POST /rag/context-preview`. The handbook should be available. Then ask, “What is Alice's compensation?” The Employee response should have no accessible source or citation.
6. Log in as Admin and ask the salary question again. The salary document is now permitted. Use `POST /rag/ask` for a generated answer once Gemini is configured.

All names and salary amounts in the sample files are fictional. On a fresh installation, Admin and Alice passwords are chosen locally and are not included in this repository.

### Enable Gemini answers

Create a key in Google AI Studio and store it in a Windows user environment variable named `GEMINI_API_KEY`. Close and reopen the terminal, then restart the API. Never place the key in source code, screenshots, Git commits, or chat messages. The optional `GEMINI_MODEL` variable changes the model; the code defaults to `gemini-3.8-flash`.

If a query has no accessible source, `/rag/ask` returns a refusal without a Gemini call. A missing key returns HTTP 503 only when a permitted source exists. The provider request uses `store=false` for interaction state, but that setting does not replace the provider's data-use terms; check current terms before considering sensitive material.

## API endpoints

| Endpoint | Purpose |
| --- | --- |
| `POST /auth/login`, `GET /auth/me` | Log in and read the current account |
| `POST /admin/users`, `PATCH /admin/users/{id}/role` | Create users and change roles |
| `POST /admin/documents`, `PATCH /admin/documents/{id}/permissions` | Upload documents and manage allowed roles |
| `POST /retrieval/search`, `POST /retrieval/semantic-search` | Search accessible chunks |
| `POST /rag/context-preview` | Inspect permitted prompt context and citations without Gemini |
| `POST /rag/ask` | Generate a cited answer from permitted context |
| `GET /health` | Check the API and database |

## Output

An Employee salary request without an accessible source returns a response shaped like:

```json
{
  "query": "What is Alice's compensation?",
  "user_role": "Employee",
  "status": "no_accessible_source",
  "answer": "I couldn't find an answer in documents you can access.",
  "citations": [],
  "timings_ms": {"context_preparation": 21.0, "gemini_generation": 0.0}
}
```

The timing number above is illustrative, not a guaranteed result. Authorized answers include source citations. The preview endpoint returns `context` instead of an `answer` and never calls Gemini.

## Testing and evaluation

Run the automated tests without using the Gemini API:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

After uploading the two fictional files with the roles listed above, run the offline security evaluation:

```powershell
.\.venv\Scripts\python.exe -m app.evaluate_security
```

It checks five fixed role/query cases three times each, examines retrieved context and citations for known fictional salary markers, and measures context-preparation time. It refuses to run if the sample dataset or its permissions differ from the expected setup.

To check three complete API answer cases, keep the server running and run this in a second terminal. It asks for Admin and Alice passwords privately and may make **up to two real Gemini calls**:

```powershell
.\.venv\Scripts\python.exe -m app.evaluate_live --live
```

To isolate vector-filter cost, run a separate in-memory benchmark with 1,000 invented points. The unfiltered arm is a measurement baseline only; it is never used by the RAG API:

```powershell
.\.venv\Scripts\python.exe -m app.benchmark_rbac
```

| Local check on 12 September 2026 | Observed result |
| --- | --- |
| Automated suite | 67 passed; 2 dependency deprecation warnings |
| Offline evaluation, 5 cases × 3 repeats | 0 detected unauthorized exposures; 100% expected-source match |
| Live answer evaluation, 3 cases | 3 passed; blocked Employee salary case used 0 ms of Gemini generation |
| Synthetic vector benchmark, 60 searches per arm | Median 0.338 ms unfiltered vs 18.43 ms RBAC-filtered; 0 unauthorized filtered results |

These are small local results, not production guarantees. The benchmark times **Qdrant search only** with the same vectors, top-k, and payload setting in both arms. It excludes embedding, SQLite checks, cache, HTTP, and Gemini. All reports are saved under the Git-ignored `reports/` directory; the live report does not save passwords, tokens, raw prompts, or document text.

## How the code is organized

| Module | Responsibility |
| --- | --- |
| `app/main.py` | Create the FastAPI application and register routes |
| `app/models.py`, `app/database.py` | Define tables and database sessions |
| `app/auth_service.py`, `app/user_service.py` | Authenticate accounts and manage users |
| `app/document_service.py` | Validate, extract, chunk, and store documents |
| `app/vector_service.py`, `app/retrieval_service.py` | Run semantic and keyword retrieval |
| `app/rag_service.py`, `app/gemini_provider.py` | Prepare authorized context and generate answers |
| `app/evaluate_security.py`, `app/evaluate_live.py`, `app/benchmark_rbac.py` | Measure behavior and performance |

## Error handling

The API rejects missing authentication, non-Admin management requests, invalid uploads, and unsupported or unreadable files. A query with no accessible source returns a refusal instead of calling Gemini. Provider errors return a safe HTTP error category or status without echoing a raw provider response, prompt, or API key. Scanned PDFs without extractable text require OCR and are not accepted as searchable documents.

## Design decisions

**Why filter inside retrieval?** A relevant but unauthorized chunk must not enter a prompt or citation list. Qdrant receives the role and currently permitted document IDs as query filters.

**Why recheck SQLite after vector search?** Vector metadata may be stale. The system reads canonical source text and permissions from SQLite before constructing context and checks access again at response boundaries.

**Why keep the cache permission-aware?** Cache keys include the user, permission version, role, and permitted-document fingerprint. Managed access changes also clear the process-local cache.

**Why skip Gemini for empty context?** Without a permitted source, the system has no authorized evidence for a document-grounded answer.

## Limitations

- The cache and local Qdrant setup are single-process; multi-worker deployment needs shared invalidation and concurrency review.
- If permissions change **after** a prompt has already been sent, the final check can suppress the returned answer but cannot retract data from the external provider. The checks do not provide an atomic revocation guarantee.
- Citations identify retrieved excerpts; they do not prove every generated sentence is correct. A language model can still make mistakes.
- The security evaluations cover a small fictional corpus and known strings. They cannot prove that all possible queries, logs, provider behavior, or concurrent schedules are safe.
- The synthetic benchmark does not measure total RBAC overhead or production-scale performance.
- The repository provides an API and interactive `/docs` page, not a separate end-user interface.

## Responsible use

Use fictional or otherwise approved documents while testing. Do not upload real confidential data to an external model account without reviewing its current data-handling terms and your organization's policy. Keep passwords, API keys, local databases, uploads, and generated reports out of Git.

## Future improvements

- Add multi-worker permission invalidation and a stronger revocation protocol around provider dispatch.
- Evaluate larger and more varied document collections, adversarial questions, and retrieval-quality trade-offs.
- Add OCR for scanned PDFs and a dedicated user interface if needed.

## Author

Gagan U. H. · GitHub: [@GaganUH](https://github.com/GaganUH)
