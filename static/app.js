const state = {
  tab: "CURRENT",
  page: 1,
  pageSize: 50,
  sortBy: "return_by_date",
  sortDir: "asc",
  filters: {
    amsc: [], set_aside: [], basket_id: "", search: "", quoted: false,
    nsn: "", cage: "",
    est_value_min: "", est_value_max: "",
    return_by_from: "", return_by_to: "",
    delivery_days_min: "", delivery_days_max: "",
    mcrl_count_min: "",
    last_award_from: "", last_award_to: "",
    last_unit_price_min: "", last_unit_price_max: "",
  },
  total: 0,
  baskets: [],
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

function fmtMoney(v) {
  if (v === null || v === undefined) return null;
  return "$" + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Dates come back from the API as YYYY-MM-DD (or an ISO timestamp). The whole
// team wants MM/DD/YYYY everywhere, so this is the one place that formats dates.
function fmtDate(v) {
  if (!v) return "—";
  const datePart = String(v).split("T")[0];
  const parts = datePart.split("-");
  if (parts.length !== 3) return datePart;
  const [y, m, d] = parts;
  return `${m}/${d}/${y}`;
}

function daysRemainingBadge(days) {
  if (days === null || days === undefined) return `<span class="value-na">—</span>`;
  const n = Number(days);
  if (n < 0) return `<span class="badge badge-overdue">${Math.abs(n)}d overdue</span>`;
  if (n <= 3) return `<span class="badge badge-duesoon">${n}d left</span>`;
  return `<span class="badge badge-daysleft">${n}d left</span>`;
}

function statusBadge(dateStatus) {
  const map = {
    CURRENT: ["badge-current", "Current"],
    FUTURE: ["badge-future", "Future"],
    OLD_POSTED: ["badge-old", "Old"],
    OLD_EXTENDED: ["badge-old", "Extended"],
  };
  const [cls, label] = map[dateStatus] || ["badge-old", dateStatus || "—"];
  return `<span class="badge ${cls}">${label}</span>`;
}

function yesNoBadge(val) {
  return val
    ? `<span class="badge badge-yes">Yes</span>`
    : `<span class="badge badge-no">No</span>`;
}

async function api(path, options) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function showToast(msg) {
  const toast = $("#toast");
  toast.textContent = msg;
  toast.classList.remove("hidden");
  setTimeout(() => toast.classList.add("hidden"), 2500);
}

// ---------------------------------------------------------------------------
// Meta / baskets
// ---------------------------------------------------------------------------

async function loadMeta() {
  try {
    const meta = await api("/api/meta");
    $("#lastUpdated").textContent = meta.latest_post_date
      ? `Last updated: ${meta.latest_post_date}`
      : "Last updated: —";
    for (const tab of ["CURRENT", "FUTURE", "OLD"]) {
      const n = meta.tab_counts?.[tab];
      $(`#count-${tab}`).textContent = n !== undefined ? `(${n})` : "";
    }
  } catch (e) {
    $("#lastUpdated").textContent = "Last updated: (error loading)";
  }
}

async function loadBaskets() {
  try {
    state.baskets = await api("/api/baskets");
    const sel = $("#basketFilter");
    sel.innerHTML = `<option value="">All (no basket filter)</option>` +
      state.baskets.map((b) => `<option value="${b.id}">${b.name} (${b.item_count})</option>`).join("");
  } catch (e) {
    console.error(e);
  }
}

// ---------------------------------------------------------------------------
// Table
// ---------------------------------------------------------------------------

function rowHtml(r, idx) {
  const est = r.estimated_value !== null
    ? fmtMoney(r.estimated_value)
    : `<span class="value-na">N/A</span>`;
  const lastPrice = r.last_award_price !== null ? fmtMoney(r.last_award_price) : "—";
  const lastAward = r.last_award_date ? fmtDate(r.last_award_date) : "—";
  const inAnyBasket = r.basket_ids && r.basket_ids.length > 0;

  return `
    <tr data-id="${r.id}">
      <td><input type="checkbox" class="row-check" data-id="${r.id}" ${inAnyBasket ? "checked" : ""}></td>
      <td>${idx}</td>
      <td>${r.rfq_number || "—"}</td>
      <td>${r.solicitation_number}</td>
      <td>${r.nsn}</td>
      <td>${r.description || "—"}</td>
      <td>${r.quantity ?? "—"}</td>
      <td>${r.amsc || "—"}</td>
      <td>${r.set_aside_label || "—"}</td>
      <td>${fmtDate(r.return_by_date)}</td>
      <td>${daysRemainingBadge(r.days_remaining)}</td>
      <td>${lastAward}</td>
      <td>${lastPrice}</td>
      <td>${est}</td>
      <td>${r.mcrl_count}</td>
      <td>${r.hist_vendor_count}</td>
      <td>${yesNoBadge(r.quoted)}</td>
      <td>${statusBadge(r.date_status)}</td>
    </tr>
  `;
}

async function loadRfqs() {
  const tbody = $("#rfqTableBody");
  tbody.innerHTML = `<tr><td colspan="17" class="empty-row">Loading…</td></tr>`;

  const params = new URLSearchParams({
    tab: state.tab,
    sort_by: state.sortBy,
    sort_dir: state.sortDir,
    page: state.page,
    page_size: state.pageSize,
  });
  (state.filters.amsc || []).forEach((v) => params.append("amsc", v));
  (state.filters.set_aside || []).forEach((v) => params.append("set_aside", v));
  if (state.filters.basket_id) params.set("basket_id", state.filters.basket_id);
  if (state.filters.search) params.set("search", state.filters.search);
  if (state.filters.quoted) params.set("quoted", "true");
  if (state.filters.nsn) params.set("nsn", state.filters.nsn);
  if (state.filters.cage) params.append("cage", state.filters.cage);
  if (state.filters.est_value_min !== "") params.set("est_value_min", state.filters.est_value_min);
  if (state.filters.est_value_max !== "") params.set("est_value_max", state.filters.est_value_max);
  if (state.filters.return_by_from) params.set("return_by_from", state.filters.return_by_from);
  if (state.filters.return_by_to) params.set("return_by_to", state.filters.return_by_to);
  if (state.filters.delivery_days_min !== "") params.set("delivery_days_min", state.filters.delivery_days_min);
  if (state.filters.delivery_days_max !== "") params.set("delivery_days_max", state.filters.delivery_days_max);
  if (state.filters.mcrl_count_min !== "") params.set("mcrl_count_min", state.filters.mcrl_count_min);
  if (state.filters.last_award_from) params.set("last_award_from", state.filters.last_award_from);
  if (state.filters.last_award_to) params.set("last_award_to", state.filters.last_award_to);
  if (state.filters.last_unit_price_min !== "") params.set("last_unit_price_min", state.filters.last_unit_price_min);
  if (state.filters.last_unit_price_max !== "") params.set("last_unit_price_max", state.filters.last_unit_price_max);

  try {
    const data = await api(`/api/rfqs?${params.toString()}`);
    state.total = data.total;
    if (data.rows.length === 0) {
      tbody.innerHTML = `<tr><td colspan="17" class="empty-row">No RFQs match these filters.</td></tr>`;
    } else {
      const startIdx = (state.page - 1) * state.pageSize + 1;
      tbody.innerHTML = data.rows.map((r, i) => rowHtml(r, startIdx + i)).join("");
    }
    const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
    $("#pageInfo").textContent = `Page ${state.page} of ${totalPages} (${state.total} total)`;

    $$(".row-check").forEach((cb) => {
      cb.addEventListener("click", (e) => e.stopPropagation());
      cb.addEventListener("change", onRowCheckToggle);
    });
    $$("#rfqTableBody tr[data-id]").forEach((tr) => {
      tr.addEventListener("click", () => openDetail(tr.dataset.id));
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="17" class="empty-row">Error loading RFQs: ${e.message}</td></tr>`;
  }
}

async function onRowCheckToggle(e) {
  const lineId = e.target.dataset.id;
  const checked = e.target.checked;
  const basketId = state.filters.basket_id || (state.baskets[0] && state.baskets[0].id);
  if (!basketId) {
    showToast("Create a basket first (use the Basket dropdown).");
    e.target.checked = !checked;
    return;
  }
  try {
    if (checked) {
      await api("/api/basket-items", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ line_id: Number(lineId), basket_id: Number(basketId) }),
      });
      showToast("Added to basket");
    } else {
      await api("/api/basket-items", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ line_id: Number(lineId), basket_id: Number(basketId) }),
      });
      showToast("Removed from basket");
    }
    loadBaskets();
  } catch (err) {
    showToast("Error: " + err.message);
  }
}

// ---------------------------------------------------------------------------
// Detail panel
// ---------------------------------------------------------------------------

function mcrlRowsHtml(sources) {
  if (!sources || sources.length === 0) {
    return `<div class="basis-note">No qualified sources on file for this NSN yet.</div>`;
  }
  const rows = sources.slice(0, 8).map((s) => `
    <tr>
      <td>${s.cage_code || "—"}</td>
      <td>${s.part_number || "—"}</td>
      <td>${s.company_name || "—"}</td>
      <td>${s.approved ? yesNoBadge(true) : yesNoBadge(false)}</td>
    </tr>
  `).join("");
  const more = sources.length > 8 ? `<div class="basis-note">+ ${sources.length - 8} more not shown.</div>` : "";
  return `
    <table class="mini-table">
      <thead><tr><th>CAGE</th><th>Part #</th><th>Company</th><th>Approved</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    ${more}
  `;
}

function historyStatsHtml(stats) {
  if (!stats || !stats.award_count) {
    return `<div class="basis-note">No award history on file for this NSN yet — this fills in automatically once ContractHist / Current Awards are imported.</div>`;
  }
  return `
    <div class="kv-grid" style="margin-bottom:10px;">
      <div class="kv"><label>Total Historical Awards</label><span>${stats.award_count}</span></div>
      <div class="kv"><label>Distinct Vendors Won</label><span>${stats.distinct_vendor_count}</span></div>
      <div class="kv"><label>Lowest Unit Price</label><span>${stats.lowest_unit_price !== null ? fmtMoney(stats.lowest_unit_price) : "—"}</span></div>
      <div class="kv"><label>Highest Unit Price</label><span>${stats.highest_unit_price !== null ? fmtMoney(stats.highest_unit_price) : "—"}</span></div>
      <div class="kv"><label>Average Unit Price</label><span>${stats.average_unit_price !== null ? fmtMoney(stats.average_unit_price) : "—"}</span></div>
    </div>
  `;
}

function historyRowsHtml(hist) {
  if (!hist || hist.length === 0) {
    return `<div class="basis-note">No award history on file for this NSN yet.</div>`;
  }
  const rows = hist.map((h) => `
    <tr>
      <td>${fmtDate(h.award_date)}</td>
      <td>${h.contract_number || "—"}</td>
      <td>${h.award_number || "—"}</td>
      <td>${h.winning_vendor_name || "—"}</td>
      <td>${h.winning_cage || "—"}</td>
      <td>${h.quantity ?? "—"}</td>
      <td>${h.unit_price !== null ? fmtMoney(h.unit_price) : "—"}</td>
      <td>${h.total_award_value !== null ? fmtMoney(h.total_award_value) : "—"}</td>
      <td>${h.source_type || h.source_year || h.source_file || "—"}</td>
    </tr>
  `).join("");
  return `
    <div class="scroll-table">
      <table class="mini-table">
        <thead><tr>
          <th>Award Date</th><th>Contract #</th><th>Award #</th><th>Vendor</th><th>CAGE</th>
          <th>Qty</th><th>Unit $</th><th>Total $</th><th>Source</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    <div class="basis-note">${hist.length} historical award${hist.length === 1 ? "" : "s"}, most recent first — scroll to see older ones.</div>
  `;
}

function quoteHistoryHtml(quotes) {
  if (!quotes || quotes.length === 0) {
    return `<div class="basis-note">No quotes recorded for this NSN yet.</div>`;
  }
  const rows = quotes.map((q) => `
    <tr>
      <td>${fmtDate(q.quoted_date)}</td>
      <td>${q.quoted_by || "—"}</td>
      <td>${q.quoted_price !== null ? fmtMoney(q.quoted_price) : "—"}</td>
    </tr>
  `).join("");
  return `
    <table class="mini-table">
      <thead><tr><th>Date</th><th>By</th><th>Price</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function notesHtml(notes) {
  if (!notes || notes.length === 0) return `<div class="basis-note">No notes yet.</div>`;
  return `<div class="notes-list">${notes.map((n) => `
    <div class="note-item">
      <div class="note-date">${n.created_at}</div>
      <div>${n.note}</div>
    </div>
  `).join("")}</div>`;
}

function basketOptionsHtml(currentBasketIds) {
  return state.baskets.map((b) => {
    const inThis = (currentBasketIds || []).includes(b.id);
    return `<option value="${b.id}" ${inThis ? "selected" : ""}>${b.name}</option>`;
  }).join("");
}

function detailHtml(r) {
  const currentBasketIds = (r.baskets || []).map((b) => b.id);
  return `
    <div class="card">
      <h3>Solicitation Details</h3>
      <div class="kv-grid">
        <div class="kv"><label>Solicitation #</label><span>${r.solicitation_number}</span></div>
        <div class="kv"><label>RFQ / PR #</label><span>${r.rfq_number || "—"}</span></div>
        <div class="kv"><label>NSN</label><span>${r.nsn}</span></div>
        <div class="kv"><label>FSC / NIIN</label><span>${r.fsc || "—"} / ${r.niin || "—"}</span></div>
        <div class="kv"><label>Description</label><span>${r.description || "—"}</span></div>
        <div class="kv"><label>Qty / Unit</label><span>${r.quantity ?? "—"} ${r.unit_of_issue || ""}</span></div>
        <div class="kv"><label>Issue Date</label><span>${fmtDate(r.issue_date)}</span></div>
        <div class="kv"><label>Return By / Bid Closing</label><span>${fmtDate(r.return_by_date)}</span></div>
        <div class="kv"><label>Days Remaining</label><span>${daysRemainingBadge(r.days_remaining)}</span></div>
        <div class="kv"><label>Delivery Days</label><span>${r.delivery_days ?? "—"}</span></div>
        <div class="kv"><label>AMSC</label><span>${r.amsc || "—"}</span></div>
        <div class="kv"><label>Set Aside</label><span>${r.set_aside_label || "—"}${r.set_aside_pct ? " (" + r.set_aside_pct + "%)" : ""}</span></div>
        <div class="kv"><label>Inspection</label><span class="value-na">— (needs DIBBS detail-page scrape)</span></div>
        <div class="kv"><label>Packaging</label><span class="value-na">— (needs DIBBS detail-page scrape)</span></div>
        <div class="kv"><label>Status</label><span>${statusBadge(r.date_status)}</span></div>
        <div class="kv"><label>Times Posted</label><span>${r.times_posted ?? "—"}</span></div>
      </div>
    </div>

    <div class="card">
      <h3>Pricing &amp; Estimated Value</h3>
      <div class="kv-grid">
        <div class="kv"><label>Last Award Date</label><span>${r.last_award_date ? fmtDate(r.last_award_date) : "—"}</span></div>
        <div class="kv"><label>Last Award Unit Price</label><span>${r.last_award_price !== null ? fmtMoney(r.last_award_price) : "—"}</span></div>
        <div class="kv"><label>Last Award Vendor</label><span>${r.last_award_vendor_name || r.last_award_cage || "—"}</span></div>
        <div class="kv"><label>Management Price (DRN 7075)</label><span>${r.management_price !== null ? fmtMoney(r.management_price) : "—"}</span></div>
        <div class="kv"><label>Estimated Value</label><span>${r.estimated_value !== null ? fmtMoney(r.estimated_value) : "N/A"}</span></div>
        <div class="kv"><label>Our Last Quote</label><span>${r.our_last_quote_price !== null && r.our_last_quote_price !== undefined ? fmtMoney(r.our_last_quote_price) + " on " + fmtDate(r.our_last_quote_date) : "Not quoted before"}</span></div>
      </div>
      <div class="basis-note">Basis: ${r.estimated_value_basis}</div>
    </div>

    <div class="card" id="calcCard">
      <h3>Markup / Margin Calculator <span class="basis-note" style="display:inline;">(scratch pad — not saved)</span></h3>
      <div class="kv-grid" style="margin-bottom:10px;">
        <div class="kv">
          <label>Your Unit Cost ($)</label>
          <input type="number" step="0.01" id="calcCost" value="${(r.last_award_price ?? r.management_price ?? "")}">
        </div>
        <div class="kv">
          <label>Markup %</label>
          <input type="number" step="0.1" id="calcMarkupPct" value="0">
        </div>
        <div class="kv">
          <label>Your Quote Price ($/unit)</label>
          <input type="number" step="0.01" id="calcQuotePrice" placeholder="or enter this instead">
        </div>
        <div class="kv">
          <label>Qty</label>
          <input type="number" step="1" id="calcQty" value="${r.quantity ?? 1}">
        </div>
      </div>
      <div class="kv-grid" style="margin-bottom:6px;">
        <div class="kv"><label>Per-Unit Cost</label><span id="calcUnitCostOut">—</span></div>
        <div class="kv"><label>Per-Unit Sell Price</label><span id="calcSuggestedPrice">—</span></div>
        <div class="kv"><label>Margin %</label><span id="calcMarginPct">—</span></div>
        <div class="kv"><label>Profit / Unit</label><span id="calcProfitUnit">—</span></div>
      </div>
      <div class="kv-grid">
        <div class="kv"><label>Total RFQ Cost (x Qty)</label><span id="calcTotalCost">—</span></div>
        <div class="kv"><label>Total RFQ Sell Value (x Qty)</label><span id="calcTotalSell">—</span></div>
        <div class="kv"><label>Total Expected Profit (x Qty)</label><span id="calcProfitTotal">—</span></div>
      </div>
      <div class="basis-note">Markup % defaults to 0. Enter Unit Cost + Markup % for a suggested price, or Unit Cost + your own Quote Price for real margin/profit. Qty defaults to this RFQ's quantity but you can override it. Nothing here is saved — it resets when you close this panel.</div>
    </div>

    <div class="card">
      <h3>Record a Quote</h3>
      <div class="kv-grid" style="margin-bottom:10px;">
        <div class="kv"><label>Your Name</label><input type="text" id="quoteBy" placeholder="who is quoting"></div>
        <div class="kv"><label>Quote Price ($/unit)</label><input type="number" step="0.01" id="quotePrice"></div>
        <div class="kv"><label>Quote Date</label><input type="date" id="quoteDate"></div>
      </div>
      <button class="btn btn-primary btn-small" id="saveQuoteBtn" data-id="${r.id}" data-nsn="${r.nsn}">Save Quote</button>
      <div class="basis-note" style="margin-top:10px;">Previous quotes for this NSN (shared across the team):</div>
      ${quoteHistoryHtml(r.quote_history)}
    </div>

    <div class="card">
      <h3>Flags</h3>
      <div class="kv-grid">
        <div class="kv"><label>Previously Quoted</label><span>${yesNoBadge(r.quoted)}</span></div>
        <div class="kv"><label>In Basket(s)</label><span>${(r.baskets || []).map((b) => b.name).join(", ") || "None"}</span></div>
      </div>
    </div>

    <div class="card">
      <h3>MCRL / Qualified Sources</h3>
      ${mcrlRowsHtml(r.mcrl_sources)}
    </div>

    <div class="card">
      <h3>Award &amp; Vendor History</h3>
      ${historyStatsHtml(r.history_stats)}
      ${historyRowsHtml(r.procurement_history)}
    </div>

    <div class="card">
      <h3>Additional Information</h3>
      <div class="kv-grid">
        <div class="kv"><label>Price Reason Code</label><span class="value-na">—</span></div>
        <div class="kv"><label>AAC</label><span class="value-na">—</span></div>
        <div class="kv"><label>SOS</label><span class="value-na">—</span></div>
        <div class="kv"><label>SOSM</label><span class="value-na">—</span></div>
        <div class="kv"><label>UI</label><span class="value-na">—</span></div>
        <div class="kv"><label>SLC</label><span class="value-na">—</span></div>
        <div class="kv"><label>CIIC</label><span class="value-na">—</span></div>
        <div class="kv"><label>Management Control</label><span class="value-na">—</span></div>
        <div class="kv"><label>Technical Document</label><span class="value-na">—</span></div>
      </div>
      <div class="basis-note">These fields come from the DIBBS RFQ detail page directly and aren't part of the IN/AS/BQ import pipeline yet, so they show blank.</div>
    </div>

    <div class="card">
      <h3>Actions</h3>
      <div class="actions-row" style="margin-bottom:12px;">
        <button class="btn btn-primary" id="toggleQuotedBtn" data-id="${r.id}" data-quoted="${r.quoted}">
          ${r.quoted ? "Mark Not Quoted" : "Mark Quoted"}
        </button>
        <select id="basketSelect" data-id="${r.id}">
          <option value="">Add to basket…</option>
          ${basketOptionsHtml(currentBasketIds)}
        </select>
      </div>
      <textarea id="noteInput" placeholder="Add a note about this RFQ..."></textarea>
      <div style="margin-top:8px;">
        <button class="btn btn-secondary" id="addNoteBtn" data-id="${r.id}">Add Note</button>
      </div>
      ${notesHtml(r.notes)}
    </div>
  `;
}

function wireCalculator() {
  const costEl = $("#calcCost");
  const markupEl = $("#calcMarkupPct");
  const quoteEl = $("#calcQuotePrice");
  const qtyEl = $("#calcQty");
  if (!costEl) return; // calculator card not present for some reason

  function recalc() {
    const cost = parseFloat(costEl.value);
    const markupPct = parseFloat(markupEl.value);
    const quotePrice = parseFloat(quoteEl.value);
    const qty = parseFloat(qtyEl.value) || 0;

    $("#calcUnitCostOut").textContent = !isNaN(cost) ? fmtMoney(cost) : "—";
    $("#calcTotalCost").textContent = !isNaN(cost) ? fmtMoney(cost * qty) : "—";

    // Suggested sell price from cost + markup %
    const suggested = (!isNaN(cost) && !isNaN(markupPct)) ? cost * (1 + markupPct / 100) : NaN;
    $("#calcSuggestedPrice").textContent = !isNaN(suggested) ? fmtMoney(suggested) : "—";

    // Margin % and profit from cost + quote price (what you're actually charging),
    // falling back to the suggested price when no quote price is entered yet.
    const effectivePrice = !isNaN(quotePrice) ? quotePrice : suggested;

    if (!isNaN(cost) && !isNaN(effectivePrice) && effectivePrice > 0) {
      const marginPct = ((effectivePrice - cost) / effectivePrice) * 100;
      const profitUnit = effectivePrice - cost;
      $("#calcMarginPct").textContent = marginPct.toFixed(1) + "%";
      $("#calcProfitUnit").textContent = fmtMoney(profitUnit);
      $("#calcTotalSell").textContent = fmtMoney(effectivePrice * qty);
      $("#calcProfitTotal").textContent = fmtMoney(profitUnit * qty);
    } else {
      $("#calcMarginPct").textContent = "—";
      $("#calcProfitUnit").textContent = "—";
      $("#calcTotalSell").textContent = "—";
      $("#calcProfitTotal").textContent = "—";
    }
  }

  [costEl, markupEl, quoteEl, qtyEl].forEach((el) => el.addEventListener("input", recalc));
  recalc();
}

async function openDetail(lineId) {
  const overlay = $("#detailOverlay");
  const content = $("#detailContent");
  overlay.classList.remove("hidden");
  content.innerHTML = "Loading…";
  try {
    const r = await api(`/api/rfqs/${lineId}`);
    content.innerHTML = detailHtml(r);

    wireCalculator();

    $("#toggleQuotedBtn").addEventListener("click", async (e) => {
      const id = e.target.dataset.id;
      const currentlyQuoted = e.target.dataset.quoted === "true";
      try {
        await api(`/api/rfqs/${id}/status`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ quoted: !currentlyQuoted }),
        });
        showToast("Updated");
        openDetail(id);
        loadRfqs();
      } catch (err) {
        showToast("Error: " + err.message);
      }
    });

    $("#addNoteBtn").addEventListener("click", async (e) => {
      const id = e.target.dataset.id;
      const note = $("#noteInput").value.trim();
      if (!note) return;
      try {
        await api(`/api/rfqs/${id}/notes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note }),
        });
        showToast("Note added");
        openDetail(id);
      } catch (err) {
        showToast("Error: " + err.message);
      }
    });

    $("#basketSelect").addEventListener("change", async (e) => {
      const basketId = e.target.value;
      const id = e.target.dataset.id;
      if (!basketId) return;
      try {
        await api("/api/basket-items", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ line_id: Number(id), basket_id: Number(basketId) }),
        });
        showToast("Added to basket");
        openDetail(id);
        loadBaskets();
      } catch (err) {
        showToast("Error: " + err.message);
      }
    });

    $("#saveQuoteBtn").addEventListener("click", async (e) => {
      const id = e.target.dataset.id;
      const nsn = e.target.dataset.nsn;
      const quotedBy = $("#quoteBy").value.trim();
      const quotedPrice = $("#quotePrice").value;
      const quotedDate = $("#quoteDate").value;
      if (!quotedPrice) {
        showToast("Enter a quote price first");
        return;
      }
      try {
        await api(`/api/rfqs/${id}/quotes`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            nsn, quoted_by: quotedBy || null,
            quoted_price: Number(quotedPrice),
            quoted_date: quotedDate || null,
          }),
        });
        showToast("Quote saved");
        openDetail(id);
        loadRfqs();
      } catch (err) {
        showToast("Error: " + err.message);
      }
    });
  } catch (e) {
    content.innerHTML = `<div class="card">Error loading detail: ${e.message}</div>`;
  }
}

// ---------------------------------------------------------------------------
// Event wiring
// ---------------------------------------------------------------------------

function wireEvents() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      $$(".tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      state.tab = tab.dataset.tab;
      state.page = 1;
      loadRfqs();
    });
  });

  $$("#rfqTable th").forEach((th, idx) => {
    // columns after the checkbox/# are sortable via the Sort By dropdown already;
    // clicking a header just flips direction on the currently selected sort field
    th.addEventListener("click", () => {
      if (idx < 2) return;
      state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      loadRfqs();
    });
  });

  $("#moreFiltersBtn").addEventListener("click", () => {
    const bar = $("#moreFiltersBar");
    const isHidden = bar.style.display === "none";
    bar.style.display = isHidden ? "flex" : "none";
    $("#moreFiltersBtn").innerHTML = isHidden ? "More filters &#9652;" : "More filters &#9662;";
  });

  function selectedValues(sel) {
    return Array.from(sel.selectedOptions).map((o) => o.value);
  }

  $("#applyBtn").addEventListener("click", () => {
    state.filters.amsc = selectedValues($("#amscFilter"));
    state.filters.set_aside = selectedValues($("#setAsideFilter"));
    state.filters.basket_id = $("#basketFilter").value;
    state.filters.search = $("#searchInput").value.trim();
    state.filters.quoted = $("#quotedFilter").checked;
    state.filters.nsn = $("#nsnFilter").value.trim();
    state.filters.cage = $("#cageFilter").value.trim();
    state.filters.est_value_min = $("#estValueMin").value;
    state.filters.est_value_max = $("#estValueMax").value;
    state.filters.return_by_from = $("#returnByFrom").value;
    state.filters.return_by_to = $("#returnByTo").value;
    state.filters.delivery_days_min = $("#deliveryDaysMin").value;
    state.filters.delivery_days_max = $("#deliveryDaysMax").value;
    state.filters.mcrl_count_min = $("#mcrlMin").value;
    state.filters.last_award_from = $("#lastAwardFrom").value;
    state.filters.last_award_to = $("#lastAwardTo").value;
    state.filters.last_unit_price_min = $("#lastPriceMin").value;
    state.filters.last_unit_price_max = $("#lastPriceMax").value;
    state.sortBy = $("#sortBy").value;
    state.page = 1;
    loadRfqs();
  });

  $("#clearBtn").addEventListener("click", () => {
    $("#amscFilter").selectedIndex = -1;
    $("#setAsideFilter").selectedIndex = -1;
    $("#basketFilter").value = "";
    $("#searchInput").value = "";
    $("#quotedFilter").checked = false;
    $("#nsnFilter").value = "";
    $("#cageFilter").value = "";
    ["estValueMin", "estValueMax", "returnByFrom", "returnByTo", "deliveryDaysMin",
     "deliveryDaysMax", "mcrlMin", "lastAwardFrom", "lastAwardTo", "lastPriceMin", "lastPriceMax"]
      .forEach((id) => { $("#" + id).value = ""; });
    $("#sortBy").value = "return_by_date";
    state.filters = {
      amsc: [], set_aside: [], basket_id: "", search: "", quoted: false,
      nsn: "", cage: "",
      est_value_min: "", est_value_max: "",
      return_by_from: "", return_by_to: "",
      delivery_days_min: "", delivery_days_max: "",
      mcrl_count_min: "",
      last_award_from: "", last_award_to: "",
      last_unit_price_min: "", last_unit_price_max: "",
    };
    state.sortBy = "return_by_date";
    state.page = 1;
    loadRfqs();
  });

  $("#searchBtn").addEventListener("click", () => $("#applyBtn").click());
  $("#searchInput").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#applyBtn").click();
  });

  $("#prevPage").addEventListener("click", () => {
    if (state.page > 1) {
      state.page -= 1;
      loadRfqs();
    }
  });
  $("#nextPage").addEventListener("click", () => {
    const totalPages = Math.max(1, Math.ceil(state.total / state.pageSize));
    if (state.page < totalPages) {
      state.page += 1;
      loadRfqs();
    }
  });

  $("#closeDetail").addEventListener("click", () => $("#detailOverlay").classList.add("hidden"));
  $("#detailOverlay").addEventListener("click", (e) => {
    if (e.target.id === "detailOverlay") $("#detailOverlay").classList.add("hidden");
  });
}

async function init() {
  wireEvents();
  await Promise.all([loadMeta(), loadBaskets()]);
  await loadRfqs();
}

init();
