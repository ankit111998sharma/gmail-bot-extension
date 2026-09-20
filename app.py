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
    if not status.oauth_ready:
        return "Gmail is not connected. Open Setup, then run python -m gmail_bot auth."
    if status.running and status.paused:
        return "Paused. Nothing is being checked right now."
    if status.running:
        return "Running. Drafts only — mail stays unread and is never sent."
    return "Stopped. Use Start when you want it to check unread mail."


def render_home(bot) -> None:
    status = bot.status()
    st.markdown(f'<div class="status-line">{_status_text(status)}</div>', unsafe_allow_html=True)
    if not status.oauth_ready:
        st.warning("Save `data/gmail/credentials.json`, then run `python -m gmail_bot auth`.")
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
    st.caption(f"Last check: {status.last_poll or '—'} · {status.last_activity or 'No activity yet'}")

    st.markdown("##### Recent activity")
    logs = bot.store.recent_job_logs(8)
    if logs:
        st.dataframe(logs, width="stretch", hide_index=True)
    else:
        st.markdown('<p class="muted">No activity yet.</p>', unsafe_allow_html=True)


def render_drafts(bot) -> None:
    rows = bot.store.list_queue()
    st.caption("Review drafts here, then send them yourself from Gmail.")
    if not rows:
        st.markdown('<p class="muted">No drafts yet. Start the bot after Gmail is connected.</p>', unsafe_allow_html=True)
    else:
        for row in rows:
            title = f"{row['status']} · {row.get('subject') or '(No Subject)'}"
            with st.expander(title):
                st.caption(row.get("sender") or "")
                if row.get("last_error"):
                    st.error(row["last_error"])
                preview = st.text_area(
                    "Draft",
                    value=row.get("draft_preview") or "",
                    key=f"preview-{row['message_id']}",
                    height=180,
                )
                name = st.text_input("Save as template", key=f"tpl-{row['message_id']}")
                if st.button("Save template", key=f"save-{row['message_id']}") and name.strip():
                    bot.store.upsert_template(name.strip(), preview)
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
    st.caption("Connect Gmail once. After that, this folder can move to another PC.")
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
4. Run `python -m gmail_bot auth` and sign in
5. This app only writes drafts. It never sends mail.
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
