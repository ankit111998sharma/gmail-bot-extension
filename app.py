from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from gmail_bot.bot import get_controller
from gmail_bot.config import load_settings
from gmail_bot.rag import KnowledgeBase

st.set_page_config(page_title="Gmail Draft Assistant", page_icon="✉️", layout="wide")


def _controller():
    if "bot" not in st.session_state:
        st.session_state.bot = get_controller(load_settings())
    return st.session_state.bot


def _badge(status) -> None:
    if not status.oauth_ready:
        st.error("Gmail OAuth is not ready. Save credentials.json then run `python -m gmail_bot auth`.")
    if status.running and status.paused:
        st.warning("Bot is paused. Queue is held; nothing is polled.")
    elif status.running:
        st.success("Bot is running. Drafts only — messages stay unread and are never sent.")
    else:
        st.info("Bot is stopped. It does not auto-start with this UI.")


def render_dashboard(bot) -> None:
    status = bot.status()
    _badge(status)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Processed", status.processed_count)
    c2.metric("Queue open", status.queue_pending)
    c3.metric("Failed", status.queue_failed)
    c4.metric("Poll (s)", status.poll_seconds)
    c5.metric("LLM", status.llm)

    b1, b2, b3 = st.columns(3)
    if b1.button("Start bot", type="primary", use_container_width=True):
        try:
            bot.start()
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    if b2.button("Pause bot", use_container_width=True):
        bot.pause()
        st.rerun()
    if b3.button("Stop bot", use_container_width=True):
        bot.stop()
        st.rerun()

    st.caption(f"Query `{status.gmail_query}` · Label `{status.label_name}`")
    st.write("Last poll:", status.last_poll or "—")
    st.write("Last activity:", status.last_activity or "—")
    if status.last_error:
        st.error(status.last_error)

    st.subheader("Recent jobs")
    logs = bot.store.recent_job_logs(15)
    if logs:
        st.dataframe(logs, use_container_width=True, hide_index=True)
    else:
        st.write("No jobs yet.")


def render_queue(bot) -> None:
    rows = bot.store.list_queue()
    st.subheader("Draft queue")
    st.caption("Preview generated drafts here. Send them yourself from Gmail after review.")
    if not rows:
        st.write("Queue is empty.")
        return
    for row in rows:
        with st.expander(f"{row['status'].upper()} · {row.get('subject') or '(No Subject)'} · {row.get('sender')}"):
            st.write("Message ID:", row["message_id"])
            st.write("Draft ID:", row.get("draft_id") or "—")
            st.write("Snippet:", row.get("snippet") or "—")
            if row.get("last_error"):
                st.error(row["last_error"])
            preview = st.text_area(
                "Draft preview",
                value=row.get("draft_preview") or "",
                key=f"preview-{row['message_id']}",
                height=220,
            )
            name = st.text_input("Save as template name", key=f"tpl-{row['message_id']}")
            if st.button("Save template", key=f"save-{row['message_id']}") and name.strip():
                bot.store.upsert_template(name.strip(), preview)
                st.success(f"Saved template “{name.strip()}”.")


def render_knowledge(bot) -> None:
    settings = bot.settings
    st.subheader("Knowledge base")
    st.caption("Local FAQs, markdown files, and optional website pages. Indexed with an on-disk TF-IDF store.")
    faqs_path: Path = settings.faqs_path
    current = faqs_path.read_text(encoding="utf-8") if faqs_path.is_file() else '{\n  "faqs": []\n}\n'
    faqs_text = st.text_area("manual FAQs (JSON)", value=current, height=240)
    urls = st.text_area("Website URLs to scrape (one per line)", value="")
    extra = st.text_area("Paste extra notes", value="", height=120)
    if st.button("Rebuild index", type="primary"):
        faqs_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            parsed = json.loads(faqs_text)
            faqs_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
        except json.JSONDecodeError as exc:
            st.error(f"FAQs JSON is invalid: {exc}")
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
            st.success(f"Indexed {count} chunks.")
        except Exception as exc:  # noqa: BLE001
            st.error(str(exc))
    chunks = bot.store.list_knowledge()
    st.write(f"Chunks in index: {len(chunks)}")
    if chunks:
        st.dataframe(
            [{"source": c["source"], "text": c["text"][:180]} for c in chunks[:80]],
            use_container_width=True,
            hide_index=True,
        )


def render_templates(bot) -> None:
    st.subheader("Email templates")
    templates = bot.store.list_templates()
    name = st.text_input("Template name")
    body = st.text_area("Template body", height=180)
    if st.button("Save template") and name.strip() and body.strip():
        bot.store.upsert_template(name.strip(), body)
        st.rerun()
    for row in templates:
        with st.expander(row["name"]):
            st.code(row["body"])
            if st.button("Delete", key=f"del-{row['id']}"):
                bot.store.delete_template(row["name"])
                st.rerun()


def render_settings(bot) -> None:
    settings = bot.settings
    st.subheader("Settings")
    st.write("Project folder:", str(settings.project_root))
    st.write("Credentials:", str(settings.credentials_path), "exists" if settings.credentials_path.is_file() else "MISSING")
    st.write("Token:", str(settings.token_path), "exists" if settings.token_path.is_file() else "MISSING")
    st.write("Database:", str(settings.database_path))
    st.write("Knowledge:", str(settings.knowledge_dir))
    st.markdown(
        """
1. Google Cloud Console → enable **Gmail API**.
2. OAuth consent screen (External is fine). Scope: `https://www.googleapis.com/auth/gmail.modify`.
3. Create an OAuth **Desktop** client and save JSON as `data/gmail/credentials.json`.
4. Run `python -m gmail_bot auth` once (browser login). Token stays in this folder.
5. This app **never sends** mail and **never marks messages as read**.
        """
    )


bot = _controller()
st.title("Gmail Draft Assistant")
st.caption("Portable local bot · drafts nested in the original thread · copy this folder to another PC and it keeps working.")

tab1, tab2, tab3, tab4, tab5 = st.tabs(["Control", "Queue", "Knowledge", "Templates", "Setup"])
with tab1:
    render_dashboard(bot)
with tab2:
    render_queue(bot)
with tab3:
    render_knowledge(bot)
with tab4:
    render_templates(bot)
with tab5:
    render_settings(bot)
