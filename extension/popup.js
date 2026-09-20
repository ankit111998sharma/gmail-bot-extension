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
  status.textContent = `Connected: ${email} · ${ai}`;
  status.classList.remove("is-error");
}

async function runDraft(redraft) {
  setBusy(true);
  showMessage(redraft ? "Fixing grammar and redrafting…" : "Writing your reply…");
  showPreview("");
  showSuggestions([]);
  const result = await send({
    action: "draftFromPopup",
    redraft: Boolean(redraft),
    rulesUrl: (document.getElementById("website-url").value || "").trim(),
    notes: (document.getElementById("draft-notes").value || "").trim(),
  });
  setBusy(false);
  if (!(result && result.ok && (result.draftText || result.text))) {
    showMessage((result && result.error) || "Could not create a draft.", true);
    return;
  }
  const text = result.draftText || result.text;
  showPreview(text);
  showSuggestions(result.suggestions);
  showMessage(result.placed ? "Draft is in the Gmail reply box." : "Draft is ready. Check Gmail if the reply box is closed.");
}

document.getElementById("draft-btn").addEventListener("click", () => runDraft(false));
document.getElementById("redraft-btn").addEventListener("click", () => runDraft(true));
refreshStatus();
