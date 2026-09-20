const API_PORTS = [8787, 8788, 8789];

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.action !== "draftOpen") {
    return;
  }
  draftOpen(request.payload)
    .then(sendResponse)
    .catch((error) => sendResponse({ ok: false, error: error.message || String(error) }));
  return true;
});

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
