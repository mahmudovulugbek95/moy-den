// сквозные проверки в безголовом Chrome (на экран ничего не выводится).
// Д4: сроки задач — плашкой «Задачи: N» в полосе «весь день» («День», «Неделя») и в клетке «Месяца»; 14 задач в один день — ничего не
// выходит за колонку; плашка → список дня по проектам (исполнитель, ☆, «выполнено» + «Вернуть», «Назад», Esc); «Все задачи» — группы
// по сроку; 12 событий и напоминание в одно время — «+N» и список во всплывашке; галочки «Показывать сроки задач» и «Показывать выполненные»; обои.
// Д5: запуск окна и обоев, перезагрузка, возврат на главный, calHome() — «День» на сегодня.
// всплывашка, закрытая мышью, возвращает клавиатуру (ровно 1 раз); ответ на запрос клавиатуры позже 400 мс.
// Запуск: node tests\e2e_1311.js [папка исходников] [--shots папка]
// Копия фикстуры с датами от сегодня + свои задачи, свой порт 8663; настоящие данные не трогает; временная папка — в Корзину.
// Оракул групп и плашек — свой расчёт по data.json (не функции страницы). Итог: e2e_1311-результат.json
const PUPPETEER = process.env.PUPPETEER_PATH || "puppeteer-core";
process.env.MOYDEN_TZ = process.env.MOYDEN_TZ || "Africa/Nairobi"; // сервер считает дни в том же поясе, что и страница (emulateTimezone)
const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const puppeteer = require(PUPPETEER);
const fs = require("fs"), path = require("path"), cp = require("child_process"), os = require("os");
const FX = require("./фикстуры_по_дате.js");
const DEV = path.resolve(__dirname, "..");
const args = process.argv.slice(2);
const shotsAt = args.indexOf("--shots");
const SHOTS = shotsAt >= 0 ? path.resolve(args[shotsAt + 1]) : null;
const SRC = path.resolve(args.filter((a, i) => !a.startsWith("--") && (shotsAt < 0 || i !== shotsAt + 1))[0] || DEV);
const RES = process.env.MOYDEN_E2E_OUT || path.join(__dirname, "e2e_1311-результат.json");
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
const T = fs.mkdtempSync(path.join(os.tmpdir(), "moyden-e2e1311-"));
const PORT = 8663, BASE = `http://127.0.0.1:${PORT}/`;
const TODAY = FX.today, DX = FX.addDays(TODAY, -3);  // день с 14 задачами — позавчера-1: просроченные
const SUN = FX.addDays(TODAY, 6 - FX.dow(TODAY));

