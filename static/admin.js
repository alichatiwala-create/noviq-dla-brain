const statusMsg = document.getElementById("status-msg");
const logBox = document.getElementById("log-box");
const liveDot = document.getElementById("log-live-dot");
let pollTimer = null;

function setStatus(text, kind) {
  statusMsg.textContent = text;
  statusMsg.className = kind || "";
}

function escapeHtml(s) {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

async function refreshLog() {
  try {
    const res = await fetch("/api/admin/log?lines=300");
    const data = await res.json();
    if (data.error) {
      logBox.textContent = "Error loading log: " + data.error;
      return;
    }
    logBox.innerHTML = data.lines.map(line => {
      let cls = "";
      if (line.includes(" WARNING ")) cls = "log-line-WARNING";
      else if (line.includes(" ERROR ")) cls = "log-line-ERROR";
      return `<span class="${cls}">${escapeHtml(line)}</span>`;
    }).join("\n");
    logBox.scrollTop = logBox.scrollHeight;
  } catch (e) {
    logBox.textContent = "Couldn't reach the server: " + e;
  }
}

async function pollStatus() {
  try {
    const res = await fetch("/api/admin/status");
    const data = await res.json();
    if (data.running) {
      liveDot.style.display = "inline";
      setStatus("A pipeline run is in progress...", "busy");
      refreshLog();
    } else {
      liveDot.style.display = "none";
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
        setStatus("Run finished - see the log below.", "ok");
        refreshLog();
      }
    }
  } catch (e) {
    // ignore transient errors while polling
  }
}

function startPolling() {
  if (pollTimer) return;
  pollTimer = setInterval(pollStatus, 3000);
}

async function triggerRun(dateStr) {
  setStatus("Starting...", "busy");
  try {
    const res = await fetch("/api/admin/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(dateStr ? { date: dateStr } : {}),
    });
    const data = await res.json();
    if (data.error) {
      setStatus(data.error, "err");
      return;
    }
    setStatus(data.message, "busy");
    startPolling();
  } catch (e) {
    setStatus("Couldn't reach the server: " + e, "err");
  }
}

document.getElementById("btn-run-tonight").addEventListener("click", () => triggerRun(null));
document.getElementById("btn-run-date").addEventListener("click", () => {
  const val = document.getElementById("date-input").value;
  if (!val) {
    setStatus("Pick a date first.", "err");
    return;
  }
  triggerRun(val);
});
document.getElementById("btn-refresh-log").addEventListener("click", refreshLog);

refreshLog();
pollStatus();
