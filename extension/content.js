function normalizeEmail(value) {
  const match = String(value || "")
    .toLowerCase()
    .match(/[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}/);
  return match ? match[0] : "";
}

function senderFromEl(el) {
  return (el?.getAttribute("email") || el?.innerText || "").trim();
}

function readOpenEmail(ownerEmail) {
  const subject =
    document.querySelector("h2.hP")?.innerText ||
    document.querySelector("h2[data-legacy-thread-id]")?.innerText ||
    "";
  const owner = normalizeEmail(ownerEmail);
  const senders = [...document.querySelectorAll("span.gD")];
  let senderEl = null;
  for (let i = senders.length - 1; i >= 0; i -= 1) {
    const email = normalizeEmail(senderFromEl(senders[i]));
    if (email && (!owner || email !== owner)) {
      senderEl = senders[i];
      break;
    }
  }
  if (!senderEl && senders.length) {
    senderEl = senders[senders.length - 1];
  }
  const bodies = document.querySelectorAll("div.a3s.aiL");
  const body = bodies.length ? bodies[bodies.length - 1].innerText : "";
  return {
    subject: subject.trim(),
    sender: senderFromEl(senderEl),
    body: body.trim(),
  };
}

function toast(text) {
  let el = document.getElementById("gmail-bot-toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "gmail-bot-toast";
    document.body.appendChild(el);
  }
  el.textContent = text;
  el.style.display = "block";
  window.setTimeout(() => {
    el.style.display = "none";
  }, 5000);
}

function isVisible(el) {
  if (!el || !el.getClientRects().length) {
    return false;
  }
  const style = window.getComputedStyle(el);
  return style.visibility !== "hidden" && style.display !== "none";
}

function isComposeEditor(el) {
  if (!el || el.getAttribute("contenteditable") !== "true") {
    return false;
  }
  if (!isVisible(el)) {
    return false;
  }
  if (el.closest("form")?.getAttribute("role") === "search") {
    return false;
  }
  const label = (el.getAttribute("aria-label") || "").toLowerCase();
  if (label.includes("search")) {
    return false;
  }
  return Boolean(
    el.classList.contains("editable") ||
      el.classList.contains("LW-avf") ||
      label.includes("message body") ||
      label.includes("compose") ||
      el.closest(".M9, .aoI, .ip, .gA, [aria-label='Reply']")
  );
}

function findComposeBox() {
  const nodes = [
    ...document.querySelectorAll('div.Am.Al.editable[contenteditable="true"]'),
    ...document.querySelectorAll('div.LW-avf[contenteditable="true"]'),
    ...document.querySelectorAll('div[aria-label="Message Body"][contenteditable="true"]'),
    ...document.querySelectorAll('div[aria-label="Compose body"][contenteditable="true"]'),
    ...document.querySelectorAll('div[role="textbox"][contenteditable="true"]'),
  ];
  const matches = nodes.filter(isComposeEditor);
  return matches[matches.length - 1] || null;
}

function readComposeText() {
  const box = findComposeBox();
  return box ? (box.innerText || "").trim() : "";
}

function clickReply() {
  const replies = [
    ...document.querySelectorAll('div[aria-label="Reply"]'),
    ...document.querySelectorAll('span[data-tooltip="Reply"]'),
    ...document.querySelectorAll('div[data-tooltip="Reply"]'),
    ...document.querySelectorAll('[aria-label^="Reply"]'),
  ];
  const unique = [...new Set(replies)];
  const btn = unique[unique.length - 1] || unique[0];
  if (btn) {
    btn.click();
  }
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function ensureComposeBox() {
  let box = findComposeBox();
  if (box) {
    return box;
  }
  clickReply();
  for (let i = 0; i < 20; i += 1) {
    await sleep(200);
    box = findComposeBox();
    if (box) {
      return box;
    }
  }
  return null;
}

function draftNeedle(text) {
  return String(text || "")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 48);
}

function hasDraftText(box, text) {
  const needle = draftNeedle(text);
  if (!needle || !box) {
    return false;
  }
  const have = (box.innerText || box.textContent || "").replace(/\s+/g, " ");
  return have.includes(needle);
}

