(function bootGmailDraftBot() {
if (window.__gmailDraftBotLoaded) {
  return;
}
window.__gmailDraftBotLoaded = true;

function normalizeEmail(value) {
  const match = String(value || "")
    .toLowerCase()
    .match(/[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}/);
  return match ? match[0] : "";
}

function lastEmail(value) {
  const matches = String(value || "")
    .toLowerCase()
    .match(/[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}/g);
  return matches && matches.length ? matches[matches.length - 1] : "";
}

function senderFromEl(el) {
  return (el?.getAttribute("email") || el?.innerText || "").trim();
}

function readPageAccount() {
  const fromTitle = lastEmail(document.title);
  if (fromTitle) {
    return fromTitle;
  }
  const labeled = document.querySelector(
    'a[aria-label*="Google Account"], a[aria-label*="@gmail.com"], img[aria-label*="@"]'
  );
  return lastEmail(labeled?.getAttribute("aria-label") || "");
}

function hasOpenThread() {
  return Boolean(document.querySelector("h2.hP, h2[data-legacy-thread-id]"));
}

function isDraftsView() {
  return /#drafts\b/i.test(location.hash || "") || /\/drafts/i.test(location.pathname || "");
}

function composeRoot() {
  const box = findComposeBox();
  return box?.closest(".AD, .aoI, .M9, .aO7, .ip, [role='dialog']") || box || null;
}

function readComposeTo() {
  const root = composeRoot() || document;
  const chips = [
    ...root.querySelectorAll('.vR span[email], .afx span[email], form span[email], [name="to"] span[email]'),
  ];
  const emails = [];
  chips.forEach((el) => {
    const email = normalizeEmail(el.getAttribute("email") || el.getAttribute("data-hovercard-id") || el.innerText);
    if (email && !emails.includes(email)) {
      emails.push(email);
    }
  });
  if (emails.length) {
    return emails.join(", ");
  }
  const input = root.querySelector(
    'textarea[name="to"], input[name="to"], input[aria-label="To recipients"], input[peoplekit-id], input[aria-label="To"]'
  );
  return (input?.value || "").trim();
}

function readComposeSubject() {
  const input = document.querySelector('input[name="subjectbox"], input[aria-label="Subject"]');
  return (input?.value || "").trim();
}

function composeModeSelected() {
  return Boolean(document.getElementById("gmail-bot-mode-compose")?.checked);
}

function shouldUseComposeMode(extras) {
  if (extras && extras.mode) {
    return String(extras.mode).toLowerCase() === "compose";
  }
  return composeModeSelected();
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
  const view = el.ownerDocument?.defaultView || window;
  const style = view.getComputedStyle(el);
  return style.visibility !== "hidden" && style.display !== "none";
}

function isComposeEditor(el) {
  if (!el) {
    return false;
  }
  const editable = (el.getAttribute("contenteditable") || "").toLowerCase();
  const gmailEditable = el.getAttribute("g_editable") === "true";
  if (editable !== "true" && editable !== "plaintext-only" && !gmailEditable) {
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
    gmailEditable ||
      (el.classList.contains("editable") && (el.classList.contains("Am") || el.classList.contains("Al"))) ||
      el.classList.contains("LW-avf") ||
      label.includes("message body") ||
      label.includes("compose body")
  );
}

function findComposeInDocument(doc) {
  if (!doc) {
    return null;
  }
  const nodes = [
    ...doc.querySelectorAll('[g_editable="true"]'),
    ...doc.querySelectorAll('div.Am.Al.editable[contenteditable="true"]'),
    ...doc.querySelectorAll('div.LW-avf[contenteditable="true"]'),
    ...doc.querySelectorAll('div[aria-label="Message Body"][contenteditable="true"]'),
    ...doc.querySelectorAll('div[aria-label="Compose body"][contenteditable="true"]'),
    ...doc.querySelectorAll('div[role="textbox"][contenteditable="true"]'),
  ];
  const matches = [...new Set(nodes)].filter(isComposeEditor);
  return matches[matches.length - 1] || null;
}

function findComposeBox() {
  const top = findComposeInDocument(document);
  if (top) {
    return top;
  }
  const frames = [...document.querySelectorAll("iframe")];
  for (const frame of frames) {
    try {
      const inner = findComposeInDocument(frame.contentDocument);
      if (inner) {
        return inner;
      }
    } catch (_error) {
      /* Cross-origin frames are skipped. */
    }
  }
  return null;
}

function stripQuotedText(text) {
  let raw = String(text || "").replace(/\r\n/g, "\n").replace(/[\u200b\u200c\u200d\ufeff]/g, "");
  raw = raw.split(
    /(?:^|\n|[>\s]{2,})(?:\s*>+\s*)*On\s+(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\s*,[\s\S]{0,220}?\bwrote\s*:/i
  )[0];
  raw = raw.split(/(?:^|\n)\s*(?:-+\s*forwarded message\s*-+|begin forwarded message)/i)[0];
  const lines = raw.split("\n");
  const kept = [];
  for (const line of lines) {
    const stripped = line.trim();
    const unquoted = stripped.replace(/^(>\s*)+/, "").trim();
    if (stripped.startsWith(">")) {
      break;
    }
    if (/^On\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun)/i.test(unquoted) && /wrote:|<|@|at\s+\d{1,2}:\d{2}|\d{4}/i.test(unquoted)) {
      break;
    }
    if (/^On\s+.+\bwrote:\s*$/i.test(unquoted) && /(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec|\d{4}|at\s+\d{1,2}:\d{2})/i.test(unquoted)) {
      break;
    }
    if (stripped === "--" || /^-+ forwarded message -+$/i.test(unquoted)) {
      break;
    }
    if (/^begin forwarded message/i.test(unquoted)) {
      break;
    }
    if (/@/.test(unquoted) && /\bwrote:\s*$/i.test(unquoted)) {
      break;
    }
    kept.push(line.replace(/^(>\s*)+/, ""));
  }
  return kept.join("\n").trim();
}

function quoteSelector() {
  return ".gmail_quote, .gmail_quote_container, .gmail_extra, .gmail_signature, blockquote.gmail_quote, [class*='gmail_quote']";
}

function removeQuotedBlocks(box) {
  if (!box) {
    return;
  }
  box.querySelectorAll(quoteSelector()).forEach((el) => el.remove());
}

function composeBodyText(box) {
  if (!box) {
    return "";
  }
  const copy = box.cloneNode(true);
  copy.querySelectorAll(quoteSelector()).forEach((el) => el.remove());
  return stripQuotedText(copy.innerText || copy.textContent || "");
}

function isJunkComposeText(text) {
  const raw = String(text || "");
  if (/bootGmailDraftBot|__gmailDraftBotLoaded|gmail-bot-fab|function normalizeEmail/.test(raw)) {
    return true;
  }
  const lines = raw.split(/\n/).map((line) => line.trim()).filter(Boolean);
  if (lines.length < 20) {
    return false;
  }
  const sample = lines.slice(0, 80);
  const numeric = sample.filter((line) => /^\d+$/.test(line)).length;
  return numeric / sample.length >= 0.7;
}

function readComposeText() {
  const text = composeBodyText(findComposeBox());
  return isJunkComposeText(text) ? "" : text;
}

function clickCompose() {
  const btn =
    document.querySelector('div[role="button"][gh="cm"]') ||
    document.querySelector('div[gh="cm"]') ||
    document.querySelector('div[role="button"][aria-label="Compose"]') ||
    document.querySelector('.T-I.T-I-KE.L3');
  if (btn) {
    btn.click();
  }
}

function fillComposeHeader(to, subject) {
  const sub = document.querySelector('input[name="subjectbox"], input[aria-label="Subject"]');
  if (sub && subject && !(sub.value || "").trim()) {
    sub.focus();
    sub.value = subject;
    sub.dispatchEvent(new Event("input", { bubbles: true }));
    sub.dispatchEvent(new Event("change", { bubbles: true }));
  }
  if (readComposeTo() || !to) {
    return;
  }
  const input = document.querySelector(
    'textarea[name="to"], input[name="to"], input[aria-label="To recipients"], input[aria-label="To"]'
  );
  if (!input) {
    return;
  }
  input.focus();
  input.value = to;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, key: "Enter" }));
}

