"use strict";

const state = { deals: [], filtered: [] };
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
  card.append(top, fareRow, route, footer);
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
  state.filtered.sort((a, b) => {
    if (order === "discount-desc") return discountPercent(b) - discountPercent(a);
    if (order === "destination") return String(a.destination || "").localeCompare(String(b.destination || ""), "zh-Hant");
    return (a.price ?? Number.MAX_SAFE_INTEGER) - (b.price ?? Number.MAX_SAFE_INTEGER);
  });
  render();
}

function hydrate(payload) {
  state.deals = Array.isArray(payload.deals) ? payload.deals : [];
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

["searchInput", "countryFilter", "statusFilter", "sortOrder"].forEach((id) => $(id).addEventListener("input", applyFilters));
$("resetFilters").addEventListener("click", () => {
  $("searchInput").value = "";
  $("countryFilter").value = "";
  $("statusFilter").value = "";
  $("sortOrder").value = "price-asc";
  applyFilters();
});
loadReport();