function escapeHtml(text) {
  return String(text || "").replace(/[&<>"']/g, (ch) => {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
  });
}

function selectEditor(box) {
  box.focus();
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(box);
  selection.removeAllRanges();
  selection.addRange(range);
}

function insertReply(box, text) {
  selectEditor(box);
  document.execCommand("selectAll", false, null);
  const inserted = document.execCommand("insertText", false, text);
  if (!inserted || !hasDraftText(box, text)) {
    selectEditor(box);
    document.execCommand("insertHTML", false, escapeHtml(text).replace(/\n/g, "<br>"));
  }
  if (!hasDraftText(box, text)) {
    try {
      const data = new DataTransfer();
      data.setData("text/plain", text);
      box.dispatchEvent(
        new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true })
      );
    } catch (_error) {
      /* Gmail may block synthetic paste; other methods still apply. */
    }
  }
  if (!hasDraftText(box, text)) {
    box.textContent = "";
    String(text || "")
      .split("\n")
      .forEach((line, index) => {
        if (index) {
          box.appendChild(document.createElement("br"));
        }
        box.appendChild(document.createTextNode(line));
      });
  }
  box.dispatchEvent(
    new InputEvent("input", { bubbles: true, cancelable: true, inputType: "insertText", data: text })
  );
  box.dispatchEvent(new Event("change", { bubbles: true }));
  box.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: "End" }));
  box.scrollIntoView({ block: "center", behavior: "smooth" });
}

async function fillComposeReliable(box, text) {
  insertReply(box, text);
  for (let i = 0; i < 8; i += 1) {
    if (hasDraftText(box, text)) {
      return true;
    }
    await sleep(250);
    const live = findComposeBox() || box;
    insertReply(live, text);
  }
  return hasDraftText(findComposeBox() || box, text);
}

function showDraftPreview(text) {
  const panel = document.getElementById("gmail-bot-panel");
  if (!panel) {
    return;
  }
  let preview = document.getElementById("gmail-bot-preview");
  if (!preview) {
    preview = document.createElement("div");
    preview.id = "gmail-bot-preview";
    panel.appendChild(preview);
  }
  preview.replaceChildren();
  const heading = document.createElement("p");
  heading.textContent = "Draft";
  const body = document.createElement("pre");
  body.textContent = text;
  preview.append(heading, body);
}

function showSuggestions(items) {
  const box = document.getElementById("gmail-bot-suggestions");
  if (!box) {
    return;
  }
  box.replaceChildren();
  const list = Array.isArray(items) ? items.filter(Boolean) : [];
  if (!list.length) {
    return;
  }
  const heading = document.createElement("p");
  heading.textContent = "Suggestions from the rules page";
  const ul = document.createElement("ul");
  list.slice(0, 5).forEach((item) => {
    const li = document.createElement("li");
    li.textContent = String(item);
    ul.appendChild(li);
  });
  box.append(heading, ul);
}

function ensurePanel() {
  if (document.getElementById("gmail-bot-panel")) {
    return;
  }
  const panel = document.createElement("div");
  panel.id = "gmail-bot-panel";
  const hint = document.createElement("p");
  hint.className = "hint";
  hint.textContent = "Both fields are optional. Leave blank to skip. A URL uses the website; notes guide the draft. AI corrects the text when available.";
  const urlLabel = document.createElement("label");
  urlLabel.setAttribute("for", "gmail-bot-url");
  urlLabel.textContent = "Website URL";
  const input = document.createElement("input");
  input.id = "gmail-bot-url";
  input.type = "url";
  input.placeholder = "https://example.com/rules (optional)";
  const notesLabel = document.createElement("label");
  notesLabel.setAttribute("for", "gmail-bot-notes");
  notesLabel.textContent = "Description for this draft";
  const notes = document.createElement("textarea");
  notes.id = "gmail-bot-notes";
  notes.placeholder = "What should this reply say? (optional)";
  const actions = document.createElement("div");
  actions.id = "gmail-bot-actions";
  const draftBtn = document.createElement("button");
  draftBtn.id = "gmail-bot-draft";
  draftBtn.type = "button";
  draftBtn.textContent = "Draft reply";
  const redraftBtn = document.createElement("button");
  redraftBtn.id = "gmail-bot-redraft";
  redraftBtn.type = "button";
  redraftBtn.textContent = "Redraft & grammar";
  draftBtn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    runDraft(false);
  });
  redraftBtn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    runDraft(true);
  });
  actions.append(draftBtn, redraftBtn);
  const suggestions = document.createElement("div");
  suggestions.id = "gmail-bot-suggestions";
  panel.append(hint, urlLabel, input, notesLabel, notes, actions, suggestions);
  document.body.appendChild(panel);
}

