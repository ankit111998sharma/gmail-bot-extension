function send(message) {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(message, (response) => {
      if (chrome.runtime.lastError) {
        resolve({ ok: false, error: chrome.runtime.lastError.message });
        return;
      }
      resolve(response || {});
    });
  });
}

function setBusy(busy) {
  document.getElementById("draft-btn").disabled = busy;
  document.getElementById("redraft-btn").disabled = busy;
}

function showMessage(text, isError) {
  const el = document.getElementById("message");
  el.hidden = !text;
  el.textContent = text || "";
  el.classList.toggle("is-error", Boolean(isError));
}

function showPreview(text) {
  const box = document.getElementById("preview");
  const body = document.getElementById("preview-text");
  if (!text) {
    box.hidden = true;
    body.textContent = "";
    return;
  }
  box.hidden = false;
  body.textContent = text;
}

function showSuggestions(items) {
  const list = document.getElementById("suggestions");
  list.replaceChildren();
  (Array.isArray(items) ? items.filter(Boolean) : []).slice(0, 5).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = String(item);
    list.appendChild(li);
  });
}

async function refreshStatus() {
  const status = document.getElementById("status");
  const health = await send({ action: "health" });
  if (!health.ok) {
    status.textContent = health.error || "Start run.bat first.";
    status.classList.add("is-error");
    return;
  }
  const email = health.connected_email || health.target_email || "Gmail";
  const ai = health.ai_ready ? "AI ready" : "AI optional";
  const guard = health.guardian && (health.guardian.fixed || []).length ? " · auto-repair on" : "";
  status.textContent = `Connected: ${email} · ${ai}${guard}`;
  status.classList.remove("is-error");
}

function selectedMode() {
  return document.getElementById("mode-compose")?.checked ? "compose" : "reply";
}

function syncMode() {
  const compose = selectedMode() === "compose";
  const wrap = document.getElementById("draft-to-wrap");
  if (wrap) {
    wrap.hidden = !compose;
  }
  const btn = document.getElementById("draft-btn");
  if (btn) {
    btn.textContent = compose ? "Write a new mail" : "Draft reply";
  }
}

async function requestDraft(redraft) {
  return send({
    action: "draftFromPopup",
    redraft: Boolean(redraft),
    rulesUrl: (document.getElementById("website-url").value || "").trim(),
    notes: (document.getElementById("draft-notes").value || "").trim(),
    mode: selectedMode(),
    to: (document.getElementById("draft-to")?.value || "").trim(),
    subject: (document.getElementById("draft-subject")?.value || "").trim(),
  });
}

function shouldRetry(result) {
  const error = ((result && result.error) || "").toLowerCase();
  if (!error) {
    return false;
  }
  return /label|timeout|refresh the gmail|not running|could not create|connection|quota/.test(error);
}

async function runDraft(redraft) {
  setBusy(true);
  const compose = selectedMode() === "compose";
  showMessage(
    redraft
      ? compose
        ? "Redrafting the new mail…"
        : "Redrafting the reply…"
      : compose
        ? "Writing a new mail…"
        : "Drafting a reply…"
  );
  showPreview("");
  showSuggestions([]);
  let result = await requestDraft(redraft);
  if (!(result && result.ok && (result.draftText || result.text)) && shouldRetry(result)) {
    showMessage("Retrying…");
    result = await requestDraft(redraft);
  }
  setBusy(false);
  if (!(result && result.ok && (result.draftText || result.text))) {
    showMessage((result && result.error) || "Could not create a draft.", true);
    return;
  }
  const text = result.draftText || result.text;
  showPreview(text);
  showSuggestions(result.suggestions);
  showMessage(
    result.placed
      ? selectedMode() === "compose"
        ? "New mail draft is in your Gmail compose box."
        : "Reply draft is in the Gmail reply box."
      : "Draft is ready. Check Gmail Drafts if the compose box is closed."
  );
}

document.getElementById("draft-btn").addEventListener("click", () => runDraft(false));
document.getElementById("redraft-btn").addEventListener("click", () => runDraft(true));
document.getElementById("mode-reply").addEventListener("change", syncMode);
document.getElementById("mode-compose").addEventListener("change", syncMode);
syncMode();
refreshStatus();
