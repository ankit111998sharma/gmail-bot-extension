function readOpenEmail() {
  const subject =
    document.querySelector("h2.hP")?.innerText ||
    document.querySelector("h2[data-legacy-thread-id]")?.innerText ||
    "";
  const senderEl = document.querySelector("span.gD");
  const sender = senderEl?.getAttribute("email") || senderEl?.innerText || "";
  const bodies = document.querySelectorAll("div.a3s.aiL");
  const body = bodies.length ? bodies[bodies.length - 1].innerText : "";
  return {
    subject: subject.trim(),
    sender: sender.trim(),
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

function findComposeBox() {
  const nodes = [
    ...document.querySelectorAll(
      'div[aria-label="Message Body"], div[role="textbox"][contenteditable="true"], div.Am.Al.editable'
    ),
  ];
  return nodes.find((node) => node.offsetParent !== null) || nodes[nodes.length - 1] || null;
}

function clickReply() {
  const reply =
    document.querySelector('div[aria-label="Reply"]') ||
    document.querySelector('span[data-tooltip="Reply"]') ||
    document.querySelector('div[data-tooltip="Reply"]') ||
    document.querySelector('[aria-label^="Reply"]');
  if (reply) {
    reply.click();
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
  for (let i = 0; i < 12; i += 1) {
    await sleep(250);
    box = findComposeBox();
    if (box) {
      return box;
    }
  }
  return null;
}

function insertReply(box, text) {
  box.focus();
  const selection = window.getSelection();
  const range = document.createRange();
  range.selectNodeContents(box);
  selection.removeAllRanges();
  selection.addRange(range);
  document.execCommand("insertText", false, text);
  box.dispatchEvent(new InputEvent("input", { bubbles: true }));
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
  btn.addEventListener("click", onClick);
  document.body.appendChild(btn);
}

async function onClick(event) {
  event.preventDefault();
  event.stopPropagation();
  const btn = event.currentTarget;
  const email = readOpenEmail();
  if (!email.subject && !email.sender) {
    toast("Open an email first, then click the bot button.");
    return;
  }
  btn.disabled = true;
  toast("Writing your reply…");
  chrome.runtime.sendMessage({ action: "draftOpen", payload: email }, async (response) => {
    btn.disabled = false;
    if (chrome.runtime.lastError) {
      toast(chrome.runtime.lastError.message);
      return;
    }
    if (!(response && response.ok && (response.draftText || response.text))) {
      toast((response && response.error) || "Could not create a draft.");
      return;
    }
    const text = response.draftText || response.text;
    const box = await ensureComposeBox();
    if (!box) {
      toast("Reply box not found. Click Reply, then click the bot button again.");
      return;
    }
    insertReply(box, text);
    toast("Your reply is in the box. Review it, then send.");
  });
}

ensureButton();
setInterval(ensureButton, 2000);
