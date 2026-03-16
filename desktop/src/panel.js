// Standalone panel window UI.
// Renders the team summary in a dedicated Tauri window so the pet window
// never expands into a larger transparent click-blocking region.

const panelContainer = document.getElementById("panel-container");
let panelData = null;

function updatePanel(details) {
  if (!details) return;
  panelData = details;
  renderPanel();
  void resizeWindowToContent();
}

function renderPanel() {
  if (!panelData) {
    panelContainer.innerHTML = '<div class="panel"><div class="panel-header">Loading…</div></div>';
    return;
  }

  const { teamName = "Team", agents = [], actionItems = [], connection } = panelData;
  const connDot = connection?.connected !== false
    ? '<span class="conn-dot conn-ok"></span>'
    : `<span class="conn-dot conn-err" title="${escapeHtml(connection?.message || "")}"></span>`;

  const stateCounts = {};
  for (const agent of agents) {
    const state = agent.state || "idle";
    stateCounts[state] = (stateCounts[state] || 0) + 1;
  }

  const statusParts = [];
  if (stateCounts.working) statusParts.push(`${stateCounts.working} working`);
  if (stateCounts.busy) statusParts.push(`${stateCounts.busy} busy`);
  if (stateCounts.needs_you) statusParts.push(`${stateCounts.needs_you} needs you`);
  if (statusParts.length === 0) statusParts.push("all idle");

  const items = actionItems.slice(0, 3);
  const itemsHtml = items.length > 0
    ? items
      .map((item) =>
        `<div class="panel-item" title="${escapeHtml(item.summary)}">
          <span class="item-agent">${escapeHtml(item.agent)}</span>
          <span class="item-text">${escapeHtml(item.summary)}</span>
        </div>`
      )
      .join("")
    : '<div class="panel-empty">No action items</div>';

  panelContainer.innerHTML = `
    <div class="panel">
      <div class="panel-header">
        <span class="panel-team">${connDot}${escapeHtml(teamName)}</span>
        <span class="panel-status">${escapeHtml(statusParts.join(" · "))}</span>
      </div>
      <div class="panel-items">${itemsHtml}</div>
      <div class="panel-footer">
        <button class="panel-btn" id="open-cccc-btn">Open CCCC</button>
      </div>
    </div>
  `;

  const btn = document.getElementById("open-cccc-btn");
  if (btn) {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      void openCCCCWeb();
    });
  }
}

async function resizeWindowToContent() {
  if (!window.__TAURI__) return;
  try {
    const { invoke } = window.__TAURI__.core;
    await nextFrame();
    const rect = panelContainer.getBoundingClientRect();
    const width = Math.max(180, Math.ceil(rect.width));
    const height = Math.max(96, Math.ceil(rect.height));
    await invoke("resize_panel_window", { width, height });
  } catch (err) {
    console.warn("Failed to resize panel window:", err);
  }
}

async function openCCCCWeb() {
  if (!window.__TAURI__) return;
  try {
    const { invoke } = window.__TAURI__.core;
    await invoke("open_cccc_web");
  } catch (err) {
    console.warn("Failed to open CCCC Web:", err);
  }
}

function nextFrame() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => resolve());
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

async function initPanelWindow() {
  renderPanel();

  if (!window.__TAURI__) {
    return;
  }

  const win = window.__TAURI__.window.getCurrentWindow();
  const myLabel = win.label;
  const { listen } = window.__TAURI__.event;
  await listen("panel-data", (event) => {
    // Only process events meant for this panel window
    if (event.payload?.windowLabel && event.payload.windowLabel !== myLabel) {
      return;
    }
    updatePanel(event.payload);
  });

  try {
    const { invoke } = window.__TAURI__.core;
    const initialPanelData = await invoke("get_panel_data");
    if (initialPanelData) {
      updatePanel(initialPanelData);
    }
  } catch (err) {
    console.warn("Failed to fetch initial panel data:", err);
  }

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") {
      return;
    }
    const { invoke } = window.__TAURI__.core;
    void invoke("toggle_panel_window");
  });
}

void initPanelWindow();
