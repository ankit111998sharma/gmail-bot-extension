# Gmail Draft Assistant

Standalone local app that reads Gmail, drafts replies in your thread, and **never sends** mail. Copy this folder to another computer and it keeps working (venv is created by `run.bat` / `run.sh`).

A Chrome extension popup lets you add an optional website URL and draft notes, then create a reply. Drafts are created **only when you click Draft reply**.

## What it does

- Optional Start / Pause / Stop polling of unread inbox mail
- Chrome extension popup: optional website URL and description, then draft a reply for the open email only
- Builds a local knowledge index from `data/knowledge/faqs.json`, markdown notes, and optional website URLs
- Optionally matches tone using recent **Sent** mail as few-shot examples
- Writes a Gmail **draft** in the same thread (`threadId` + `In-Reply-To` / `References`)
- Labels the original message `GmailBot-Drafted`
- Leaves the message **unread** so you can still see it
- Never calls `messages.send`

## One-click start

Windows:

```bat
run.bat
```

macOS / Linux:

```bash
chmod +x run.sh
./run.sh
```

Then open the URL Streamlit prints (usually http://127.0.0.1:8501).

## Chrome extension (click to draft)

Keep `run.bat` running, then load the helper in Chrome:

1. Open `chrome://extensions`
2. Turn on **Developer mode**
3. Click **Load unpacked**
4. Select `d:\Cursor ai projects\gmail bot extension\extension`
5. Pin **Gmail Draft Bot**
6. Open [Gmail](https://mail.google.com/mail/u/0/#inbox), open an email, click the extension icon
7. Optionally paste a website URL and/or a description, then click **Draft reply**. Leave both blank to skip them.

The bot does not draft until you click **Draft reply**. Review the draft in Gmail; it never sends.

## One-time Gmail setup (you must do this)

1. [Google Cloud Console](https://console.cloud.google.com/) → create a project → enable **Gmail API**.
2. OAuth consent screen (External is fine for a personal Gmail). Add yourself as a test user.
3. Scope: `https://www.googleapis.com/auth/gmail.modify` (needed for a custom label; the bot still never sends).
4. Credentials → OAuth client ID → **Desktop app** → download JSON as `data/gmail/credentials.json`.
5. From this folder:

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m gmail_bot auth
```

That writes `data/gmail/token.json`. Both JSON files stay in this folder and are gitignored. Publishing the OAuth app is not required for your own account.

You can also type the Gmail address on the Home screen and click **Connect this Gmail**. The bot only uses that inbox. To switch accounts, enter the new address and connect again.

## CLI

```bash
python -m gmail_bot auth
python -m gmail_bot ingest --url https://example.com
python -m gmail_bot inbox --once
python -m gmail_bot inbox
python -m gmail_bot status
```

## LLM

Default `INBOX_LLM=placeholder` still injects FAQ hits into a clearly labeled draft.

To use a local model, install [Ollama](https://ollama.com), pull `llama3`, then set in `.env`:

```
INBOX_LLM=ollama
OLLAMA_HOST=http://127.0.0.1:11434
OLLAMA_MODEL=llama3
```

## Portable copy

Copy the whole project folder (including `data/gmail/token.json` if you already signed in). On the new PC run `run.bat` or `run.sh`. Python 3.11+ must be installed. Optional PyInstaller bundle:

```bat
scripts\build_portable.bat
```

## Safety

- Drafts only
- Unread stays unread
- Logs store sender/subject/snippet, not full bodies
- Automated tests use a fake Gmail client and never touch a live inbox
