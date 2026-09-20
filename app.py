from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from gmail_bot.bot import get_controller
from gmail_bot.config import load_settings
from gmail_bot.rag import KnowledgeBase

st.set_page_config(page_title="Gmail Draft Assistant", page_icon="✉️", layout="centered")

_CSS = """
<style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2.5rem; max-width: 820px; }
    #MainMenu, footer, .stDeployButton { visibility: hidden; }
    h1 { font-size: 1.55rem !important; font-weight: 600 !important; letter-spacing: -0.02em; color: #111827 !important; }
    .hero-note { color: #6b7280; font-size: 0.95rem; margin: -0.4rem 0 1.2rem; }
    .status-line {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 0.85rem 1rem;
        color: #374151;
        margin-bottom: 0.9rem;
    }
    div[data-testid="stMetric"] {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
        padding: 0.55rem 0.75rem;
    }
    div[data-testid="stMetric"] label { color: #6b7280; }
    .stButton > button {
        border-radius: 10px;
        min-height: 2.55rem;
        font-weight: 600;
    }
    [data-testid="stExpander"] {
        background: #ffffff;
        border: 1px solid #e5e7eb;
        border-radius: 12px;
    }
    .muted { color: #6b7280; font-size: 0.9rem; }
</style>
"""


def _controller():
    if "bot" not in st.session_state:
        st.session_state.bot = get_controller(load_settings())
    return st.session_state.bot


def _status_text(status) -> str:
    if status.target_email and status.connected_email and status.target_email != status.connected_email:
        return f"This bot is set to {status.target_email}, but Gmail is signed in as {status.connected_email}."
    if status.target_email and not status.connected_email:
        return f"Use {status.target_email}. Click Connect this Gmail and sign in as that account."
    if not status.oauth_ready:
        return "Enter the Gmail address below, then connect it."
    if status.running and status.paused:
        return f"Paused for {status.connected_email or status.target_email or 'Gmail'}."
    if status.running:
        account = status.connected_email or status.target_email or "the signed-in Gmail"
        return f"Running for {account}. Drafts only — mail stays unread and is never sent."
    if status.connected_email:
        return f"Ready for {status.connected_email}. Use Start to check unread mail."
    return "Stopped. Enter a Gmail address, connect it, then start."


def render_account(bot) -> None:
    status = bot.status()
    if "gmail_account_input" not in st.session_state:
        st.session_state.gmail_account_input = status.target_email
    st.markdown("##### Gmail to use")
    st.text_input(
        "Email address",
        key="gmail_account_input",
        placeholder="you@gmail.com",
        label_visibility="collapsed",
    )
    save_col, connect_col = st.columns(2)
    if save_col.button("Use this email", width="stretch"):
        try:
            email = bot.set_target_email(st.session_state.gmail_account_input)
            st.success(f"Saved {email}. The bot will only use this inbox.")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    if connect_col.button("Connect this Gmail", width="stretch"):
        try:
            with st.spinner("A browser window will open. Sign in with that same Gmail."):
                email = bot.connect_account(st.session_state.gmail_account_input)
            st.success(f"Connected {email}.")
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    if status.connected_email:
        st.caption(f"Signed in as {status.connected_email}")
    elif status.target_email:
        st.caption(f"Saved {status.target_email} — not signed in yet")
    else:
        st.caption("The bot will only draft mail for the Gmail you enter here.")
    if "rules_url_input" not in st.session_state:
        st.session_state.rules_url_input = bot.store.get_setting("rules_url")
    st.markdown("##### Rules URL")
    st.text_input(
        "Rules or regulations URL",
        key="rules_url_input",
        placeholder="https://example.com/fee-rules",
        label_visibility="collapsed",
    )
    if st.button("Use this URL", width="stretch"):
        try:
            url = bot.set_rules_url(st.session_state.rules_url_input)
            if url:
                st.success("Saved. The next reply will use rules from this page.")
            else:
                st.success("Cleared the rules URL.")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    st.caption("Optional. Paste a rules or regulations page; suggestions appear on Drafts and in Gmail.")


def render_home(bot) -> None:
    status = bot.status()
    st.markdown(f'<div class="status-line">{_status_text(status)}</div>', unsafe_allow_html=True)
    render_account(bot)
    if not status.oauth_ready:
        st.warning("Save `data/gmail/credentials.json` first, then click Connect this Gmail.")
    if status.last_error:
        st.error(status.last_error)

    left, mid, right = st.columns(3)
    if left.button("Start", type="primary", width="stretch"):
        try:
            bot.start()
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    if mid.button("Pause", width="stretch"):
        bot.pause()
        st.rerun()
    if right.button("Stop", width="stretch"):
        bot.stop()
        st.rerun()

    m1, m2, m3 = st.columns(3)
    m1.metric("Drafts made", status.processed_count)
    m2.metric("Waiting", status.queue_pending)
    m3.metric("Failed", status.queue_failed)
    st.caption("On Gmail, click the Chrome extension icon for optional website URL and notes, then draft. Keep this window running.")

    st.markdown("##### Recent activity")
    logs = bot.store.recent_job_logs(8)
    if logs:
        st.dataframe(logs, width="stretch", hide_index=True)
    else:
        st.markdown('<p class="muted">No activity yet.</p>', unsafe_allow_html=True)


