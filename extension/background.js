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

async function draftFromPopup(request) {
  const tab = await findGmailTab();
  if (!tab) {
    return { ok: false, error: "Open Gmail in Chrome first, then use this extension." };
  }
  try {
    const response = await chrome.tabs.sendMessage(tab.id, {
      action: "runDraft",
      redraft: Boolean(request.redraft),
      rulesUrl: request.rulesUrl || "",
      notes: request.notes || "",
    });
    return response || { ok: false, error: "Refresh the Gmail tab, then try again." };
  } catch (_error) {
    return { ok: false, error: "Refresh the Gmail tab, then try again." };
  }
}
