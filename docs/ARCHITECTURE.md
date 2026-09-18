# Architecture

Standalone Gmail draft assistant (this repo only).

```text
Streamlit UI  ──►  InboxBot thread  ──►  GmailAdapter (OAuth, drafts, labels)
                         │
                         ├── SQLite queue + processed ids
                         ├── KnowledgeBase (TF-IDF over FAQs, files, URLs)
                         ├── Sent-mail style examples
                         └── Draft engine (placeholder | Ollama)
```

## Rules

| Action | Allowed |
| --- | --- |
| `users.drafts.create` with `threadId` + In-Reply-To | yes |
| Apply `GmailBot-Drafted` | yes |
| `users.messages.send` | **never** |
| Remove `UNREAD` | **never** |

## Resilience

- SQLite-backed queue (`draft_queue`, `inbox_processed`)
- Per-call Gmail rate limiter
- Exponential backoff on poll failures
- Per-message failures are recorded and retried (capped)

## RAG

Portable TF-IDF index persisted as `knowledge_chunks` in SQLite. No cloud embedding API and no model download, so the folder stays copyable. Swap `TfidfIndex` later if you want Chroma/FAISS.

## LLM

`gmail_bot/llm.py` is the only place that talks to a model. Default is placeholder drafts that still include retrieved FAQ text.

## UI

`app.py` (Streamlit): Start / Pause / Stop, queue preview, knowledge editor, templates. The worker does **not** auto-start with the UI.