// ---------- данные: фикстура + 14 задач на DX (5 «Магазин Портал», 5 ZETA, 4 LUMEN; две со временем, одна выполнена) + задачи для групп ----------
const data = JSON.parse(FX.dataJson(fs.readFileSync(path.join(__dirname, "fixture-data.json"), "utf8")));
const P14 = [["portal", 5], ["zeta", 5], ["lumen", 4]];
let id = 500;
for (const [p, n] of P14) for (let i = 0; i < n; i++) {
  const t = { id: id, project: p, title: `Д4: задача ${String(id - 499).padStart(2, "0")}` + (id === 507 ? " — очень длинное название про согласование макетов главной с подрядчиком и заказчиком" : ""), whoId: 1 + (id % 3), due: DX, state: "work" };
  if (id === 501) t.time = "09:30"; if (id === 506) t.time = "15:00";
  if (id === 504) { t.state = "done"; t.done = DX; }
  data.tasks.push(t); id++;
}
data.tasks.push({ id: 520, project: "portal", title: "Д4: позже", whoId: 1, due: FX.addDays(TODAY, 9), state: "work" });
if (SUN > TODAY) data.tasks.push({ id: 521, project: "zeta", title: "Д4: в эту неделю", whoId: 2, due: SUN, state: "work" });
data.tasks.push({ id: 522, project: "zeta", title: "Д4: без срока", whoId: 2, due: "", state: "work" });
data.tasks.push({ id: 523, project: "panda", title: "Д4: ждём ответа, срок прошёл", whoId: 2, due: FX.addDays(TODAY, -6), state: "wait" });
data.reminders = (data.reminders || []).concat([{ id: 900, date: DX, time: "10:10", text: "Д4: напоминание в «+N»" }]);
const DY = FX.addDays(TODAY, -5);  // день, где все задачи — проекта «Кедр» (его ставим на паузу)
for (const i of [530, 531]) data.tasks.push({ id: i, project: "kedr", title: `Д4: Кедр ${i}`, whoId: 1, due: DY, state: "work" });
data.projects.find(p => p.id === "portal").deadline = DX;  // плашка даты проекта в полосе дня DX
data.nextId = 600;
fs.writeFileSync(path.join(T, "data.json"), JSON.stringify(data, null, 1));
fs.copyFileSync(path.join(__dirname, "fixture-settings.json"), path.join(T, "settings.json"));
fs.writeFileSync(path.join(T, "cal.json"), '{"calendars": [{"name": "Рабочий", "ical_url": ""}, {"name": "Личный", "ical_url": ""}]}');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const readData = () => JSON.parse(fs.readFileSync(path.join(T, "data.json"), "utf8").replace(/^\uFEFF/, ""));
const api = async (method, p, body) => { const r = await fetch(BASE + p.replace(/^\//, ""), { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); return r.json(); };
let srv;
const results = [];
const ONLY = process.env.MOYDEN_E2E_ONLY ? new RegExp(process.env.MOYDEN_E2E_ONLY) : null;  // мутанты: только шаг, который должен поймать подмену
async function step(name, fn) {
  if (ONLY && !ONLY.test(name)) return;
  try { const info = await fn(); results.push({ step: name, ok: true, info: info ?? null }); console.log("PASS", name, info ? JSON.stringify(info).slice(0, 400) : ""); }
  catch (e) { results.push({ step: name, ok: false, error: String(e && e.message || e) }); console.log("FAIL", name, String(e && e.message || e).slice(0, 700)); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }
async function waitFor(fn, ms = 8000, msg = "не дождался") { const t = Date.now(); while (Date.now() - t < ms) { if (await fn()) return true; await sleep(150); } throw new Error(msg); }
const shot = async (pg, name) => { if (!SHOTS) return; await pg.screenshot({ path: path.join(SHOTS, name + ".png") }); };

// ---------- независимый оракул ----------
const projName = pid => (data.projects.find(p => p.id === pid) || {}).name || pid;
const shown = (t, o = {}) => (o.done !== false || t.state !== "done");  // завершённых проектов в фикстуре нет
const dayTasks = (d, o) => readData().tasks.filter(t => t.due === d && shown(t, o));
function groupsOracle(o = {}) {
  const g = { "Просрочено": [], "Срок прошёл: ждём ответа, пауза": [], "Сегодня": [], "Эта неделя": [], "Позже": [], "Без срока": [], "Выполнено": [] };
  for (const t of readData().tasks.filter(t => shown(t, o))) g[t.state === "done" ? "Выполнено" : !t.due ? "Без срока" : t.due < TODAY ? (t.state === "wait" ? "Срок прошёл: ждём ответа, пауза" : "Просрочено") : t.due === TODAY ? "Сегодня" : t.due <= SUN ? "Эта неделя" : "Позже"].push(t);
  return Object.entries(g).filter(([, ts]) => ts.length).map(([k, ts]) => ({ g: k, n: ts.length, ids: ts.map(t => t.id).sort((a, b) => a - b),
    p: Object.fromEntries([...new Set(ts.map(t => t.project))].map(p => [projName(p), ts.filter(t => t.project === p).length]).sort()) }));
}

(async () => {
  srv = cp.spawn((process.env.PY || path.join(DEV, ".venv", "Scripts", "python.exe")), [path.join(SRC, "server.py"), "--port", String(PORT), "--data", path.join(T, "data.json"),
    "--calendars", path.join(T, "cal.json"), "--cache", path.join(T, "cache"), "--settings", path.join(T, "settings.json"), "--log", path.join(T, "server.log"),
    "--weather-url", "http://127.0.0.1:9/none", "--weather-cache", path.join(T, "wx.json"), "--google-sync", path.join(T, "google-events.json"), "--refresh", "2"], { stdio: "ignore" });
  for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE + "api/ping")).ok) break; } catch {} await sleep(200); }
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--lang=ru-RU"], protocolTimeout: 120000 });
  await require("./страж_localhost.js").install(browser, __filename);
  const errs = [];
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 900 });
  await page.emulateTimezone("Africa/Nairobi");
  page.on("pageerror", e => errs.push(String(e)));
  // Д5 RED: у пользователя в памяти страницы с 1.3.x сохранён вид «Неделя» (calView) — 1.3.11 его не восстанавливает
  const WX = JSON.stringify({ ok: true, cities: [{ name: "Найроби", code: 3, isDay: true, now: 20, max: 20, min: 10, precip: 0 }] });
  await page.setRequestInterception(true);
  page.on("request", r => { if (/\/api\/weather(\?|$)/.test(r.url())) return r.respond({ status: 200, contentType: "application/json", body: WX }); r.continue(); });
  await page.evaluateOnNewDocument(() => { if (!sessionStorage.getItem("e2e1311")) { localStorage.setItem("calView", "week"); sessionStorage.setItem("e2e1311", "1"); } });
  await page.goto(BASE, { waitUntil: "networkidle2" });
  await waitFor(() => page.evaluate(() => document.querySelectorAll(".proj").length > 0));
  const go = async (pg, hash) => { await pg.evaluate(h => { location.hash = h; }, hash); await sleep(600); };
  const calState = pg => pg.evaluate(() => ({ вид: (document.querySelector('.seg button[aria-pressed="true"]') || {}).dataset?.v || null, дата: document.getElementById("calLabel").textContent,
    один_день: !!document.querySelector("#calBody .dw.one"), дни: [...document.querySelectorAll("#calBody .dcol")].map(c => c.dataset.d) }));
  const todayLabel = await page.evaluate(t => dayLong(t), TODAY).catch(() => null);
  const isDayToday = s => s.вид === "day" && s.один_день && s.дни.length === 1 && s.дни[0] === TODAY && s.дата === todayLabel;
  const calTo = async (pg, view, d) => {
    await pg.click(`.seg button[data-v="${view}"]`); await sleep(300); await pg.click("#calToday"); await sleep(300);
    const k = view === "week" ? FX.weeksFromToday(d) : view === "month" ? FX.monthsFromToday(d) : FX.days(d, TODAY);
    for (let i = 0; i < Math.abs(k); i++) { await pg.click(k < 0 ? "#calPrev" : "#calNext"); await sleep(250); }
    await sleep(900);
  };
  // всё внутри своей колонки: блоки сетки в .dcol, плашки полосы в .ad, строки «Месяца» в .mc (видимые)
  const fitCheck = pg => pg.evaluate(() => {
    const R = e => e.getBoundingClientRect(), bad = [], inside = (r, c) => r.left >= c.left - 0.5 && r.right <= c.right + 0.5;
    for (const c of document.querySelectorAll("#calBody .dcol")) { const cr = R(c); for (const e of c.querySelectorAll(".ev")) { const r = R(e); if (!inside(r, cr)) bad.push(`сетка ${c.dataset.d}: «${e.textContent.trim().slice(0, 20)}» ${Math.round(r.left)}–${Math.round(r.right)} вне ${Math.round(cr.left)}–${Math.round(cr.right)}`); } }
    for (const a of document.querySelectorAll("#calBody .ad")) { const ar = R(a); for (const e of a.children) { const r = R(e); if (!inside(r, ar)) bad.push(`полоса: «${e.textContent.trim().slice(0, 20)}»`); } }
    for (const t of document.querySelectorAll("#calBody [data-tday] .tx")) { const r = R(t), pr = R(t.parentElement);  // число на плашке не прячется («Задачи:…»)
      if (t.scrollWidth > t.clientWidth + 1 || r.right > pr.right + 0.5 || r.bottom > pr.bottom + 0.5 || r.left < pr.left - 0.5) bad.push(`плашка обрезана: «${t.textContent}» ${t.scrollWidth}>${t.clientWidth}`); }
    for (const m of document.querySelectorAll("#calBody .mc")) { const mr = R(m); for (const e of m.querySelectorAll(".mitems > :not([hidden])")) { const r = R(e); if (!inside(r, mr) || r.bottom > mr.bottom + 0.5) bad.push(`месяц ${m.dataset.cell}: «${e.textContent.trim().slice(0, 20)}»`); } }
    return bad;
  });
  const plateOf = (pg, d) => pg.evaluate(d => { const b = document.querySelector(`#calBody [data-tday="${d}"]`); if (!b) return null;
    const cs = getComputedStyle(b); return { text: b.textContent.trim(), late: b.classList.contains("late"), pz: b.classList.contains("pz"), полоса: !!b.closest(".ad"), клетка: !!b.closest(".mc"), первая: b.parentElement.firstElementChild === b, bg: cs.backgroundColor, fg: cs.color, hidden: b.hidden }; }, d);
  const gridTasks = pg => pg.evaluate(() => document.querySelectorAll("#calBody .dcol [data-task], #calBody .dcol .ev.task, #calBody .tk").length);
  const listState = pg => pg.evaluate(() => ({ виден: !document.getElementById("lsv").hidden, заголовок: document.getElementById("lsT").textContent, итог: document.getElementById("lsDet").textContent,
    строк: document.querySelectorAll("#lsBody .lrow").length,
    группы: [...document.querySelectorAll("#lsBody .lgrp")].map(g => ({ g: g.dataset.g, n: +g.querySelector("h2 em").textContent, строк: g.querySelectorAll(".lrow").length,
      p: Object.fromEntries([...g.querySelectorAll("h3")].map(h => [h.firstChild.textContent.trim(), +h.querySelector("em").textContent])),
      ids: [...g.querySelectorAll(".lrow")].map(r => +r.dataset.id).sort((a, b) => a - b) })),
    проекты: Object.fromEntries([...document.querySelectorAll("#lsBody > h2")].map(h => [h.firstChild.textContent.trim(), +h.querySelector("em").textContent])),
    rows: [...document.querySelectorAll("#lsBody .lrow")].map(r => ({ id: +r.dataset.id, done: r.classList.contains("done"), чипы: [...r.querySelectorAll(".t2 .chip")].map(c => c.textContent.trim()),
      звезда: !!r.querySelector(".star"), вернуть: (r.querySelector(".undo") || {}).textContent || null, ck: r.querySelector(".ck").dataset.lact })) }));

  // ---------- обои: программа отвечает «клавиатура у обоев»; задержка ответа — kbdDelay ----------
  const wp = await browser.newPage();
  await wp.setViewport({ width: 2560, height: 1392 });
  await wp.emulateTimezone("Africa/Nairobi");
  wp.on("pageerror", e => errs.push("обои: " + String(e)));
  const posts = []; let kbdDelay = 0;
  await wp.setRequestInterception(true);
  wp.on("request", r => {
    if (r.method() === "POST" && /\/api\/app\//.test(r.url())) {
      const p = { url: r.url().replace(BASE, "/"), body: r.postData(), t: Date.now(), ответ: null }; posts.push(p);
      const d = /focus/.test(p.url) ? kbdDelay : 0;
      setTimeout(() => { p.ответ = Date.now(); r.respond({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, focus: "wallpaper", released: true }) }).catch(() => {}); }, d);
      return;
    }
    if (/\/api\/weather(\?|$)/.test(r.url())) return r.respond({ status: 200, contentType: "application/json", body: WX });
    r.continue();
  });
  await wp.goto(BASE + "?wallpaper=1&left=0&bottom=0", { waitUntil: "networkidle2" });
  await waitFor(() => wp.evaluate(() => document.querySelectorAll(".proj").length > 0));
  const asks = () => posts.filter(p => p.url === "/api/app/wallpaper-focus");
  const rels = () => posts.filter(p => p.url === "/api/app/wallpaper-release");
  const noKbd = async () => { await wp.evaluate(() => { document.activeElement && document.activeElement.blur(); window.dispatchEvent(new Event("blur")); }); await sleep(150); posts.length = 0; };
  const active = pg => pg.evaluate(() => { const a = document.activeElement; return a && a !== document.body ? (a.id || a.dataset.fid || a.tagName) : null; });

  // ================= Д5 =================
  await step("Д5: запуск окна — «День» на сегодня, хотя в памяти страницы сохранён вид «Неделя»; «Неделя» → перезагрузка → «День» на сегодня", async () => {
    await page.bringToFront();
    const s0 = await calState(page);
    assert(isDayToday(s0), "FAIL-сценарий окно открылось не «Днём» на сегодня: " + JSON.stringify(s0));
    await calTo(page, "week", FX.addDays(TODAY, 14));
    const s1 = await calState(page);
    assert(s1.вид === "week" && s1.дни.length === 7, "переключение на «Неделю»: " + JSON.stringify(s1));
    await page.reload({ waitUntil: "networkidle2" }); await waitFor(() => page.evaluate(() => document.querySelectorAll(".proj").length > 0)); await sleep(1200);
    const s2 = await calState(page);
    assert(isDayToday(s2), "после перезагрузки не «День» на сегодня: " + JSON.stringify(s2));
    await shot(page, "д5-окно-после-перезагрузки");
    return { запуск: s0.вид, неделя: s1.вид, после_перезагрузки: s2 };
  });

  await step("Д5: обои — «Неделя» → перезагрузка обоев → «День» на сегодня", async () => {
    await wp.bringToFront(); await sleep(800);
    await calTo(wp, "week", TODAY);
    const s1 = await calState(wp);
    await wp.reload({ waitUntil: "networkidle2" }); await waitFor(() => wp.evaluate(() => document.querySelectorAll(".proj").length > 0)); await sleep(1200);
    const s2 = await calState(wp);
    assert(s1.вид === "week" && isDayToday(s2), JSON.stringify({ s1, s2 }));
    return { до: s1.вид, после: s2 };
  });

  await step("Д5: «Месяц» на два месяца вперёд → проект → «Назад» (и Esc) → «День» на сегодня; window.calHome() из app.py — тоже", async () => {
    await page.bringToFront();
    const out = {};
    for (const how of ["назад", "esc"]) {
      await calTo(page, "month", FX.addDays(TODAY, 62));
      await page.click('.proj[data-pid="zeta"]'); await page.waitForSelector("#pv:not([hidden])"); await sleep(600);
      if (how === "назад") await page.click("#pv [data-back]"); else { await page.evaluate(() => document.activeElement && document.activeElement.blur()); await page.keyboard.press("Escape"); }
      await page.waitForSelector("#home:not([hidden])"); await sleep(1000);
      out[how] = await calState(page);
    }
    await calTo(page, "week", FX.addDays(TODAY, 7));
    await page.evaluate(() => window.calHome()); await sleep(900);
    out.calHome = await calState(page);
    await calTo(page, "week", FX.addDays(TODAY, 7));
    await page.click("#calFull"); await sleep(500); await page.keyboard.press("Escape"); await sleep(600);
    out.развернуть_свернуть = await calState(page);  // это тот же календарь — выбранный вид держится
    const bad = Object.entries(out).filter(([k, s]) => k === "развернуть_свернуть" ? s.вид !== "week" : !isDayToday(s));
    assert(!bad.length, "FAIL-сценарий " + JSON.stringify(Object.fromEntries(bad)));
    return out;
  });

  await step("Д5 : проект, открытый из календаря (плашка «Срок сдачи» в полосе дня), → «Назад» и Esc — календарь на той же неделе; из списка проектов — «День» на сегодня", async () => {
    await page.bringToFront(); await go(page, "");
    const out = {};
    for (const how of ["назад", "esc"]) {
      await calTo(page, "week", DX);
      await page.click(`#calBody .ad [data-proj="portal"]`); await page.waitForSelector("#pv:not([hidden])"); await sleep(600);
      if (how === "назад") await page.click("#pv [data-back]"); else { await page.evaluate(() => document.activeElement && document.activeElement.blur()); await page.keyboard.press("Escape"); }
      await page.waitForSelector("#home:not([hidden])"); await sleep(900);
      out[how] = await calState(page);
    }
    await page.click('.proj[data-pid="portal"]'); await page.waitForSelector("#pv:not([hidden])"); await sleep(500);
    await page.click("#pv [data-back]"); await page.waitForSelector("#home:not([hidden])"); await sleep(900);
    out.из_списка_проектов = await calState(page);
    const keep = s => s.вид === "week" && s.дни.includes(DX);
    assert(keep(out.назад) && keep(out.esc) && isDayToday(out.из_списка_проектов), "FAIL-сценарий " + JSON.stringify(out));
    return out;
  });

  await step("Д5 : главный в «Неделе» → проект из списка → карточка события на странице проекта → ссылка проекта → «Назад» до главного — «День» на сегодня; та же ссылка из карточки в календаре — вид держится", async () => {
    const EV = FX.addDays(TODAY, 1);
    fs.writeFileSync(path.join(T, "google-events.json"), JSON.stringify({ source: "e2e", synced_at: new Date(Date.now() - 60000).toISOString(),
      calendars: [{ id: "work", name: "Рабочий", color: "#E8833A" }], events: [{ id: "fx-aud1", calendar: "work", title: "Совещание Магазин Портал", start: `${EV}T12:00:00+03:00`, end: `${EV}T13:00:00+03:00`, allDay: false }] }));
    await page.bringToFront(); await go(page, ""); await sleep(2500);
    const out = {};
    // путь карточка открыта со страницы проекта
    await calTo(page, "week", FX.addDays(TODAY, 21));
    await page.click('.proj[data-pid="portal"]'); await page.waitForSelector("#pv:not([hidden])");
    await waitFor(() => page.$('#pvEvents [data-pev]'), 9000, "событие проекта не показано на странице проекта");
    await page.click('#pvEvents [data-pev]'); await page.waitForSelector("#calPop:popover-open a[href^='#/p/']"); await sleep(300);
    await page.click("#calPop a[href^='#/p/']"); await sleep(800);
    for (let i = 0; i < 4 && await page.$eval("#home", e => e.hidden); i++) { await page.evaluate(() => { const p = document.getElementById("calPop"); if (p.matches(":popover-open")) p.hidePopover(); document.activeElement && document.activeElement.blur(); }); await page.keyboard.press("Escape"); await sleep(700); }
    await page.waitForSelector("#home:not([hidden])"); await sleep(900);
    out.из_проекта = await calState(page);
    // обратный путь: карточка открыта из календаря — навигация внутри календаря, вид держится
    await calTo(page, "week", EV); await sleep(1200);
    await page.evaluate(d => { for (const b of document.querySelectorAll(`#calBody .dcol[data-d="${d}"] .ev`)) if (b.textContent.includes("Совещание Магазин Портал")) { b.click(); return; } }, EV);
    await page.waitForSelector("#calPop:popover-open a[href^='#/p/']"); await sleep(300);
    await page.click("#calPop a[href^='#/p/']"); await page.waitForSelector("#pv:not([hidden])"); await sleep(600);
    await page.evaluate(() => document.activeElement && document.activeElement.blur()); await page.keyboard.press("Escape");
    await page.waitForSelector("#home:not([hidden])"); await sleep(900);
    out.из_календаря = await calState(page);
    fs.unlinkSync(path.join(T, "google-events.json"));
    await calTo(page, "day", TODAY);
    assert(isDayToday(out.из_проекта) && out.из_календаря.вид === "week" && out.из_календаря.дни.includes(EV), "FAIL-сценарий " + JSON.stringify(out));
    return out;
  });

  // ================= Д4 =================
  await step("Д4: 14 задач в один день — одна плашка «Задачи: 14» (красная: есть просроченные) в полосе «весь день» в «Неделе» и «Дне», первой строкой в «Месяце»; на сетке часов сроков нет; ничего не выходит за колонку — 1600, 1100, обои 2560", async () => {
    const out = {}, bad = [];
    const n14 = dayTasks(DX).length;
    assert(n14 === 14, "оракул: " + n14);
    for (const [pg, w, h, nm] of [[page, 1600, 900, "1600"], [page, 1100, 800, "1100"], [wp, 2560, 1392, "обои"]]) {
      await pg.bringToFront(); if (pg === page) await pg.setViewport({ width: w, height: h }); await go(pg, ""); await sleep(600);
      for (const view of ["week", "day", "month"]) {
        await calTo(pg, view, DX);
        const pl = await plateOf(pg, DX), fit = await fitCheck(pg), gt = await gridTasks(pg);
        const k = `${nm} ${view}`; out[k] = { плашка: pl && pl.text, красная: pl && pl.late, сроков_на_сетке: gt, вне_колонки: fit.length };
        const where = view === "month" ? pl && pl.клетка && pl.первая && !pl.hidden : pl && pl.полоса;
        if (!pl || pl.text !== "Задачи: 14" || !pl.late || !where || gt !== 0 || fit.length) { bad.push(k); out[k].вне = fit.slice(0, 5); }
        await shot(pg, `д4-${view}-${nm}`);
      }
      await calTo(pg, "day", TODAY);
    }
    await page.setViewport({ width: 1600, height: 900 });
    assert(!bad.length, "FAIL-сценарий 1.3.10 (блоки 18:00 вылезают в соседние дни): " + bad.join("; ") + " | " + JSON.stringify(Object.fromEntries(bad.map(b => [b, out[b]]))));
    return out;
  });

  await step("Д4: плашка → список дня: 14 строк по проектам (5/5/4), у каждой исполнитель и ☆; выполненная — «выполнено» и «Вернуть»; ☆ сохраняется; «Вернуть» возвращает в работу; «Назад» и Esc — на главный", async () => {
    await page.bringToFront(); await go(page, ""); await calTo(page, "week", DX);
    await page.click(`#calBody [data-tday="${DX}"]`); await page.waitForSelector("#lsv:not([hidden])"); await sleep(500);
    const L = await listState(page);
    await shot(page, "д4-список-дня-1600");
    const emp = id => { const e = data.employees.find(x => x.id === id); return [e.firstName, e.lastName].filter(Boolean).join(" ") || e.name; };
    const want = dayTasks(DX), wantP = Object.fromEntries(P14.map(([p, n]) => [projName(p), n]));
    const who = L.rows.filter(r => { const t = want.find(x => x.id === r.id); return !t || !r.чипы.includes(emp(t.whoId)); }).map(r => r.id);
    const done = L.rows.find(r => r.id === 504);
    // у выполненной — «выполнено, вовремя» / «…, с опозданием на N дн.» от планового срока; свой оракул по data.json: 504 выполнена в день плана
    const t504 = readData().tasks.find(t => t.id === 504), late504 = Math.max(0, FX.days(t504.done, t504.plan || t504.due));
    assert(late504 === 0, "оракул: 504 выполнена не в день плана — " + JSON.stringify(t504));
    const doneChip = "выполнено, вовремя";
    const ok = L.виден && L.строк === 14 && JSON.stringify(L.проекты) === JSON.stringify(wantP) && !who.length && L.rows.every(r => r.звезда)
      && done && done.done && done.чипы.includes(doneChip) && done.вернуть === "Вернуть" && done.ck === "undo"
      && L.rows.filter(r => r.id !== 504).every(r => !r.done && r.вернуть === null && r.ck === "done")
      && L.rows.find(r => r.id === 501).чипы.includes("к 09:30") && /14 задач в 3 проектах/.test(L.итог) && L.заголовок.includes(String(+DX.slice(8)));
    assert(ok, JSON.stringify({ L: { ...L, rows: L.rows.slice(0, 3) }, без_исполнителя: who, done }));
    // ☆ туда и обратно
    await page.click('#lsBody .lrow[data-id="500"] .star'); await waitFor(() => readData().tasks.find(t => t.id === 500).star === true, 5000, "☆ не сохранилась");
    await sleep(600); await page.click('#lsBody .lrow[data-id="500"] .star'); await waitFor(() => readData().tasks.find(t => t.id === 500).star !== true, 5000, "☆ не снялась");
    // «Вернуть»
    await sleep(400); await page.click('#lsBody .lrow[data-id="504"] .undo');
    await waitFor(() => readData().tasks.find(t => t.id === 504).state === "work", 5000, "«Вернуть» не вернул в работу");
    await sleep(800);
    const L2 = await listState(page), r504 = L2.rows.find(r => r.id === 504);
    assert(L2.строк === 14 && r504 && !r504.done && r504.вернуть === null, "после «Вернуть»: " + JSON.stringify(r504));
    await api("PATCH", "api/tasks/504", { state: "done" }); await sleep(2500);
    //«ждём ответа» (потеря данных пользователя)
    await api("PATCH", "api/tasks/510", { state: "wait" }); await sleep(2500);
    await page.click('#lsBody .lrow[data-id="510"] .ck'); await waitFor(() => readData().tasks.find(t => t.id === 510).state === "done", 5000, "✓ не отметил 510");
    await sleep(1200); await page.click('#lsBody .lrow[data-id="510"] .undo');
    await waitFor(() => readData().tasks.find(t => t.id === 510).state !== "done", 5000, "«Вернуть» 510 не сработал");
    const st510 = readData().tasks.find(t => t.id === 510).state;
    assert(st510 === "wait", "FAIL-сценарий 1.3.11 круг 1: «Вернуть» из «ждём ответа» дал «" + st510 + "»");
    await api("PATCH", "api/tasks/510", { state: "work" }); await sleep(2500);
    await go(page, "#/p/panda"); await page.waitForSelector('#pv .trow[data-id="523"]'); await sleep(500);
    await page.click('#pv .trow[data-id="523"] [data-act="toggle"]'); await waitFor(() => readData().tasks.find(t => t.id === 523).state === "done", 5000, "✓ в проекте не отметил 523");
    await sleep(1500);
    await page.evaluate(() => { document.getElementById("doneBox").open = true; }); await page.waitForSelector('#doneList .trow[data-id="523"] [data-act="toggle"]', { visible: true }); await sleep(300);
    await page.click('#doneList .trow[data-id="523"] [data-act="toggle"]'); await waitFor(() => readData().tasks.find(t => t.id === 523).state !== "done", 5000, "снять ✓ в проекте («Выполненные») не сработало");
    const st523 = readData().tasks.find(t => t.id === 523).state;
    assert(st523 === "wait", "переключатель в проекте: «ждём ответа» потеряно — " + st523);
    await go(page, "#/list/day/2026-02-30"); await sleep(700);
    const bad = await page.evaluate(() => ({ home: !document.getElementById("home").hidden, list: !document.getElementById("lsv").hidden }));
    assert(bad.home && !bad.list, "#/list/day/2026-02-30 — не на главный: " + JSON.stringify(bad));
    await go(page, ""); await calTo(page, "week", DX);
    await page.click(`#calBody [data-tday="${DX}"]`); await page.waitForSelector("#lsv:not([hidden])"); await sleep(400);
    // «Назад»
    await page.click("#lsv [data-back]"); await page.waitForSelector("#home:not([hidden])"); await sleep(600);
    const back = await calState(page);
    // Esc
    await calTo(page, "week", DX); await page.click(`#calBody [data-tday="${DX}"]`); await page.waitForSelector("#lsv:not([hidden])"); await sleep(400);
    await page.evaluate(() => document.activeElement && document.activeElement.blur()); await page.keyboard.press("Escape");
    await page.waitForSelector("#home:not([hidden])", { timeout: 5000 }); await sleep(600);
    const esc = await calState(page);
    const keep = s => s.вид === "week" && s.дни.includes(DX);  // возврат из списка дня — календарь там же
    assert(keep(back) && keep(esc), "после «Назад»/Esc из списка дня календарь не на той же неделе: " + JSON.stringify({ back, esc }));
    return { строк: L.строк, проекты: L.проекты, выполненная: done };
  });

  await step("Д4: «Месяц» — нажатие на «Задачи: N» в клетке открывает тот же список дня", async () => {
    await page.bringToFront(); await go(page, ""); await calTo(page, "month", DX);  // разбор с главного — не зависит от экрана, где кончился прошлый шаг
    await page.click(`#calBody .mc [data-tday="${DX}"]`); await page.waitForSelector("#lsv:not([hidden])"); await sleep(500);
    const L = await listState(page);
    await page.click("#lsv [data-back]"); await page.waitForSelector("#home:not([hidden])"); await sleep(500);
    assert(L.строк === 14 && L.rows.map(r => r.id).sort((a, b) => a - b).join() === dayTasks(DX).map(t => t.id).sort((a, b) => a - b).join(), JSON.stringify(L.rows.map(r => r.id)));
    return { строк: L.строк };
  });

  await step("Д4: «Все задачи» — кнопка рядом с «День / Неделя / Месяц»; группы Просрочено → Сегодня → Эта неделя → Позже → Без срока (→ Выполнено), внутри по проектам; состав и числа = свой расчёт по data.json; окно и обои", async () => {
    const out = {}, want = groupsOracle();
    for (const [pg, nm] of [[page, "окно"], [wp, "обои"]]) {
      await pg.bringToFront(); await go(pg, ""); await sleep(500);
      const pos = await pg.evaluate(() => { const R = s => document.querySelector(s).getBoundingClientRect(), a = R("#calAll"), sg = R(".seg"), f = R("#calFull");
        return { текст: document.getElementById("calAll").textContent.trim(), справа_от_видов: a.left >= sg.right - 1 && a.left - sg.right < 40, левее_развернуть: a.right <= f.left + 1, одна_строка: Math.abs((a.top + a.bottom) / 2 - (sg.top + sg.bottom) / 2) < 4 }; });
      await pg.click("#calAll"); await pg.waitForSelector("#lsv:not([hidden])"); await sleep(500);
      const L = await listState(pg);
      await shot(pg, `д4-все-задачи-${nm}`);
      const got = L.группы.map(g => ({ g: g.g, n: g.n, ids: g.ids, p: Object.fromEntries(Object.entries(g.p).sort()) }));
      out[nm] = { кнопка: pos, группы: got.map(g => `${g.g} ${g.n}`) };
      const order = ["Просрочено", "Срок прошёл: ждём ответа, пауза", "Сегодня", "Эта неделя", "Позже", "Без срока", "Выполнено"];
      const kpi = await pg.evaluate(() => +document.querySelector("#sum .kpi b").textContent);  // «Просрочено» в списке = счётчику главного экрана
      const okOrder = got.every((g, i) => i === 0 || order.indexOf(got[i - 1].g) < order.indexOf(g.g));
      const okEq = JSON.stringify(got) === JSON.stringify(want) && L.группы.every(g => g.строк === g.n) && (got.find(g => g.g === "Просрочено") || { n: 0 }).n === kpi && got.some(g => g.g === "Срок прошёл: ждём ответа, пауза" && g.ids.includes(523));
      if (!(pos.текст === "Все задачи" && pos.справа_от_видов && pos.левее_развернуть && pos.одна_строка && okOrder && okEq && L.заголовок === "Все задачи"))
        throw new Error(`${nm}: ` + JSON.stringify({ pos, got, want }));
      await pg.evaluate(() => document.activeElement && document.activeElement.blur()); await pg.keyboard.press("Escape");
      await pg.waitForSelector("#home:not([hidden])", { timeout: 5000 }); await sleep(500);
    }
    return out;
  });

  await step("Д4: галочки — «Показывать выполненные» снята: плашка «Задачи: 13», список дня 13, в «Все задачи» нет «Выполнено»; «Показывать сроки задач» снята: плашек нет в «Неделе» и «Месяце»; вернули — снова 14", async () => {
    await page.bringToFront(); await go(page, ""); await calTo(page, "week", DX);
    const out = {};
    await page.click("#calDone"); await sleep(500);
    out.без_выполненных = (await plateOf(page, DX) || {}).text;
    await page.click(`#calBody [data-tday="${DX}"]`); await page.waitForSelector("#lsv:not([hidden])"); await sleep(400);
    const L = await listState(page); out.список = L.строк; out.выполненная_в_списке = L.rows.some(r => r.id === 504);
    await page.click("#lsv [data-back]"); await page.waitForSelector("#home:not([hidden])"); await sleep(400);
    await page.click("#calAll"); await page.waitForSelector("#lsv:not([hidden])"); await sleep(400);
    const A = await listState(page); out.все_задачи = A.группы.map(g => `${g.g} ${g.n}`);
    const wantA = groupsOracle({ done: false });
    await page.click("#lsv [data-back]"); await page.waitForSelector("#home:not([hidden])"); await sleep(400);
    await calTo(page, "week", DX);
    await page.click("#calDone"); await sleep(400);  // вернули «выполненные»
    out.снова = (await plateOf(page, DX) || {}).text;
    await page.click("#calTasks"); await sleep(400);
    out.сроки_сняты_неделя = await page.evaluate(() => document.querySelectorAll("#calBody [data-tday]").length);
    await calTo(page, "month", DX);
    out.сроки_сняты_месяц = await page.evaluate(() => document.querySelectorAll("#calBody [data-tday]").length);
    await page.click("#calTasks"); await sleep(400);
    out.сроки_вернули_месяц = (await plateOf(page, DX) || {}).text;
    await calTo(page, "day", TODAY);
    const ok = out.без_выполненных === "Задачи: 13" && out.список === 13 && !out.выполненная_в_списке
      && JSON.stringify(A.группы.map(g => ({ g: g.g, n: g.n, ids: g.ids, p: Object.fromEntries(Object.entries(g.p).sort()) }))) === JSON.stringify(wantA) && !A.группы.some(g => g.g === "Выполнено")
      && out.снова === "Задачи: 14" && out.сроки_сняты_неделя === 0 && out.сроки_сняты_месяц === 0 && out.сроки_вернули_месяц === "Задачи: 14";
    assert(ok, JSON.stringify(out));
    return out;
  });

  await step("Д4: пауза — просроченные задачи проекта на паузе не красят плашку (серая); в списке дня — «на паузе», без «просрочено»; пауза снята — снова красная", async () => {
    await api("PATCH", "api/projects/kedr", { pause: { reason: "e2e 1.3.11" } }); await sleep(3500);
    await page.bringToFront(); await go(page, ""); await calTo(page, "week", DY);
    const pl = await plateOf(page, DY);
    await page.click(`#calBody [data-tday="${DY}"]`); await page.waitForSelector("#lsv:not([hidden])"); await sleep(450);
    const rows = await page.$$eval("#lsBody .lrow", els => els.map(r => ({ t: r.innerText.replace(/\s+/g, " "), pause: r.querySelector(".dot").classList.contains("pause"), late: r.querySelector(".dot").classList.contains("late") })));
    await page.click("#lsv [data-back]"); await page.waitForSelector("#home:not([hidden])"); await sleep(400);
    await page.click("#calAll"); await page.waitForSelector("#lsv:not([hidden])"); await sleep(450);
    const A = await listState(page), kpi = await page.evaluate(() => +document.querySelector("#sum .kpi b").textContent);
    await page.click("#lsv [data-back]"); await page.waitForSelector("#home:not([hidden])"); await sleep(400);
    const gp = A.группы.find(g => g.g === "Срок прошёл: ждём ответа, пауза"), gl = A.группы.find(g => g.g === "Просрочено");
    const allOk = gp && [530, 531].every(i => gp.ids.includes(i)) && gl && ![530, 531].some(i => gl.ids.includes(i)) && gl.n === kpi;
    await api("PATCH", "api/projects/kedr", { pause: null }); await sleep(3500);
    await calTo(page, "week", DY); const pl2 = await plateOf(page, DY); await calTo(page, "day", TODAY);
    assert(allOk, "«Все задачи» при паузе: " + JSON.stringify({ gp, gl: gl && { n: gl.n }, kpi }));
    const ok = pl && pl.text === "Задачи: 2" && !pl.late && pl.pz && rows.length === 2 && rows.every(r => r.pause && !r.late && r.t.includes("на паузе") && !/просрочено/.test(r.t))
      && pl2 && pl2.late && !pl2.pz;
    assert(ok, JSON.stringify({ pl, rows, pl2 }));
    return { на_паузе: pl, строки: rows.length, снята: pl2 && pl2.late };
  });

  await step("Д4: 12 событий и напоминание в одно время — в колонке не больше столбцов, чем помещается, остальные «+N»; всё внутри колонки; видимые + N = 12; «+N» → список 12 во всплывашке; пункт → карточка события; «Неделя» 1600 и 1100, «День», обои", async () => {
    const ev = [];
    for (let i = 0; i < 12; i++) ev.push({ id: `fx-n${i}`, calendar: "work", title: `Совещание ${String(i + 1).padStart(2, "0")}`, start: `${DX}T10:${String(i * 2).padStart(2, "0")}:00+03:00`, end: `${DX}T11:30:00+03:00`, allDay: false });
    fs.writeFileSync(path.join(T, "google-events.json"), JSON.stringify({ source: "e2e 1.3.11", synced_at: new Date(Date.now() - 60000).toISOString(),
      calendars: [{ id: "work", name: "Рабочий", color: "#E8833A" }, { id: "personal", name: "Личный", color: "#4285F4" }], events: ev }));
    const out = {}, bad = [];
    const cnt = pg => pg.evaluate(d => { const c = document.querySelector(`#calBody .dcol[data-d="${d}"]`); if (!c) return null;
      const vis = [...c.querySelectorAll(".ev:not(.evmore)")].filter(e => /Совещание|напоминание в/.test(e.textContent)), more = [...c.querySelectorAll(".ev.evmore")];
      return { видимых: vis.length, плюс: more.map(m => +m.textContent.replace("+", "")), мин_ширина: Math.min(...vis.map(e => e.getBoundingClientRect().width)), колонка: Math.round(c.getBoundingClientRect().width) }; }, DX);
    for (const [pg, w, h, nm, view] of [[page, 1600, 900, "1600", "week"], [page, 1100, 800, "1100", "week"], [page, 1600, 900, "1600", "day"], [page, 1100, 800, "1100", "day"], [wp, 2560, 1392, "обои", "week"]]) {
      await pg.bringToFront(); if (pg === page) await pg.setViewport({ width: w, height: h }); await go(pg, ""); await sleep(400);
      await calTo(pg, view, DX); await sleep(1500); await calTo(pg, view, DX);
      await pg.evaluate(() => { const hh = parseFloat(document.querySelector(".dw").style.getPropertyValue("--hh")); document.querySelector(".dwscroll").scrollTop = 9 * hh; }); await sleep(300);
      const c = await cnt(pg), fit = await fitCheck(pg), k = `${nm} ${view}`;
      out[k] = { ...c, вне_колонки: fit.length };
      await shot(pg, `д4-плюсN-${view}-${nm}`);
      const sum = c ? c.видимых + c.плюс.reduce((a, b) => a + b, 0) : 0;
      if (!c || sum !== 13 || fit.length || (c.видимых && c.мин_ширина < 30) || (view === "week" && !c.плюс.length)) { bad.push(k); out[k].вне = fit.slice(0, 4); continue; }
      if (c.плюс.length) {
        await pg.click(`#calBody .dcol[data-d="${DX}"] .ev.evmore`); await sleep(600);
        const pop = await pg.evaluate(() => { const p = document.getElementById("calPop"); return { открыт: p.matches(":popover-open"), пунктов: p.querySelectorAll("[data-mi]").length, заголовок: (p.querySelector("h3") || {}).textContent || "", фокус: document.activeElement && document.activeElement.className }; });
        out[k].список = pop;
        if (nm === "1600" && view === "week") {
          await shot(pg, "д4-плюсN-список");
          // опрос перерисовал сетку и «+N» исчез — пункт списка всё равно открывает карточку
          await pg.evaluate(d => document.querySelector(`#calBody .dcol[data-d="${d}"] .ev.evmore`).remove(), DX);
          await pg.evaluate(() => [...document.querySelectorAll("#calPop [data-mi]")].find(b => b.textContent.includes("Совещание 02")).click()); await sleep(600);
          const c2 = await pg.evaluate(() => { const p = document.getElementById("calPop"); return p.matches(":popover-open") && !p.querySelector("[data-mi]") ? p.innerText : ""; });
          out[k].без_якоря = c2.slice(0, 60);
          if (!/Совещание 02/.test(c2) || !/10:02–11:30/.test(c2)) bad.push(k + " пункт без якоря молчит (нет карточки события)");
          await pg.keyboard.press("Escape"); await sleep(300); await calTo(pg, view, DX);
          await pg.evaluate(() => { const hh = parseFloat(document.querySelector(".dw").style.getPropertyValue("--hh")); document.querySelector(".dwscroll").scrollTop = 9 * hh; }); await sleep(300);
          await pg.click(`#calBody .dcol[data-d="${DX}"] .ev.evmore`); await sleep(600);
        }
        if (!pop.открыт || pop.пунктов !== 13 || !/10:00/.test(pop.заголовок)) { bad.push(k + " список"); continue; }
        await pg.evaluate(() => [...document.querySelectorAll("#calPop [data-mi]")].find(b => b.textContent.includes("Совещание 04")).click()); await sleep(600);
        const card = await pg.evaluate(() => document.getElementById("calPop").matches(":popover-open") ? document.getElementById("calPop").innerText : "");
        out[k].карточка = card.slice(0, 60);
        if (!/Совещание 04/.test(card) || !/10:06/.test(card)) bad.push(k + " карточка");
        await pg.keyboard.press("Escape"); await sleep(400);
        if (await pg.evaluate(() => document.getElementById("calPop").matches(":popover-open"))) bad.push(k + " Esc");
      }
    }
    { // обои — «+N» → пункт «Напоминание» открывает поле и просит клавиатуру (ровно 1 запрос)
      await wp.bringToFront(); await go(wp, ""); await calTo(wp, "week", DX); await noKbd();
      await wp.click(`#calBody .dcol[data-d="${DX}"] .ev.evmore`); await sleep(600);
      const it = await wp.evaluateHandle(() => [...document.querySelectorAll("#calPop [data-mi]")].find(b => b.textContent.includes("напоминание в")));
      await it.click(); await sleep(800);
      out.обои_напоминание = { фокус: await active(wp), запросов: asks().length };
      if (out.обои_напоминание.фокус !== "remText" || out.обои_напоминание.запросов !== 1) bad.push("обои: «+N» → напоминание без клавиатуры");
      await wp.keyboard.press("Escape"); await sleep(600);
    }
    fs.unlinkSync(path.join(T, "google-events.json"));
    await page.setViewport({ width: 1600, height: 900 });
    for (const pg of [page, wp]) { await pg.bringToFront(); await go(pg, ""); await calTo(pg, "day", TODAY); }
    assert(!bad.length, "FAIL-сценарий 1.3.10 (блоки уже 40 px вылезают за колонку): " + bad.join("; ") + " | " + JSON.stringify(out));
    return out;
  });

  await step("Д4: обои — плашка → список дня (14) → Esc на главный; «Все задачи» на обоях → «Назад»", async () => {
    await wp.bringToFront(); await go(wp, ""); await calTo(wp, "week", DX);
    await wp.click(`#calBody [data-tday="${DX}"]`); await wp.waitForSelector("#lsv:not([hidden])"); await sleep(500);
    const L = await listState(wp);
    await shot(wp, "д4-список-дня-обои");
    await wp.evaluate(() => document.activeElement && document.activeElement.blur()); await wp.keyboard.press("Escape");
    await wp.waitForSelector("#home:not([hidden])", { timeout: 5000 }); await sleep(600);
    const s = await calState(wp);
    await wp.click("#calAll"); await wp.waitForSelector("#lsv:not([hidden])"); await sleep(400);
    const A = await listState(wp);
    await wp.click("#lsv [data-back]"); await wp.waitForSelector("#home:not([hidden])"); await sleep(400);
    assert(L.строк === 14 && s.вид === "week" && s.дни.includes(DX) && A.группы.length >= 4, JSON.stringify({ строк: L.строк, s, группы: A.группы.map(g => g.g) }));
    return { список_дня: L.строк, после_esc: s.вид, групп: A.группы.length };
  });

  await step("шапка (пункт 24): по x — дата < часы < погода < помидоро; часы справа от «Среда / дата» под приветствием; «Днём до 20°, ночью до 10°, без осадков» — не больше 2 строк; ничего не обрезано и не перенесено; в ряду справа нет пустоты — 1600, 1100, обои 2560", async () => {
    const out = {}, bad = [];
    for (const [pg, w, h, nm] of [[page, 1600, 900, "1600"], [page, 1100, 800, "1100"], [wp, 2560, 1392, "обои"]]) {
      await pg.bringToFront(); if (pg === page) await pg.setViewport({ width: w, height: h });
      await go(pg, ""); await pg.reload({ waitUntil: "networkidle2" });
      await waitFor(() => pg.evaluate(() => !document.getElementById("wx").hidden && document.querySelectorAll(".proj").length > 0 && !!document.querySelector("#wx .wxd")), 12000, nm + ": погода не показана");
      await sleep(900);
      const r = await pg.evaluate(() => {
        const B = e => { const b = e.getBoundingClientRect(); return { l: b.left, r: b.right, t: b.top, b: b.bottom, h: b.height }; }, Q = s => B(document.querySelector(s));
        const d = Q("#date"), wd = Q("#wday"), c = Q("#clock"), x = Q("#wx"), p = Q("#pomo"), he = Q(".dt > .hello"), hdr = document.querySelector("header"), hr = B(hdr);
        const pr = parseFloat(getComputedStyle(hdr).paddingRight) * ((hr.r - hr.l) / hdr.offsetWidth || 1);  // обои: масштаб zoom
        const lines = e => { const g = document.createRange(); g.selectNodeContents(e); return new Set([...g.getClientRects()].filter(q => q.width > 0).map(q => Math.round(q.top))).size; };  // строки текста, не зависят от zoom
        const one = s => lines(document.querySelector(s));
        const wxd = document.querySelector("#wx .wxd");
        const cut = [...document.querySelectorAll("header .dt *, #clock, #wx *, #pomo *, #sum *")].filter(e => e.getClientRects().length && e.scrollWidth > Math.ceil(e.getBoundingClientRect().width) + 1).map(e => e.id || e.className || e.tagName);
        const kids = [...hdr.children].filter(e => e.getClientRects().length && getComputedStyle(e).visibility !== "hidden").map(e => ({ id: e.id || e.className, ...B(e) }));
        const rows = []; for (const k of kids) { const r = rows.find(r => Math.abs(r[0].t - k.t) < 30 || (k.t < r[0].b && r[0].t < k.b)); r ? r.push(k) : rows.push([k]); }
        const holes = rows.filter(r => r.length >= 2).map(r => +(hr.r - pr - Math.max(...r.map(k => k.r))).toFixed(1));
        return { порядок: Math.max(d.r, wd.r) < c.l && c.r < x.l && x.r <= p.l + 0.5, часы_под_приветствием: c.t >= he.b - 1 && c.b <= Math.max(d.b, wd.b) + 4,
          строк_погоды: lines(wxd), текст_погоды: wxd.textContent, обрезано: cut, дыры_справа: holes,
          одной_строкой: { часы: one("#clock"), день: one("#wday"), таймер: one("#pomoTime") }, гориз_прокрутка: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 };
      });
      out[nm] = r;
      if (!r.порядок || !r.часы_под_приветствием || r.строк_погоды > 2 || !/без осадков/.test(r.текст_погоды) || r.обрезано.length || r.дыры_справа.some(v => v > 2)
        || Object.values(r.одной_строкой).some(v => v !== 1) || r.гориз_прокрутка) bad.push(nm);
      await shot(pg, `шапка-${nm}`);
    }
    await page.setViewport({ width: 1600, height: 900 });
    assert(!bad.length, "FAIL-сценарий 1.3.10 (часы посередине, погода справа): " + bad.join(", ") + " | " + JSON.stringify(out));
    return out;
  });

  // ================= клавиатура обоев =================
  const slotClick = async () => {
    await calTo(wp, "day", TODAY);
    const col = await (await wp.$("#calBody .dcol")).boundingBox(), sc = await (await wp.$("#calBody .dwscroll")).boundingBox();
    await wp.mouse.click(col.x + col.width * 0.8, sc.y + sc.height / 2); await sleep(150);
  };
  await step("MED : обои — всплывашка напоминания, закрытая мышью (× и щелчок мимо), возвращает клавиатуру ровно 1 раз; Esc из поля — ровно 1 (не 2); щелчок мимо в другое поле — 0", async () => {
    await wp.bringToFront(); await go(wp, ""); kbdDelay = 0;
    const out = {};
    // ×
    await noKbd(); await slotClick(); await sleep(600);
    const a1 = asks().length, f1 = await active(wp);
    await wp.click("#calPop .x"); await sleep(1000);
    out.крестик = { запрос: a1, фокус: f1, возвратов: rels().length };
    // щелчок мимо (не в поле)
    await noKbd(); await slotClick(); await sleep(600);
    const a2 = asks().length;
    await wp.click("header #date"); await sleep(1000);
    out.мимо = { запрос: a2, открыт: await wp.evaluate(() => document.getElementById("calPop").matches(":popover-open")), возвратов: rels().length };
    // Esc из поля
    await noKbd(); await slotClick(); await sleep(600);
    await wp.keyboard.press("Escape"); await sleep(1100);
    out.esc = { возвратов: rels().length };
    // щелчок мимо в другое поле: клавиатура нужна новому полю
    await noKbd(); await slotClick(); await sleep(600);
    posts.length = 0;
    await wp.click("#addProjBtn"); await sleep(1000);
    out.в_поле = { возвратов: rels().length, фокус: await active(wp) };
    await wp.keyboard.press("Escape"); await sleep(600);
    const ok = out.крестик.запрос >= 1 && out.крестик.фокус === "remText" && out.крестик.возвратов === 1 && out.мимо.запрос >= 1 && !out.мимо.открыт && out.мимо.возвратов === 1
      && out.esc.возвратов === 1 && out.в_поле.возвратов === 0;
    assert(ok, "FAIL-сценарий 1.3.10 (закрыта мышью — клавиатура осталась у обоев): " + JSON.stringify(out));
    return out;
  });

  await step("LOW : обои — ответ на запрос клавиатуры приходит через 900 мс: всплывашку уже закрыли мышью — клавиатура возвращается после ответа; Enter до ответа — возврат после ответа", async () => {
    await wp.bringToFront(); await go(wp, "");
    const out = {};
    kbdDelay = 900;
    await noKbd(); await slotClick();  // запрос ушёл, ответа ещё нет
    await wp.click("#calPop .x"); await sleep(1800);
    const ask = asks()[0], after = rels().filter(r => ask && ask.ответ && r.t >= ask.ответ);
    out.закрыта_мышью = { запросов: asks().length, ответ_через_мс: ask && ask.ответ ? ask.ответ - ask.t : null, возвратов: rels().length, после_ответа: after.length };
    await noKbd(); await slotClick();
    await wp.keyboard.type("Позвонить"); await wp.keyboard.press("Enter"); await sleep(1800);
    const ask2 = asks()[0], after2 = rels().filter(r => ask2 && ask2.ответ && r.t >= ask2.ответ);
    out.enter = { запросов: asks().length, возвратов: rels().length, после_ответа: after2.length, сохранено: (readData().reminders || []).some(r => r.text === "Позвонить") };
    kbdDelay = 0;
    const ok = out.закрыта_мышью.запросов === 1 && out.закрыта_мышью.ответ_через_мс >= 850 && out.закрыта_мышью.после_ответа === 1 && out.закрыта_мышью.возвратов === 1
      && out.enter.запросов === 1 && out.enter.после_ответа >= 1 && out.enter.сохранено;
    assert(ok, "FAIL-сценарий 1.3.10 (поздний ответ оставляет клавиатуру у обоев): " + JSON.stringify(out));
    return out;
  });

  await browser.close();
  try { cp.execSync(`taskkill /PID ${srv.pid} /T /F`, { stdio: "ignore" }); } catch {}
  const ok = results.filter(r => r.ok).length;
  if (errs.length) console.log("ошибки страницы:", errs.slice(0, 5));
  fs.writeFileSync(RES, JSON.stringify({ исходники: SRC, когда: new Date().toISOString(), results, errs }, null, 1));
  await sleep(500);
  try { cp.execFileSync((process.env.PY || path.join(DEV, ".venv", "Scripts", "python.exe")), [path.join(__dirname, "в_корзину.py"), T], { stdio: "ignore" }); } catch {}
  console.log(`ИТОГ: ${ok}/${results.length}`);
  process.exit(ok === results.length && !errs.length ? 0 : 1);
})().catch(e => { console.error("ОШИБКА", e); try { cp.execSync(`taskkill /PID ${srv.pid} /T /F`, { stdio: "ignore" }); } catch {} process.exit(2); });
