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
  const label = document.createElement("label");
  label.setAttribute("for", "gmail-bot-url");
  label.textContent = "Rules or regulations URL";
  const input = document.createElement("input");
  input.id = "gmail-bot-url";
  input.type = "url";
  input.placeholder = "https://example.com/rules";
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
  panel.append(label, input, actions, suggestions);
  document.body.appendChild(panel);
  fetchHealth().then((health) => {
    if (health.rules_url && !input.value) {
      input.value = health.rules_url;
    }
  });
}

function ensureButton() {
  if (document.getElementById("gmail-bot-fab")) {
    return;
  }
  const btn = document.createElement("button");
  btn.id = "gmail-bot-fab";
  btn.type = "button";
  btn.title = "Draft a reply for this email";
  btn.setAttribute("aria-label", "Draft a reply for this email");
  btn.textContent = "✉️";
  btn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopPropagation();
    runDraft(false);
  });
  document.body.appendChild(btn);
}

function fetchHealth() {
  return new Promise((resolve) => {
    chrome.runtime.sendMessage({ action: "health" }, (response) => {
      if (chrome.runtime.lastError) {
        resolve({});
        return;
      }
      resolve(response || {});
    });
  });
}

function setBusy(busy) {
  ["gmail-bot-fab", "gmail-bot-draft", "gmail-bot-redraft"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) {
      el.disabled = busy;
    }
  });
}

async function runDraft(redraft) {
  const health = await fetchHealth();
  const email = readOpenEmail(health.connected_email || health.target_email || "");
  if (!email.subject && !email.sender) {
    toast("Open an email first, then draft or redraft.");
    return;
  }
  const existing = readComposeText();
  if (redraft && !existing) {
    toast("Open the reply box with a draft first, then click Redraft.");
    return;
  }
  setBusy(true);
  toast(redraft ? "Fixing grammar and redrafting…" : "Writing your reply…");
  if (!redraft) {
    ensureComposeBox();
  }
  const payload = {
    ...email,
    existingDraft: redraft ? existing : "",
    rulesUrl: document.getElementById("gmail-bot-url")?.value || "",
  };
  chrome.runtime.sendMessage({ action: "draftOpen", payload }, async (response) => {
    setBusy(false);
    if (chrome.runtime.lastError) {
      toast(chrome.runtime.lastError.message);
      return;
    }
    if (!(response && response.ok && (response.draftText || response.text))) {
      toast((response && response.error) || "Could not create a draft.");
      return;
    }
    const text = response.draftText || response.text;
    showDraftPreview(text);
    showSuggestions(response.suggestions);
    if (response.rulesUrl && document.getElementById("gmail-bot-url") && !document.getElementById("gmail-bot-url").value) {
      document.getElementById("gmail-bot-url").value = response.rulesUrl;
    }
    const box = await ensureComposeBox();
    if (!box) {
      toast("Draft is ready in the bot card. Click Reply if you also want it in Gmail's box.");
      return;
    }
    const placed = await fillComposeReliable(box, text);
    toast(
      placed
        ? "Draft is in the reply box. No need to refresh."
        : "Draft is ready in the bot card. Click the reply box and try Redraft if Gmail hid it."
    );
  });
}

ensurePanel();
ensureButton();
setInterval(() => {
  ensurePanel();
  ensureButton();
}, 2000);