function clickReply() {
  const replies = [
    ...document.querySelectorAll('div[role="button"][aria-label="Reply"]'),
    ...document.querySelectorAll('div[aria-label="Reply"]'),
    ...document.querySelectorAll('span[role="link"][data-tooltip="Reply"]'),
    ...document.querySelectorAll('span[data-tooltip="Reply"]'),
    ...document.querySelectorAll('div[data-tooltip="Reply"]'),
    ...document.querySelectorAll('[aria-label^="Reply"]'),
    ...document.querySelectorAll('div.ams.bkH'),
    ...document.querySelectorAll("span.ams.bkH"),
    ...document.querySelectorAll('span.ams'),
  ];
  const unique = [...new Set(replies)].filter(isVisible);
  const btn = unique[unique.length - 1] || unique[0];
  if (btn) {
    btn.click();
  }
}

function readThreadId() {
  const tagged = document.querySelector("[data-legacy-thread-id], [data-thread-perm-id], h2.hP");
  const fromDom =
    tagged?.getAttribute("data-legacy-thread-id") ||
    tagged?.getAttribute("data-thread-perm-id") ||
    "";
  if (fromDom) {
    return fromDom;
  }
  const hash = (location.hash || "").replace(/^#/, "").split("?")[0];
  const parts = hash.split("/").filter(Boolean);
  const last = parts[parts.length - 1] || "";
  return /^[a-f0-9]{10,}$/i.test(last) ? last : "";
}

function normalizeGmailDraftId(value) {
  let raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  try {
    raw = decodeURIComponent(raw);
  } catch (_error) {
    /* Keep the raw compose id if it is not encoded. */
  }
  raw = raw.split(",")[0].trim();
  const colon = raw.lastIndexOf(":");
  if (colon >= 0) {
    raw = raw.slice(colon + 1);
  }
  raw = raw.replace(/^#/, "").trim();
  if (!raw || /^(new|null|undefined)$/i.test(raw) || /^cllg/i.test(raw)) {
    return "";
  }
  if (/^r-?\d{6,}$/i.test(raw)) {
    return raw;
  }
  if (raw.length > 40) {
    return "";
  }
  return raw;
}

function isNewComposeWindow() {
  const href = location.href || "";
  const match = href.match(/[?&#]compose=([^&#]+)/i);
  if (match) {
    let raw = match[1];
    try {
      raw = decodeURIComponent(raw);
    } catch (_error) {
      /* Keep the compose token if it is not encoded. */
    }
    if (/^cllg/i.test(raw) || /^new$/i.test(raw)) {
      return true;
    }
  }
  return Boolean(findComposeBox() && !hasOpenThread());
}

function rememberDraftId(box, draftId) {
  const id = normalizeGmailDraftId(draftId);
  if (!box || !id) {
    return;
  }
  box.setAttribute("data-gmail-bot-draft-id", id);
}

function readComposeDraftId() {
  const box = findComposeBox();
  const remembered = normalizeGmailDraftId(box?.getAttribute("data-gmail-bot-draft-id") || "");
  if (remembered) {
    return remembered;
  }
  const href = location.href || "";
  const match = href.match(/[?&#]compose=([^&#]+)/i);
  if (match) {
    const id = normalizeGmailDraftId(match[1]);
    if (id) {
      return id;
    }
  }
  const hidden = document.querySelector('input[name="draft"], input[name="draft_id"]');
  if (hidden && hidden.value) {
    const id = normalizeGmailDraftId(hidden.value);
    if (id) {
      return id;
    }
  }
  const tagged = box?.getAttribute("data-draft-id") || document.querySelector("[data-draft-id]")?.getAttribute("data-draft-id");
  return normalizeGmailDraftId(tagged || "");
}

function openGmailDraft(draftId) {
  const id = normalizeGmailDraftId(draftId);
  if (!id || id === "new") {
    return;
  }
  const hash = location.hash || "#inbox";
  const base = hash.split("?")[0];
  const next = `${base}?compose=${encodeURIComponent(id)}`;
  if (location.hash !== next) {
    location.hash = next;
  }
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function ensureComposeBox(preferNew) {
  let box = findComposeBox();
  if (box) {
    return box;
  }
  if (preferNew) {
    clickCompose();
  } else {
    clickReply();
  }
  for (let i = 0; i < 30; i += 1) {
    await sleep(200);
    box = findComposeBox();
    if (box) {
      return box;
    }
    if (i === 8 || i === 16) {
      if (preferNew) {
        clickCompose();
      } else {
        clickReply();
      }
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
  const have = composeBodyText(box).replace(/\s+/g, " ");
  return have.includes(needle);
}

function escapeHtml(text) {
  return String(text || "").replace(/[&<>"']/g, (ch) => {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
  });
}

function selectEditor(box) {
  box.focus();
  const doc = box.ownerDocument || document;
  const view = doc.defaultView || window;
  const selection = view.getSelection();
  const range = doc.createRange();
  range.selectNodeContents(box);
  selection.removeAllRanges();
  selection.addRange(range);
}

function gmailBodyHtml(text) {
  return String(text || "")
    .split("\n")
    .map((line) => (line ? `<div>${escapeHtml(line)}</div>` : "<div><br></div>"))
    .join("");
}

function insertReply(box, text) {
  const doc = box.ownerDocument || document;
  const clean = stripQuotedText(text);
  removeQuotedBlocks(box);
  selectEditor(box);
  doc.execCommand("selectAll", false, null);
  const inserted = doc.execCommand("insertText", false, clean);
  if (!inserted || !hasDraftText(box, clean)) {
    selectEditor(box);
    doc.execCommand("selectAll", false, null);
    doc.execCommand("insertHTML", false, gmailBodyHtml(clean) || "<div><br></div>");
  }
  if (!hasDraftText(box, clean)) {
    try {
      const data = new DataTransfer();
      data.setData("text/plain", clean);
      data.setData("text/html", gmailBodyHtml(clean));
      box.dispatchEvent(
        new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true })
      );
    } catch (_error) {
      /* Gmail may block synthetic paste; other methods still apply. */
    }
  }
  if (!hasDraftText(box, clean)) {
    box.innerHTML = gmailBodyHtml(clean) || "<div><br></div>";
  }
  if (!hasDraftText(box, clean)) {
    box.textContent = "";
    String(clean || "")
      .split("\n")
      .forEach((line, index) => {
        if (index) {
          box.appendChild(doc.createElement("br"));
        }
        box.appendChild(doc.createTextNode(line));
      });
  }
  removeQuotedBlocks(box);
  box.dispatchEvent(
    new InputEvent("input", { bubbles: true, cancelable: true, inputType: "insertFromPaste", data: clean })
  );
  box.dispatchEvent(new Event("change", { bubbles: true }));
  box.dispatchEvent(new KeyboardEvent("keyup", { bubbles: true, key: "End" }));
  box.scrollIntoView({ block: "center", behavior: "smooth" });
}

async function fillComposeReliable(box, text) {
  insertReply(box, text);
  for (let i = 0; i < 10; i += 1) {
    if (hasDraftText(box, text)) {
      return true;
    }
    await sleep(250);
    const live = findComposeBox() || box;
    insertReply(live, text);
    box = live;
  }
  return hasDraftText(findComposeBox() || box, text);
}

async function keepDraftVisible(text) {
  let placed = false;
  const deadline = Date.now() + 2800;
  while (Date.now() < deadline) {
    const box = findComposeBox();
    if (box) {
      removeQuotedBlocks(box);
      if (hasDraftText(box, text)) {
        placed = true;
      } else {
        insertReply(box, text);
        placed = hasDraftText(box, text) || placed;
      }
    }
    await sleep(350);
  }
  return placed;
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

function preferredMode() {
  if (isNewComposeWindow() && !hasOpenThread()) {
    return "compose";
  }
  if (isDraftsView() && !hasOpenThread()) {
    return "compose";
  }
  if (hasOpenThread()) {
    return "reply";
  }
  return findComposeBox() && !hasOpenThread() ? "compose" : "reply";
}

function syncPanelMode() {
  const compose = composeModeSelected();
  const toWrap = document.getElementById("gmail-bot-to-wrap");
  const draftBtn = document.getElementById("gmail-bot-draft");
  const redraftBtn = document.getElementById("gmail-bot-redraft");
  if (toWrap) {
    toWrap.hidden = !compose;
  }
  if (draftBtn) {
    draftBtn.textContent = compose ? "Write a new mail" : "Draft reply";
  }
  if (redraftBtn) {
    redraftBtn.textContent = "Redraft & grammar";
    redraftBtn.hidden = false;
  }
  const toEl = document.getElementById("gmail-bot-to");
  if (toEl && !toEl.value) {
    toEl.value = readComposeTo();
  }
  const subjectEl = document.getElementById("gmail-bot-subject");
  if (subjectEl && !subjectEl.value) {
    subjectEl.value = readComposeSubject();
  }
}

function setRadioCaption(input, caption) {
  if (!input) {
    return;
  }
  const label = input.closest("label") || input.parentElement;
  if (!label) {
    return;
  }
  [...label.childNodes].forEach((node) => {
    if (node !== input) {
      node.remove();
    }
  });
  label.appendChild(document.createTextNode(` ${caption}`));
}

function setModeLabels(root) {
  setRadioCaption(root.querySelector("#gmail-bot-mode-compose"), "Write a new mail");
  setRadioCaption(root.querySelector("#gmail-bot-mode-reply"), "Reply to an open mail");
}

function ensureSubjectFields(toWrap) {
  if (!toWrap || document.getElementById("gmail-bot-subject")) {
    return;
  }
  const subjectLabel = document.createElement("label");
  subjectLabel.setAttribute("for", "gmail-bot-subject");
  subjectLabel.textContent = "Subject";
  const subjectInput = document.createElement("input");
  subjectInput.id = "gmail-bot-subject";
  subjectInput.type = "text";
  subjectInput.placeholder = "Optional subject";
  toWrap.append(subjectLabel, subjectInput);
}

function upgradePanel() {
  const panel = document.getElementById("gmail-bot-panel");
  if (!panel) {
    return;
  }
  const hint = panel.querySelector(".hint");
  if (hint) {
    hint.textContent =
      "Pick Write a new mail or Reply to an open mail. Redraft & grammar works for both. URL and notes are optional.";
  }
  if (document.getElementById("gmail-bot-mode")) {
    setModeLabels(panel);
    ensureSubjectFields(document.getElementById("gmail-bot-to-wrap"));
    syncPanelMode();
    return;
  }
  const mode = document.createElement("fieldset");
  mode.id = "gmail-bot-mode";
  mode.className = "mode";
  const legend = document.createElement("legend");
  legend.textContent = "What should the bot do?";
  mode.appendChild(legend);
  mode.insertAdjacentHTML(
    "beforeend",
    '<label><input type="radio" name="gmail-bot-mode" id="gmail-bot-mode-compose" value="compose" /> Write a new mail</label>' +
      '<label><input type="radio" name="gmail-bot-mode" id="gmail-bot-mode-reply" value="reply" /> Reply to an open mail</label>'
  );
  const toWrap = document.createElement("div");
  toWrap.id = "gmail-bot-to-wrap";
  toWrap.hidden = true;
  const toLabel = document.createElement("label");
  toLabel.setAttribute("for", "gmail-bot-to");
  toLabel.textContent = "To";
  const toInput = document.createElement("input");
  toInput.id = "gmail-bot-to";
  toInput.type = "text";
  toInput.placeholder = "name@example.com";
  toWrap.append(toLabel, toInput);
  ensureSubjectFields(toWrap);
  if (hint && hint.nextSibling) {
    panel.insertBefore(mode, hint.nextSibling);
    panel.insertBefore(toWrap, mode.nextSibling);
  } else {
    panel.prepend(mode, toWrap);
  }
  const start = preferredMode();
  const composeRadio = document.getElementById("gmail-bot-mode-compose");
  const replyRadio = document.getElementById("gmail-bot-mode-reply");
  if (start === "compose" && composeRadio) {
    composeRadio.checked = true;
  } else if (replyRadio) {
    replyRadio.checked = true;
  }
  mode.querySelectorAll("input").forEach((el) => {
    el.addEventListener("change", syncPanelMode);
  });
  syncPanelMode();
}

function ensurePanel() {
  if (!document.getElementById("gmail-bot-panel")) {
    const panel = document.createElement("div");
    panel.id = "gmail-bot-panel";
    const hint = document.createElement("p");
    hint.className = "hint";
    hint.textContent =
      "Pick Write a new mail or Reply to an open mail. Redraft & grammar works for both. URL and notes are optional.";
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
    notes.placeholder = "What should this email say? (optional)";
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
  upgradePanel();
}

function togglePanel() {
  ensurePanel();
  const panel = document.getElementById("gmail-bot-panel");
  if (!panel) {
    return;
  }
  panel.classList.toggle("is-open");
  if (panel.classList.contains("is-open") && isNewComposeWindow() && !hasOpenThread()) {
    const composeRadio = document.getElementById("gmail-bot-mode-compose");
    if (composeRadio) {
      composeRadio.checked = true;
      syncPanelMode();
    }
  }
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
  const toEl = document.getElementById("gmail-bot-to");
  const subjectEl = document.getElementById("gmail-bot-subject");
  const composeRadio = document.getElementById("gmail-bot-mode-compose");
  const replyRadio = document.getElementById("gmail-bot-mode-reply");
  if (urlEl && extras.rulesUrl != null) {
    urlEl.value = extras.rulesUrl;
  }
  if (notesEl && extras.notes != null) {
    notesEl.value = extras.notes;
  }
  if (toEl && extras.to != null) {
    toEl.value = extras.to;
  }
  if (subjectEl && extras.subject != null) {
    subjectEl.value = extras.subject;
  }
  if (String(extras.mode || "").toLowerCase() === "compose" && composeRadio) {
    composeRadio.checked = true;
  } else if (String(extras.mode || "").toLowerCase() === "reply" && replyRadio) {
    replyRadio.checked = true;
  }
  syncPanelMode();
}

async function runDraft(redraft, extras) {
  applyOptionalFields(extras);
  const health = await fetchHealth();
  let composeMode = shouldUseComposeMode(extras);
  if (!composeMode && !(extras && extras.mode) && isNewComposeWindow() && !hasOpenThread()) {
    composeMode = true;
    const composeRadio = document.getElementById("gmail-bot-mode-compose");
    if (composeRadio) {
      composeRadio.checked = true;
      syncPanelMode();
    }
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
  toast(
    redraft
      ? composeMode
        ? "Redrafting the new mail…"
        : "Redrafting the reply…"
      : composeMode
        ? "Writing a new mail…"
        : "Drafting a reply…"
  );
  await ensureComposeBox(composeMode);
  await sleep(400);
  const existing = readComposeText();
  const gmailDraftId = readComposeDraftId();
  const payload = {
    existingDraft: existing,
    rulesUrl,
    notes,
    gmailDraftId,
    pageEmail: readPageAccount(),
  };
  if (composeMode) {
    payload.mode = "compose";
    payload.to = (
      extras && extras.to != null ? extras.to : document.getElementById("gmail-bot-to")?.value || readComposeTo()
    ).trim();
    payload.subject = (
      extras && extras.subject != null
        ? extras.subject
        : document.getElementById("gmail-bot-subject")?.value || readComposeSubject()
    ).trim();
    payload.sender = payload.to;
    if (!payload.to && !gmailDraftId) {
      setBusy(false);
      const error = "Enter who this new mail is To, or open a draft that already has a recipient.";
      toast(error);
      return { ok: false, error };
    }
  } else {
    const email = readOpenEmail(health.connected_email || health.target_email || "");
    if (!email.subject && !email.sender) {
      setBusy(false);
      const error = "Open a mail first, or switch to Write a new mail.";
      toast(error);
      return { ok: false, error };
    }
    payload.mode = "reply";
    payload.subject = email.subject;
    payload.sender = email.sender;
    payload.body = email.body;
    payload.threadId = readThreadId();
  }
  const response = await sendRuntime({
    action: "draftOpen",
    payload,
  });
  setBusy(false);
  if (!(response && response.ok && (response.draftText || response.text))) {
    const error = (response && response.error) || "Could not create a draft.";
    toast(error);
    return { ok: false, error };
  }
  const text = response.draftText || response.text;
  const savedId = response.draft_id || response.draftId || "";
  showDraftPreview(text);
  showSuggestions(response.suggestions);
  await sleep(400);
  let box = await ensureComposeBox(composeMode);
  rememberDraftId(box, savedId);
  fillComposeHeader(payload.to || "", response.subject || payload.subject || "");
  let placed = false;
  if (box && hasDraftText(box, text)) {
    placed = true;
  } else if (box) {
    placed = await fillComposeReliable(box, text);
  }
  if (!placed && savedId && normalizeGmailDraftId(savedId) !== normalizeGmailDraftId(gmailDraftId)) {
    if (isNewComposeWindow()) {
      box = findComposeBox() || box;
      if (box) {
        placed = await fillComposeReliable(box, text);
      }
    } else {
      openGmailDraft(savedId);
      await sleep(900);
      box = await ensureComposeBox(composeMode);
      fillComposeHeader(payload.to || "", response.subject || payload.subject || "");
      if (box) {
        placed = await fillComposeReliable(box, text);
      }
    }
  }
  rememberDraftId(findComposeBox() || box, savedId);
  placed = (await keepDraftVisible(text)) || placed;
  if (placed) {
    toast(
      composeMode
        ? "New mail draft is in your Gmail compose box. No need to refresh."
        : "Reply draft is in the reply box. No need to refresh."
    );
  } else {
    toast("Draft is ready in the bot card. Open the Gmail draft if the box is hidden.");
  }
  return { ok: true, placed, draftText: text, suggestions: response.suggestions };
}

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.action === "ping") {
    sendResponse({ ok: true });
    return;
  }
  if (request.action !== "runDraft") {
    return;
  }
  runDraft(Boolean(request.redraft), {
    rulesUrl: request.rulesUrl || "",
    notes: request.notes || "",
    mode: request.mode || "",
    to: request.to || "",
    subject: request.subject || "",
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
})();