function togglePanel() {
  ensurePanel();
  const panel = document.getElementById("gmail-bot-panel");
  if (!panel) {
    return;
  }
  panel.classList.toggle("is-open");
}

function ensureButton() {
  if (document.getElementById("gmail-bot-fab")) {
    return;
  }
  const btn = document.createElement("button");
  btn.id = "gmail-bot-fab";
  btn.type = "button";
  btn.title = "Open draft options";
  btn.setAttribute("aria-label", "Open draft options");
  btn.textContent = "✉️";
  btn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    togglePanel();
  });
  document.body.appendChild(btn);
}

function sendRuntime(message) {
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

function fetchHealth() {
  return sendRuntime({ action: "health" });
}

function setBusy(busy) {
  ["gmail-bot-fab", "gmail-bot-draft", "gmail-bot-redraft"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.disabled = busy;
    }
  });
}

function applyOptionalFields(extras) {
  if (!extras) {
    return;
  }
  ensurePanel();
  const urlEl = document.getElementById("gmail-bot-url");
  const notesEl = document.getElementById("gmail-bot-notes");
  if (urlEl && extras.rulesUrl != null) {
    urlEl.value = extras.rulesUrl;
  }
  if (notesEl && extras.notes != null) {
    notesEl.value = extras.notes;
  }
}

async function runDraft(redraft, extras) {
  applyOptionalFields(extras);
  const health = await fetchHealth();
  const email = readOpenEmail(health.connected_email || health.target_email || "");
  if (!email.subject && !email.sender) {
    const error = "Open an email first, then draft or redraft.";
    toast(error);
    return { ok: false, error };
  }
  const existing = readComposeText();
  if (redraft && !existing) {
    const error = "Open the reply box with a draft first, then click Redraft.";
    toast(error);
    return { ok: false, error };
  }
  const rulesUrl = (
    extras && extras.rulesUrl != null
      ? extras.rulesUrl
      : document.getElementById("gmail-bot-url")?.value || ""
  ).trim();
  const notes = (
    extras && extras.notes != null
      ? extras.notes
      : document.getElementById("gmail-bot-notes")?.value || ""
  ).trim();
  setBusy(true);
  toast(redraft ? "Fixing grammar and redrafting…" : "Writing your reply…");
  if (!redraft) {
    ensureComposeBox();
  }
  const response = await sendRuntime({
    action: "draftOpen",
    payload: {
      ...email,
      existingDraft: redraft ? existing : "",
      rulesUrl,
      notes,
    },
  });
  setBusy(false);
  if (!(response && response.ok && (response.draftText || response.text))) {
    const error = (response && response.error) || "Could not create a draft.";
    toast(error);
    return { ok: false, error };
  }
  const text = response.draftText || response.text;
  showDraftPreview(text);
  showSuggestions(response.suggestions);
  const box = await ensureComposeBox();
  if (!box) {
    const message = "Draft is ready in the bot card. Click Reply if you also want it in Gmail's box.";
    toast(message);
    return { ok: true, placed: false, draftText: text, suggestions: response.suggestions };
  }
  const placed = await fillComposeReliable(box, text);
  toast(
    placed
      ? "Draft is in the reply box. No need to refresh."
      : "Draft is ready in the bot card. Click the reply box and try Redraft if Gmail hid it."
  );
  return { ok: true, placed, draftText: text, suggestions: response.suggestions };
}

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.action !== "runDraft") {
    return;
  }
  runDraft(Boolean(request.redraft), {
    rulesUrl: request.rulesUrl || "",
    notes: request.notes || "",
  })
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
  return true;
});

ensurePanel();
ensureButton();
setInterval(() => {
  ensurePanel();
  ensureButton();
}, 2000);
