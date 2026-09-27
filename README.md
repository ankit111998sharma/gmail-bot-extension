# Gmail Draft Assistant

A portable local app plus a Chrome extension that reads Gmail, drafts replies in the same thread, and **never sends** mail.

Copyright © Ankit Sharma.

## About this project

Gmail Draft Assistant watches your inbox (or drafts only when you click in the extension), builds a local knowledge index from FAQs, notes, and optional website URLs, and writes a Gmail **draft**. You review and send in Gmail yourself. The original message stays unread and is labeled `GmailBot-Drafted` so it is not drafted twice by accident.

Copy the whole folder to another computer; `run.bat` / `run.sh` can create the virtual environment there.

## Purpose

- Speed up email replies with a first draft that matches your notes and optional site copy.
- Keep a human in control: drafts only, never `messages.send`.
- Work from Gmail in the browser via a small Chrome extension, or from a Streamlit home screen.

## How it works

1. You enable the Gmail API in Google Cloud, create a Desktop OAuth client, and save it as `data/gmail/credentials.json`.
2. `python -m gmail_bot auth` (or **Connect this Gmail** on the Home screen) writes `data/gmail/token.json`.
3. Optional: Start / Pause / Stop polling of unread inbox mail from Streamlit. A health checker can repair local files, retry recoverable draft errors, and index listed websites.
4. Knowledge comes from `data/knowledge/faqs.json`, markdown notes, and URLs you ingest. Optional few-shot tone comes from recent **Sent** mail.
5. Drafts are created with the same `threadId` and `In-Reply-To` / `References` headers. The Chrome popup can add a website URL and draft notes; a reply is created **only when you click Draft reply**.
6. Default `INBOX_LLM=placeholder` injects FAQ hits into a labeled draft. You can point `INBOX_LLM=ollama` at a local model instead.

Scope used: `https://www.googleapis.com/auth/gmail.modify` (needed for a custom label). The bot still never sends.

## Advantages

- Safe default: drafts only; unread stays unread.
- Portable: credentials stay in this folder (gitignored) and travel with a copy of the project.
- Extension drafts the open email only, so you are not bulk-replying the whole inbox by accident.
- Logs store sender, subject, and snippet, not full bodies.
- Tests use a fake Gmail client and never touch a live inbox.

## Technologies

| Area | Choice |
| --- | --- |
| Language | Python 3.11+ |
| UI | Streamlit |
| Gmail | Google API Python client, OAuth (Desktop) |
| Extension | Chrome Manifest V3 (JavaScript) |
| Knowledge / fetch | BeautifulSoup, requests, httpx |
| Config | python-dotenv |
| Optional LLM | Ollama (`llama3` or another local model) |
| Tests | pytest |

## How to run this project

### One-click (Windows)

```bat
run.bat
```

### macOS / Linux

```bash
chmod +x run.sh
./run.sh
```

Then open the URL Streamlit prints (usually [http://127.0.0.1:8501](http://127.0.0.1:8501)).

### Manual

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m gmail_bot auth
```

Place OAuth JSON at `data/gmail/credentials.json` before auth.

### Chrome extension

Keep the app running, then:

1. Open `chrome://extensions`
2. Turn on **Developer mode**
3. **Load unpacked** and select the `extension` folder in this project
4. Open Gmail, open an email, click **Gmail Draft Bot**, optionally add a URL or notes, then **Draft reply**

### CLI

```bash
python -m gmail_bot auth
python -m gmail_bot ingest --url https://example.com
python -m gmail_bot inbox --once
python -m gmail_bot inbox
python -m gmail_bot status
```

### Optional local LLM

Install [Ollama](https://ollama.com), pull a model, then in `.env`:

```
INBOX_LLM=ollama
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=llama3
```

Copyright © Ankit Sharma.
