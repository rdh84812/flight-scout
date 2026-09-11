"use strict";

const state = { deals: [], filtered: [], reportDate: "" };
const $ = (id) => document.getElementById(id);

function formatPrice(value, currency = "TWD") {
  if (value === null || value === undefined) return "價格未提供";
  const amount = new Intl.NumberFormat("zh-TW", { maximumFractionDigits: 0 }).format(value);
  return String(currency).toUpperCase() === "TWD" ? `NT$${amount}` : `${currency} ${amount}`;
}

function discountPercent(deal) {
  const match = String(deal.discount_info || "").match(/(\d+(?:\.\d+)?)\s*%/);
  if (match) return Number(match[1]);
  if (deal.original_price && deal.price) return ((deal.original_price - deal.price) / deal.original_price) * 100;
  return 0;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "";
  } catch { return ""; }
}

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined && text !== null) element.textContent = text;
  return element;
}

function statusLabel(status) {
  return { new: "新發現", price_drop: "價格下降", unchanged: "持續追蹤" }[status] || "持續追蹤";
}

function departureTimestamp(value, referenceDate) {
  const text = String(value || "").trim();
  const match = text.match(/^(?:(\d{4})[年/\-])?(\d{1,2})[月/\-](\d{1,2})(?:日)?/);
  if (!match) return null;
  const reference = new Date(`${referenceDate}T00:00:00Z`);
  if (!match[1] && !Number.isFinite(reference.getTime())) return null;
  let year = match[1] ? Number(match[1]) : reference.getUTCFullYear();
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (!match[1] && (month < reference.getUTCMonth() + 1 ||
      (month === reference.getUTCMonth() + 1 && day < reference.getUTCDate()))) year += 1;
  const date = new Date(Date.UTC(year, month - 1, day));
  return date.getUTCMonth() === month - 1 && date.getUTCDate() === day ? date.getTime() : null;
}

function compareDeals(a, b, order, direction, referenceDate) {
  let left, right;
  if (order === "date") {
    left = departureTimestamp(a.outbound_date, referenceDate);
    right = departureTimestamp(b.outbound_date, referenceDate);
  } else if (order === "destination") {
    left = a.destination || null; right = b.destination || null;
  } else if (order === "discount") {
    left = discountPercent(a); right = discountPercent(b);
  } else if (order === "price_drop") {
    left = a.price_drop; right = b.price_drop;
  } else {
    left = a.price; right = b.price;
  }
  // Missing values stay at the bottom in either direction.
  if (left == null || right == null) return left == null ? (right == null ? 0 : 1) : -1;
  const comparison = typeof left === "string" ? left.localeCompare(right, "zh-Hant") : left - right;
  return comparison * (direction === "desc" ? -1 : 1) ||
    String(a.destination || "").localeCompare(String(b.destination || ""), "zh-Hant");
}

function createCard(deal) {
  const card = node("article", "deal-card");
  const top = node("div", "card-top");
  const titleWrap = node("div");
  titleWrap.append(node("h2", "destination", deal.destination || "目的地未提供"));
  titleWrap.append(node("span", "country", deal.country || "地區未提供"));
  top.append(titleWrap, node("span", `status ${deal.status || "unchanged"}`, statusLabel(deal.status)));

  const fareRow = node("div", "fare-row");
  const fare = node("div");
  fare.append(node("strong", "price", formatPrice(deal.price, deal.currency)));
  fare.append(node("div", "original-price", deal.original_price ? formatPrice(deal.original_price, deal.currency) : ""));
  fareRow.append(fare, node("div", "discount", deal.discount_info || (deal.price_drop ? `再降 ${formatPrice(deal.price_drop, deal.currency)}` : "")));

  const route = node("div", "route");
  const dates = [deal.outbound_date, deal.return_date].filter(Boolean).join(" → ") || "日期未提供";
  route.append(node("div", "dates", dates));
  route.append(node("div", "details", [deal.flight_details, deal.flight_number].filter(Boolean).join(" · ") || "航程資訊未提供"));

  const footer = node("div", "card-footer");
  footer.append(node("span", "airline", deal.airline || "航空公司未顯示"));
  const link = node("a", "booking-link", "查看航班 ↗");
  const url = safeUrl(deal.source_url);
  if (url) { link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer"; }
  else { link.href = "#"; link.setAttribute("aria-disabled", "true"); }
  footer.append(link);
  if (deal.price_drop) {
    fare.append(node("div", "price-change", `上次 ${formatPrice(deal.previous_price, deal.currency)} · 降 ${formatPrice(deal.price_drop, deal.currency)}`));
  }
  card.append(top, route, fareRow, footer);
  return card;
}

function render() {
  const container = $("results");
  container.replaceChildren(...state.filtered.map(createCard));
  container.setAttribute("aria-busy", "false");
  $("resultCount").textContent = `顯示 ${state.filtered.length}／${state.deals.length} 筆候選`;
  $("emptyState").hidden = state.filtered.length !== 0;
}

function applyFilters() {
  const query = $("searchInput").value.trim().toLocaleLowerCase("zh-TW");
  const country = $("countryFilter").value;
  const status = $("statusFilter").value;
  const order = $("sortOrder").value;
  state.filtered = state.deals.filter((deal) => {
    const searchable = [deal.destination, deal.country, deal.flight_details, deal.airline, deal.flight_number].join(" ").toLocaleLowerCase("zh-TW");
    return (!query || searchable.includes(query)) && (!country || deal.country === country) && (!status || deal.status === status);
  });
  state.filtered.sort((a, b) => compareDeals(a, b, order, $("sortDirection").value, state.reportDate));
  render();
}

function hydrate(payload) {
  state.deals = Array.isArray(payload.deals) ? payload.deals : [];
  state.reportDate = payload.report_date || String(payload.generated_at || "").slice(0, 10);
  $("generatedAt").textContent = `更新於 ${new Intl.DateTimeFormat("zh-TW", { dateStyle: "medium", timeStyle: "short" }).format(new Date(payload.generated_at))}`;
  $("promptText").textContent = payload.prompt || "";
  $("lowestPrice").textContent = formatPrice(payload.summary?.lowest_price);
  $("totalDeals").textContent = String(payload.summary?.total ?? state.deals.length);
  $("changedDeals").textContent = String(payload.summary?.changed ?? 0);
  $("countryCount").textContent = String(payload.summary?.countries?.length ?? 0);
  const countries = [...new Set(state.deals.map((deal) => deal.country).filter(Boolean))].sort((a, b) => a.localeCompare(b, "zh-Hant"));
  $("countryFilter").append(...countries.map((country) => { const option = node("option", "", country); option.value = country; return option; }));
  applyFilters();
}

async function loadReport() {
  try {
    const response = await fetch("data/latest.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    hydrate(await response.json());
  } catch (error) {
    console.error("Unable to load flight report", error);
    $("results").setAttribute("aria-busy", "false");
    $("resultCount").textContent = "報告載入失敗";
    $("errorState").hidden = false;
  }
}

["searchInput", "countryFilter", "statusFilter", "sortOrder", "sortDirection"].forEach((id) => $(id).addEventListener("input", applyFilters));
$("resetFilters").addEventListener("click", () => {
  $("searchInput").value = "";
  $("countryFilter").value = "";
  $("statusFilter").value = "";
  $("sortOrder").value = "price";
  $("sortDirection").value = "desc";
  applyFilters();
});
loadReport();