def render_drafts(bot) -> None:
    rows = bot.store.list_queue()
    st.caption("Review drafts here, then send them yourself from Gmail. Redraft fixes grammar and uses the rules URL.")
    if not rows:
        st.markdown('<p class="muted">No drafts yet. Start the bot after Gmail is connected.</p>', unsafe_allow_html=True)
    else:
        for row in rows:
            title = f"{row['status']} · {row.get('subject') or '(No Subject)'}"
            with st.expander(title):
                st.caption(row.get("sender") or "")
                if row.get("last_error"):
                    st.error(row["last_error"])
                preview_key = f"preview-{row['message_id']}"
                suggest_key = f"suggest-{row['message_id']}"
                if preview_key not in st.session_state:
                    st.session_state[preview_key] = row.get("draft_preview") or ""
                preview = st.text_area("Draft", key=preview_key, height=180)
                if st.button("Redraft & fix grammar", key=f"redraft-{row['message_id']}"):
                    try:
                        result = bot.redraft_existing(
                            row["message_id"],
                            preview,
                            st.session_state.get("rules_url_input") or bot.store.get_setting("rules_url"),
                        )
                        st.session_state[preview_key] = result.get("draftText") or result.get("text") or preview
                        st.session_state[suggest_key] = result.get("suggestions") or []
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(str(exc))
                suggestions = st.session_state.get(suggest_key) or []
                if suggestions:
                    st.markdown("**Suggestions from the rules page**")
                    for item in suggestions:
                        st.write(f"- {item}")
                name = st.text_input("Save as template", key=f"tpl-{row['message_id']}")
                if st.button("Save template", key=f"save-{row['message_id']}") and name.strip():
                    bot.store.upsert_template(name.strip(), st.session_state.get(preview_key) or preview)
                    st.success(f"Saved “{name.strip()}”.")

    st.markdown("##### Templates")
    templates = bot.store.list_templates()
    name = st.text_input("Name", key="new-template-name")
    body = st.text_area("Text", height=140, key="new-template-body")
    if st.button("Save") and name.strip() and body.strip():
        bot.store.upsert_template(name.strip(), body)
        st.rerun()
    for row in templates:
        with st.expander(row["name"]):
            st.write(row["body"])
            if st.button("Delete", key=f"del-{row['id']}"):
                bot.store.delete_template(row["name"])
                st.rerun()


def render_knowledge(bot) -> None:
    settings = bot.settings
    st.caption("Answers come from these notes. Keep this short and clear.")
    faqs_path: Path = settings.faqs_path
    current = faqs_path.read_text(encoding="utf-8") if faqs_path.is_file() else '{\n  "faqs": []\n}\n'
    extra = st.text_area("Notes and FAQs", value="", height=160, placeholder="Hours, pricing, common answers…")
    with st.expander("Advanced: JSON FAQs and website URLs"):
        faqs_text = st.text_area("FAQ JSON", value=current, height=180)
        urls = st.text_area("Website URLs", value="", placeholder="One URL per line")
    if st.button("Update knowledge", type="primary"):
        faqs_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            parsed = json.loads(faqs_text)
            faqs_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
        except json.JSONDecodeError as exc:
            st.error(f"FAQ JSON is invalid: {exc}")
            return
        kb = KnowledgeBase(bot.store)
        try:
            count = kb.rebuild_from_sources(
                faqs_path=faqs_path,
                knowledge_dir=settings.knowledge_dir,
                urls=[line.strip() for line in urls.splitlines() if line.strip()],
                extra_notes=extra,
            )
            bot.knowledge = kb
            st.success(f"Saved {count} pieces of knowledge.")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    chunks = bot.store.list_knowledge()
    st.caption(f"{len(chunks)} pieces indexed")
    if chunks:
        st.dataframe(
            [{"source": c["source"], "text": c["text"][:160]} for c in chunks[:40]],
            width="stretch",
            hide_index=True,
        )


def render_setup(bot) -> None:
    settings = bot.settings
    creds_ok = settings.credentials_path.is_file()
    token_ok = settings.token_path.is_file()
    st.caption("Connect the Gmail you typed on Home. After that, this folder can move to another PC.")
    c1, c2 = st.columns(2)
    c1.write("Credentials file")
    c1.write("Ready" if creds_ok else "Missing")
    c2.write("Login token")
    c2.write("Ready" if token_ok else "Missing")
    st.markdown(
        """
1. Google Cloud → enable **Gmail API**
2. Create a Desktop OAuth client
3. Save it as `data/gmail/credentials.json`
4. On **Home**, type the Gmail address and click **Connect this Gmail**
5. Sign in as that same address. The bot only uses that inbox.
6. This app only writes drafts. It never sends mail.

**Chrome extension**

1. Keep this app running (`run.bat`).
2. Chrome → `chrome://extensions` → Developer mode → Load unpacked → select the `extension` folder in this project.
3. Pin **Gmail Draft Bot**, open Gmail, then click the extension icon. Optional website URL and description appear there. Leave both blank to skip them. Use **Draft reply** or **Redraft & grammar**.
        """
    )


bot = _controller()
st.markdown(_CSS, unsafe_allow_html=True)
st.title("Gmail Draft Assistant")
st.markdown('<p class="hero-note">A simple local helper that drafts replies. You send them.</p>', unsafe_allow_html=True)

home, drafts, knowledge, setup = st.tabs(["Home", "Drafts", "Knowledge", "Setup"])
with home:
    render_home(bot)
with drafts:
    render_drafts(bot)
with knowledge:
    render_knowledge(bot)
with setup:
    render_setup(bot)
