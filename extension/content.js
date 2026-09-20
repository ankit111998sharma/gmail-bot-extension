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
  }, 4000);
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

function onClick(event) {
  event.preventDefault();
  event.stopPropagation();
  const btn = event.currentTarget;
  const email = readOpenEmail();
  if (!email.subject && !email.sender) {
    toast("Open an email first, then click the bot button.");
    return;
  }
  btn.disabled = true;
  toast("Creating draft…");
  chrome.runtime.sendMessage({ action: "draftOpen", payload: email }, (response) => {
    btn.disabled = false;
    if (chrome.runtime.lastError) {
      toast(chrome.runtime.lastError.message);
      return;
    }
    if (response && response.ok) {
      toast("Draft created. Review it in Gmail before sending.");
      return;
    }
    toast((response && response.error) || "Could not create a draft.");
  });
}

ensureButton();
setInterval(ensureButton, 2000);
