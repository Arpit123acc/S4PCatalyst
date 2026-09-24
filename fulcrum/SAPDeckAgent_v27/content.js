// SAP Deck Agent — Content Script (lightweight)
// Just handles ping and forwards extract requests to background worker

chrome.runtime.onMessage.addListener((msg, sender, respond) => {
  if (msg.action === "ping") {
    const url  = window.location.href;
    const pidM = url.match(/\/SolP\/([A-Z0-9]+)/i);
    respond({
      ok:        true,
      pid:       pidM ? pidM[1].toUpperCase() : "—",
      name:      document.title.replace(/\s*[-|].*/,"").trim(),
      bodyChars: (document.body.innerText || "").length,
    });
    return true;
  }
});

