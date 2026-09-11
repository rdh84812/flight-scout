const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

const context = vm.createContext({
  document: { getElementById: () => ({ addEventListener() {} }) },
  fetch: () => new Promise(() => {}), URL, Intl,
});
vm.runInContext(readFileSync(join(__dirname, "../web/assets/app.js"), "utf8"), context);
const compare = vm.runInContext("compareDeals", context);
const date = vm.runInContext("departureTimestamp", context);

test("departure dates sort numerically and handle year rollover", () => {
  assert.ok(date("9月30日週三", "2026-09-07") < date("10月1日週四", "2026-09-07"));
  assert.ok(date("12月31日", "2026-12-01") < date("1月2日", "2026-12-01"));
  assert.equal(date("2027-01-02", "2026-12-01"), date("1月2日", "2026-12-01"));
  assert.equal(date("日期未提供", "2026-09-07"), null);
  assert.equal(date("2月30日", "2026-09-07"), null);
  assert.equal(date("2028年2月29日", "2026-09-07"), Date.UTC(2028, 1, 29));
});

test("each ordering supports reverse and missing price/date remains last", () => {
  const a = { destination: "大阪", price: 8000, price_drop: 500, discount_info: "20%", outbound_date: "9月30日" };
  const b = { destination: "東京", price: 9000, price_drop: 1200, discount_info: "30%", outbound_date: "10月1日" };
  for (const order of ["price", "price_drop", "discount", "destination", "date"]) {
    const asc = compare(a, b, order, "asc", "2026-09-07");
    const desc = compare(a, b, order, "desc", "2026-09-07");
    assert.notEqual(asc, 0);
    assert.equal(asc, -desc);
  }
  for (const direction of ["asc", "desc"]) {
    for (const order of ["price", "date"]) {
      assert.ok(compare({}, a, order, direction, "2026-09-07") > 0);
    }
  }
});

test("price drop ordering uses the drop amount", () => {
  const smallerDrop = { destination: "大阪", price_drop: 500 };
  const largerDrop = { destination: "東京", price_drop: 1200 };
  assert.ok(compare(smallerDrop, largerDrop, "price_drop", "asc", "2026-09-07") < 0);
  assert.ok(compare(smallerDrop, largerDrop, "price_drop", "desc", "2026-09-07") > 0);
  assert.ok(compare({}, largerDrop, "price_drop", "desc", "2026-09-07") > 0);
});
