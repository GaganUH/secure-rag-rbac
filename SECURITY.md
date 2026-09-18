# Security Policy

## Supported version

| Version | Security support |
| --- | --- |
| `1.0.x` | Supported |
| Earlier versions | Not supported |

This is a reference implementation for permission-aware retrieval. It demonstrates security controls and evaluates them against a small fictional corpus; it is not a claim of production certification.

## Reporting a vulnerability

Please use GitHub's private vulnerability-reporting or Security Advisory feature for this repository. Do not publish credentials, bearer tokens, private documents, prompts, database contents, or reproducible exploit details in a public issue.

Include the affected endpoint or module, the role used during the test, the expected behavior, the observed behavior, and minimal reproduction steps. Use only fictional test data.

## Security model

The application is designed so that authorization happens before retrieved text is placed in an LLM prompt:

1. The bearer session is validated and the user's current active status, role, and permission version are read from SQLite.
2. SQLite determines the document IDs that the current role may access.
3. Qdrant semantic search receives both role and document-ID filters.
4. Matched chunk IDs are resolved to canonical text in SQLite and permissions are checked again.
5. If no authorized source remains, the request is refused without calling Gemini.
6. Access is checked again before an answer and its citations are returned.

Retrieval-cache keys include the user identity, permission version, role, and permitted-document fingerprint. Managed permission changes also clear the process-local cache.

## Secrets and private data

- Set `GEMINI_API_KEY` through the environment; never commit it to Git.
- Do not commit the local `data/` directory, uploaded documents, evaluation reports, passwords, bearer tokens, or captured prompts.
- Rotate a key or credential immediately if it is accidentally exposed.
- Use fictional or approved documents unless your organization has reviewed the external provider's current data-handling terms.

The repository's `.gitignore` excludes the usual local database, uploads, vector index, reports, environment files, and virtual environment. This reduces accidental commits but does not replace reviewing staged files before every push.

## Known boundaries

- A permission change after a prompt has already been sent can suppress the returned answer, but it cannot retract text already dispatched to an external provider.
- The cache and local Qdrant configuration are single-process. Multi-worker deployments need shared invalidation and additional concurrency review.
- The supplied tests and evaluations cover known scenarios and cannot prove the absence of every information leak.
- Model answers can still be incorrect even when their source retrieval is authorized.
- Production deployment additionally requires HTTPS, secure secret storage, database hardening, rate limiting, monitoring, backup controls, dependency maintenance, and an organization-specific threat assessment.
