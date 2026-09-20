const API_PORTS = [8787, 8788, 8789];

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.action === "health") {
    fetchHealth()
      .then(sendResponse)
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }
  if (request.action === "draftFromPopup") {
    draftFromPopup(request)
      .then(sendResponse)
      .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
    return true;
  }
  if (request.action !== "draftOpen") {
    return;
  }
  draftOpen(request.payload)
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
  return true;
});

async function fetchHealth() {
  let lastError = "Local app is not running. Start run.bat first.";
  for (const port of API_PORTS) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/health`);
      const data = await response.json();
      if (data && data.ok) {
        return data;
      }
      lastError = data.error || lastError;
    } catch (error) {
      lastError = error.message || lastError;
    }
  }
  return { ok: false, error: lastError };
}

async function draftOpen(payload) {
  let lastError = "Local app is not running. Start run.bat first.";
  for (const port of API_PORTS) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/api/draft-open`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json();
      if (data && data.ok) {
        return data;
      }
      lastError = data.error || lastError;
      if (response.status !== 404) {
        return { ok: false, error: lastError };
      }
    } catch (error) {
      lastError = error.message || lastError;
    }
  }
  throw new Error(lastError);
}

async function findGmailTab() {
  const [active] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (active && /mail\.google\.com/i.test(active.url || "")) {
    return active;
  }
  const tabs = await chrome.tabs.query({ url: ["https://mail.google.com/*"] });
  return tabs.find((tab) => tab.active) || tabs[0] || null;
}

async function sendTabMessage(tabId, message) {
  try {
    return await chrome.tabs.sendMessage(tabId, message);
  } catch (error) {
    return { ok: false, error: error.message || String(error) };
  }
}

async function ensureGmailContent(tabId) {
  const ping = await sendTabMessage(tabId, { action: "ping" });
  if (ping && ping.ok) {
    return true;
  }
  if (!chrome.scripting) {
    return false;
  }
  await chrome.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
  try {
    await chrome.scripting.insertCSS({ target: { tabId }, files: ["content.css"] });
  } catch (_error) {
    /* CSS insert is optional if the content script already loaded styles. */
  }
  const again = await sendTabMessage(tabId, { action: "ping" });
  return Boolean(again && again.ok);
}

async function draftFromPopup(request) {
  const tab = await findGmailTab();
  if (!tab) {
    return { ok: false, error: "Open Gmail in Chrome first, then use this extension." };
  }
  const ready = await ensureGmailContent(tab.id);
  if (!ready) {
    return { ok: false, error: "Refresh the Gmail tab, then try again." };
  }
  const response = await sendTabMessage(tab.id, {
    action: "runDraft",
    redraft: Boolean(request.redraft),
    rulesUrl: request.rulesUrl || "",
    notes: request.notes || "",
    mode: request.mode || "",
    to: request.to || "",
  });
  return response || { ok: false, error: "Refresh the Gmail tab, then try again." };
}
