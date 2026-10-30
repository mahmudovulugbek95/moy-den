// Сквозная проверка дашборда в безголовом Chrome (puppeteer + системный Chrome).
// Запуск: node "tests\e2e.js"   (из папки репозитория)
// Работает на КОПИИ data.json, тестовом календаре и подменённой погоде; настоящие данные не трогает.
// Итог: tests\e2e-результат.json, снимки — в папке «скриншоты».
const PUPPETEER = process.env.PUPPETEER_PATH || "puppeteer-core";
process.env.MOYDEN_TZ = process.env.MOYDEN_TZ || "Africa/Nairobi"; // сервер считает дни в том же поясе, что и страница (emulateTimezone)
const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const puppeteer = require(PUPPETEER);
const fs = require("fs"), path = require("path"), http = require("http"), cp = require("child_process"), os = require("os");
const FX = require("./фикстуры_по_дате.js"), D = FX.D; // даты фикстур — от сегодняшнего дня, шаги не зависят от даты прогона

const DASH = path.resolve(__dirname, "..");
const SHOTS = path.join(DASH, "скриншоты");
const T = fs.mkdtempSync(path.join(os.tmpdir(), "moyden-e2e-"));
const PORT = 8650, ICS_PORT = 8651, WX_PORT = 8652, SECRET = "private-E2E-SECRET-91b2";
const BASE = `http://127.0.0.1:${PORT}/`;
const PY = (process.env.PY || path.join(DASH, ".venv", "Scripts", "python.exe"));
fs.mkdirSync(SHOTS, { recursive: true });
fs.writeFileSync(path.join(T, "data.json"), FX.dataJson(fs.readFileSync(path.join(__dirname, "fixture-data.json"), "utf8")));  // своя фикстура, даты от сегодня
fs.copyFileSync(path.join(__dirname, "fixture-settings.json"), path.join(T, "settings.json"));  // фикстура, не настройки пользователя
fs.writeFileSync(path.join(T, "cal.json"), JSON.stringify({ calendars: [
  { name: "Рабочий", color: "#4a6cf7", ical_url: `http://127.0.0.1:${ICS_PORT}/${SECRET}/basic.ics` },
  { name: "Личный", color: "#0b8f7f", ical_url: "" }] }));
const ICS = FX.ics(fs.readFileSync(path.join(__dirname, "test-calendar.ics"), "utf8"));
let icsUp = true, wxUp = true;
const icsSrv = http.createServer((q, r) => {
  if (icsUp && q.url === `/${SECRET}/basic.ics`) { r.writeHead(200, { "Content-Type": "text/calendar" }); r.end(ICS); }
  else { r.socket.destroy(); }
}).listen(ICS_PORT);
const tdy = () => new Intl.DateTimeFormat("sv-SE", { timeZone: "Africa/Nairobi" }).format(new Date());
const wxSrv = http.createServer((q, r) => {   // подмена Open-Meteo
  if (!wxUp) return r.socket.destroy();
  r.writeHead(200, { "Content-Type": "application/json" });
  r.end(JSON.stringify({ utc_offset_seconds: 10800, current: { temperature_2m: 23.6, weather_code: 2, is_day: 1 },
    daily: { time: [tdy()], weather_code: [2], temperature_2m_max: [26.9], temperature_2m_min: [14.1], precipitation_probability_max: [10] } }));
}).listen(WX_PORT);

let srv = null;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const readData = () => JSON.parse(fs.readFileSync(path.join(T, "data.json"), "utf8").replace(/^\uFEFF/, ""));
const task = id => readData().tasks.find(x => x.id === id);
async function ping() { try { const r = await fetch(BASE + "api/ping"); return r.ok; } catch { return false; } }
async function startServer() {
  srv = cp.spawn(PY, [path.join(DASH, "server.py"), "--port", String(PORT), "--data", path.join(T, "data.json"),
    "--calendars", path.join(T, "cal.json"), "--cache", path.join(T, "cache"), "--refresh", "2", "--log", path.join(T, "server.log"),
    "--settings", path.join(T, "settings.json"), "--weather-url", `http://127.0.0.1:${WX_PORT}/v1/forecast`, "--weather-cache", path.join(T, "wx.json"),
    "--weather-refresh", "2", "--weather-retry", "2", "--weather-max-age", "6", "--google-sync", path.join(T, "google-events.json")], { stdio: "ignore", env: { ...process.env, MOYDEN_TEST_ICAL: "1" } });  // тестовый календарь на 127.0.0.1
  for (let i = 0; i < 60; i++) { if (await ping()) return; await sleep(200); }
  throw new Error("сервер не поднялся");
}
function stopServer() { if (srv) { try { cp.execSync(`taskkill /PID ${srv.pid} /T /F`, { stdio: "ignore" }); } catch {} srv = null; } }

const results = [];
async function step(name, fn) {
  const t0 = Date.now();
  try { const info = await fn(); results.push({ step: name, ok: true, ms: Date.now() - t0, info: info ?? null }); console.log("PASS", name, info ? JSON.stringify(info).slice(0, 300) : ""); }
  catch (e) { results.push({ step: name, ok: false, ms: Date.now() - t0, error: String(e && e.message || e) }); console.log("FAIL", name, String(e && e.message || e)); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }
async function waitFor(fn, ms = 8000, msg = "не дождался") { const t = Date.now(); while (Date.now() - t < ms) { if (await fn()) return; await sleep(100); } throw new Error(msg); }

(async () => {
  await startServer();
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--window-size=1600,900", "--lang=ru-RU"] });
  await require("./страж_localhost.js").install(browser, __filename);
  const page = await browser.newPage();
  const HOUR = process.env.MOYDEN_E2E_HOUR; // прогон «при сдвинутых часах» — на странице HOUR:00 того же дня
  if (HOUR) { const t = new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Nairobi", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date()).split(":");
    await page.evaluateOnNewDocument(off => { const R = Date; class Dt extends R { constructor(...a) { if (a.length) super(...a); else super(R.now() + off); } static now() { return R.now() + off; } } window.Date = Dt; },
      ((+HOUR - +t[0]) * 60 - +t[1]) * 60000); }
  { // клик по сроку в календаре открывает всплывашку срока; «Открыть задачу в проекте» ведёт в проект
    const raw = page.click.bind(page);
    page.click = async (sel, o) => { await raw(sel, o); if (/data-task|\.ev\.task|\.tk/.test(sel)) { await sleep(200); if (await page.$("#calPop:popover-open [data-open-task]")) await raw("#calPop [data-open-task]"); } };
  }
  await page.setViewport({ width: 1600, height: 900 });
  await page.emulateTimezone("Africa/Nairobi");
  const consoleErrors = [];
  page.on("console", m => { if (m.type() === "error" && !/ERR_CONNECTION_REFUSED|Failed to load resource/.test(m.text())) consoleErrors.push(m.text()); });
  page.on("pageerror", e => consoleErrors.push(String(e)));
  await page.evaluateOnNewDocument(() => {
    window.__cls = 0; window.__lcp = 0; window.__inp = 0; let win = 0, first = 0, last = 0;
    new PerformanceObserver(l => { for (const e of l.getEntries()) { if (e.hadRecentInput) continue;
      if (win && e.startTime - last < 1000 && e.startTime - first < 5000) { win += e.value; } else { win = e.value; first = e.startTime; }
      last = e.startTime; window.__cls = Math.max(window.__cls, win);
      if (e.value > 0.01) (window.__shifts = window.__shifts || []).push({ v: +e.value.toFixed(4), t: Math.round(e.startTime), src: (e.sources || []).map(x => x.node && x.node.nodeType === 1 ? (x.node.id || String(x.node.className).slice(0, 30) || x.node.tagName) : "?").slice(0, 4) }); } }).observe({ type: "layout-shift", buffered: true });
    new PerformanceObserver(l => { for (const e of l.getEntries()) window.__lcp = e.startTime; }).observe({ type: "largest-contentful-paint", buffered: true });
    new PerformanceObserver(l => { for (const e of l.getEntries()) if (e.interactionId) window.__inp = Math.max(window.__inp, e.duration); }).observe({ type: "event", buffered: true, durationThreshold: 16 });
  });
  const $eval = (s, f, ...a) => page.$eval(s, f, ...a);
  const count = s => page.$$eval(s, els => els.length);
  const screen = () => page.evaluate(() => ({ home: !document.getElementById("home").hidden, pv: !document.getElementById("pv").hidden, hash: location.hash }));
  const openProject = async pid => { if (await page.$eval("#home", e => e.hidden)) { await page.evaluate(() => { location.hash = ""; }); await page.waitForSelector("#home:not([hidden])"); await sleep(400); } await page.click(`.proj[data-pid="${pid}"]`); await page.waitForSelector("#pv:not([hidden])"); await sleep(700); };
  const toHome = async () => { await page.keyboard.press("Escape"); await page.waitForSelector("#home:not([hidden])"); await sleep(500); };
  // вид календаря с нужным днём — «Сегодня», затем листание на столько недель или месяцев, сколько до дня d
  const calTo = async (view, d) => {
    await page.click(`.seg button[data-v="${view}"]`); await sleep(300); await page.click("#calToday"); await sleep(300);
    const k = view === "week" ? FX.weeksFromToday(d) : view === "month" ? FX.monthsFromToday(d) : FX.days(d, FX.today);
    for (let i = 0; i < Math.abs(k); i++) { await page.click(k < 0 ? "#calPrev" : "#calNext"); await sleep(250); }
    await sleep(700);
  };
  // сроки задач — одной плашкой «Задачи: N» в полосе «весь день» (в «Месяце» — в клетке); задачи дня — список по нажатию на плашку
  const plateOf = d => page.evaluate(d => { const b = document.querySelector(`#calBody [data-tday="${d}"]`); if (!b) return null; const cs = getComputedStyle(b);
    return { t: b.textContent.trim(), n: +b.dataset.n, late: b.classList.contains("late"), pz: b.classList.contains("pz"), полоса: !!b.closest(".dwall .ad, .mitems"), bg: cs.backgroundColor, fg: cs.color }; }, d);
  const dayList = async d => {
    await page.click(`#calBody [data-tday="${d}"]`); await page.waitForSelector("#lsv:not([hidden])"); await sleep(450);
    const rows = await page.$$eval("#lsBody .lrow", els => els.map(r => ({ id: +r.dataset.id, t: r.innerText.replace(/\s+/g, " ").trim(), late: r.querySelector(".dot").classList.contains("late"),
      pause: r.querySelector(".dot").classList.contains("pause"), done: r.classList.contains("done"), strike: getComputedStyle(r.querySelector(".t1")).textDecorationLine.includes("line-through"),
      cm: (r.querySelector('.chip[title^="Комментариев"]') || {}).title || "" })));
    await page.click("#lsv [data-back]"); await page.waitForSelector("#home:not([hidden])"); await sleep(450);
    return rows;
  };
  const dayTasks = async d => {  // строки списка дня; число на плашке = числу строк
    const p = await plateOf(d); if (!p) return [];
    const rows = await dayList(d);
    assert(p.полоса && p.n === rows.length && p.t === "Задачи: " + rows.length, `плашка ${d}: ${JSON.stringify(p)} ≠ ${rows.length} строк`);
    return rows;
  };

  await step("главный экран: шапка с погодой, днём недели и временем; 5 счётчиков; нет «Кто чем занят»", async () => {
    await page.goto(BASE, { waitUntil: "networkidle0" });
    await page.waitForSelector("#projects .proj");
    await waitFor(() => page.$eval("#wx", e => !e.hidden), 9000, "погода не появилась");
    const h = await page.evaluate(() => ({ wday: wday.textContent, wdaySize: parseFloat(getComputedStyle(wday).fontSize), clock: clock.textContent, wx: wx.innerText.replace(/\s+/g, " "), icon: !!wx.querySelector("svg"), kpis: [...document.querySelectorAll("#sum .kpi span")].map(s => s.textContent) }));
    assert(/^(Понедельник|Вторник|Среда|Четверг|Пятница|Суббота|Воскресенье)$/.test(h.wday) && h.wdaySize >= 28, "день недели крупно: " + JSON.stringify(h));
    assert(/^\d\d:\d\d$/.test(h.clock), "время");
    assert(h.icon && h.wx.includes("24°") && h.wx.includes("Днём до 27°, ночью до 14°") && h.wx.includes("вероятность осадков 10%"), "погода: " + h.wx);
    assert(JSON.stringify(h.kpis) === JSON.stringify(["просрочено", "на сегодня", "ждём ответа", "без срока", "на паузе"]), "счётчики: " + h.kpis);
    const txt = await page.evaluate(() => document.body.innerText);
    assert(!txt.includes("Кто чем занят") && !/перегруз/.test(txt), "карточка осталась");
    const projH = await $eval("#projects", el => el.closest("section").getBoundingClientRect().height);
    const col = await $eval("#hot", el => el.closest(".col").getBoundingClientRect().height);
    assert(Math.abs(projH - col) < 2, `проекты не на всю высоту: ${projH} vs ${col}`);
    const prioTop = await page.evaluate(() => document.querySelector(".col section").contains(document.getElementById("prio")));
    assert(prioTop, "«Приоритетные» не первый блок левой колонки");
    return h;
  });
  await sleep(1200);
  await page.screenshot({ path: path.join(SHOTS, "13-главный-с-погодой.png") });

  await step("календарь «День»: события Google и сроки задач, 07:00–21:00", async () => {
    await waitFor(() => page.$$eval("#calBody .ev", e => e.length > 0), 8000, "события не появились");
    const evs = await page.$$eval("#calBody .ev b", els => els.map(e => e.textContent));
    assert(evs.includes("Созвон с Токио") && evs.includes("Встреча с клиентом") && !evs.includes("Отменено"), "события: " + evs);
    assert((await count("#calBody .now")) === 1, "нет линии «сейчас»");
    assert((await count("#calBody .dcol [data-task], #calBody .dcol .ev.task")) === 0, "сроки задач остались на сетке часов");
    assert((await count(`#calBody .dwall [data-tday="${FX.today}"]`)) === 1 && (await count("#calBody .dwall [data-tday]")) === 1, "в полосе «весь день» не одна плашка «Задачи: N»");
    const ov = await page.$$eval("#calBody .ev", els => els.filter(e => /Созвон с Токио|Встреча с клиентом/.test(e.textContent)).map(e => e.style.getPropertyValue("--w")));
    assert(ov.length === 2 && ov.every(w => w === "0.5"), "пересекающиеся события не в двух колонках: " + ov);
    const v = await page.evaluate(() => { const s = document.querySelector(".dwscroll"), hh = parseFloat(document.querySelector(".dw").style.getPropertyValue("--hh")); return { a: s.scrollTop / hh, b: (s.scrollTop + s.clientHeight) / hh }; });
    assert(v.b - v.a >= 8 && v.a <= 13.7 + 0.5, "час не меньше 48 px, сетка прокручивается; видно не меньше 8 часов вокруг «сейчас»: " + JSON.stringify(v));
    const tk = await dayTasks(FX.today);  // срок без времени — в списке дня по плашке, с проектом и исполнителем
    assert(tk.some(t => t.t.includes("Встреча по брендбуку") && t.t.includes("Брендбук") && t.t.includes("Борис")), "срок «Встреча по брендбуку · Брендбук · Борис» не в списке дня: " + JSON.stringify(tk));
    return { события: evs.length, видно: v, задач_дня: tk.length };
  });

  await step("всплывашка события: время, место, описание; «Только в календаре»", async () => {
    await calTo("week", D("2026-10-22"));
    await page.evaluate(() => { for (const b of document.querySelectorAll("#calBody .ev")) if (b.textContent.includes("Звонок из Лагоса")) { b.click(); return; } });
    await sleep(300);
    const txt = await $eval("#calPop", e => e.matches(":popover-open") ? e.innerText : "");
    assert(txt.includes("Звонок из Лагоса") && txt.includes("17:00–18:00") && txt.includes("Только в календаре"), "всплывашка: " + txt);
    await page.keyboard.press("Escape"); await sleep(250);
    assert(!(await $eval("#calPop", e => e.matches(":popover-open"))), "Esc не закрыл");
    await calTo("day", FX.today);
    await page.evaluate(() => { for (const b of document.querySelectorAll("#calBody .ev")) if (b.textContent.includes("Созвон с Токио")) { b.click(); return; } });
    await sleep(350);
    const t2 = await $eval("#calPop", e => e.innerText);
    assert(t2.includes("Google Meet") && t2.includes("11:00–12:00"), "место/время: " + t2);
    await page.screenshot({ path: path.join(SHOTS, "06-всплывашка-события.png") });
    await page.keyboard.press("Escape");
    return { ok: true };
  });

  await step("«Неделя»: задачи по дням, повтор с исключением", async () => {
    await calTo("week", D("2026-10-21"));
    assert((await dayTasks(D("2026-10-21"))).some(x => x.t.includes("проверить вёрстку") && x.late), "позавчера (" + FX.ddmm("2026-10-21") + "): просроченная не красная");
    await calTo("week", D("2026-10-22"));
    assert((await dayTasks(D("2026-10-22"))).some(x => x.t.includes("ZETA") && x.t.includes("Разбор референсов") && x.late), "вчера: нет ZETA");
    await calTo("week", D("2026-10-23"));
    assert((await dayTasks(D("2026-10-23"))).some(x => x.t.includes("Встреча по брендбуку") && !x.late), "сегодня: нет «Встреча по брендбуку»");
    await calTo("week", D("2026-10-19"));
    assert(!(await page.$eval(`#calBody .dcol[data-d="${D("2026-10-19")}"]`, e => e.innerText)).includes("Стендап"), FX.ddmm("2026-10-19") + ": исключение повтора показано");
    await calTo("week", FX.today);
    await page.screenshot({ path: path.join(SHOTS, "04-календарь-неделя.png") });
    return { ok: true };
  });

  await step("«Месяц»: задачи и события в клетках", async () => {
    const cell = async d => { await calTo("month", d); return page.$eval(`[data-cell="${d}"]`, e => [...e.querySelectorAll(".tk,.it,.pl")].filter(b => !b.hidden).map(b => ({ c: b.className, t: b.textContent.trim() }))); };
    const c21 = await cell(D("2026-10-21")), c24 = await cell(D("2026-10-24")), c26 = await cell(D("2026-10-26"));
    await calTo("month", FX.today);
    assert(c21.length && c21[0].c.includes("tks") && c21[0].c.includes("late") && /^Задачи: \d+$/.test(c21[0].t), "21.10: первой строкой клетки — красная плашка «Задачи: N»: " + JSON.stringify(c21));  // 1.3.11 Д4
    assert(c24.some(x => x.c.includes("pl") && x.t.includes("День рождения")) && c24.some(x => x.t.includes("до 02:00")), "24.10");
    assert(c26.some(x => x.t.includes("12:00") && x.t.includes("перенесён")), "26.10");
    await page.screenshot({ path: path.join(SHOTS, "05-календарь-месяц-с-задачами.png") });
    return { ok: true };
  });

  await step("календарь на весь экран — свой экран с «Назад», Esc сворачивает", async () => {
    await page.click("#calFull"); await sleep(500);
    const v = await page.evaluate(() => ({ w: document.querySelector(".cal").getBoundingClientRect().width, hash: location.hash, back: getComputedStyle(document.querySelector(".calbar .backbtn")).display }));
    assert(v.w > 1400 && v.hash === "#/cal" && v.back !== "none", JSON.stringify(v));
    await page.screenshot({ path: path.join(SHOTS, "05б-календарь-месяц-развёрнут.png") });
    await page.keyboard.press("Escape"); await sleep(500);
    assert((await $eval(".cal", e => e.getBoundingClientRect().width)) < 800, "Esc не свернул");
    return v;
  });

  await step("задача в календаре → проект с подсветкой", async () => {
    await calTo("month", D("2026-10-22"));
    await page.click(`[data-cell="${D("2026-10-22")}"] [data-tday]`); await page.waitForSelector("#lsv:not([hidden])"); await sleep(400);  // плашка → список дня
    await page.evaluate(() => { for (const a of document.querySelectorAll("#lsBody .lrow a.task")) if (a.innerText.includes("Кедр")) { a.click(); return; } });
    await page.waitForSelector("#pv:not([hidden])"); await sleep(900);
    const name = await $eval("#pvName", e => e.value);
    const flashed = await page.$$eval("#openList .trow.flash .ttl", els => els.map(e => e.value));
    assert(name === "Кедр" && flashed.includes("Разбор референсов"), name + " " + flashed);
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    await page.keyboard.press("Escape"); await page.waitForSelector("#lsv:not([hidden])", { timeout: 5000 }); await sleep(400);  // из проекта — назад в список дня
    await toHome();
    return { проект: name };
  });

  await step("клик по проекту → экран (заголовок, этап, статус, ответственный), фокус в «Добавить задачу»", async () => {
    await openProject("portal");
    const v = await page.evaluate(() => ({ name: pvName.value, stage: pvStage.value, status: pvStatus.value, owner: pvOwner.value, focus: document.activeElement.id, back: document.querySelector("#pv .backbtn").getBoundingClientRect().height, backTo: document.querySelector("#pv .backto").textContent }));
    assert(v.name === "Магазин Портал" && v.stage === "вёрстка каталога" && v.status === "ok" && v.owner === "Борис", JSON.stringify(v));
    assert(v.focus === "atTitle", "фокус");
    assert(v.back >= 40 && v.backTo === "к проектам", "кнопка «Назад»: " + JSON.stringify(v));
    return v;
  });

  let newId = null;
  await step("добавить задачу (Enter) с исполнителем из справочника → в data.json", async () => {
    await page.type("#atTitle", "Проверить макеты по замечаниям");
    await page.$eval("#atWho", e => { e.value = ""; }); await page.type("#atWho", "Влад");
    await page.$eval("#atDue", (e, d) => { e.value = d; }, D("2026-10-28"));
    await page.focus("#atTitle"); await page.keyboard.press("Enter");
    await waitFor(() => page.$$eval("#openList .ttl", els => els.some(e => e.value === "Проверить макеты по замечаниям")));
    const t = readData().tasks.find(x => x.title === "Проверить макеты по замечаниям");
    assert(t && t.whoId === 4 && t.who === "Влад" && t.due === D("2026-10-28") && t.state === "work", JSON.stringify(t));
    newId = t.id;
    await page.type("#atTitle", "Собрать комплект документов на подпись"); await page.keyboard.press("Enter");
    await waitFor(() => page.$$eval("#openList .ttl", els => els.some(e => e.value === "Собрать комплект документов на подпись")));
    return { id: t.id, nextId: readData().nextId };
  });
  await sleep(1500); await page.mouse.move(800, 600);
  await page.screenshot({ path: path.join(SHOTS, "02-экран-проекта.png") });

  await step("отметить выполненной → зачёркнута, «Выполненные», data.json done", async () => {
    await page.click(`#openList .trow[data-id="${newId}"] .ck`);
    await waitFor(() => page.$$eval(`#doneList .trow[data-id="${newId}"]`, e => e.length === 1));
    assert((await page.$eval(`#doneList .trow[data-id="${newId}"] .ttl`, e => getComputedStyle(e).textDecorationLine)).includes("line-through"), "не зачёркнута");
    assert(task(newId).state === "done" && task(newId).done, "data.json");
    await page.click("#doneBox summary"); await sleep(300);
    await page.screenshot({ path: path.join(SHOTS, "03-выполненные.png") });
    return { done: task(newId).done };
  });

  await step("вернуть из выполненных", async () => {
    await page.click(`#doneList .trow[data-id="${newId}"] .ck`);
    await waitFor(() => page.$$eval(`#openList .trow[data-id="${newId}"]`, e => e.length === 1));
    assert(task(newId).state === "work" && !("done" in task(newId)), "data.json");
    return { state: "work" };
  });

  await step("правка по клику: название, исполнитель (поиск), состояние, срок, время; Esc отменяет", async () => {
    const sel = `#openList .trow[data-id="${newId}"]`;
    await page.click(`${sel} .ttl`); await page.keyboard.down("Control"); await page.keyboard.press("a"); await page.keyboard.up("Control");
    await page.keyboard.type("Проверить макеты и иконки"); await page.keyboard.press("Enter");
    await waitFor(() => task(newId).title === "Проверить макеты и иконки", 5000, "название");
    await page.click(`${sel} input.who`, { clickCount: 3 }); await page.keyboard.type("Нина"); await page.keyboard.press("Tab");
    await waitFor(() => task(newId).whoId === 2, 5000, "исполнитель");
    await page.select(`${sel} select[data-f="state"]`, "wait");
    await waitFor(() => task(newId).state === "wait", 5000, "состояние");
    await page.$eval(`${sel} input.pick`, (e, d) => { e.value = d; e.dispatchEvent(new Event("change", { bubbles: true })); }, D("2026-10-27"));
    await waitFor(() => task(newId).due === D("2026-10-27"), 5000, "срок");
    await page.waitForSelector(`${sel} input[data-f="time"]`);
    await page.$eval(`${sel} input[data-f="time"]`, e => { e.value = "15:30"; e.dispatchEvent(new Event("change", { bubbles: true })); });
    await waitFor(() => task(newId).time === "15:30", 5000, "время");
    // FAIL: несуществующий исполнитель не сохраняется
    await page.click(`${sel} input.who`, { clickCount: 3 }); await page.keyboard.type("Никто Никтович"); await page.keyboard.press("Tab");
    await sleep(500);
    assert(task(newId).whoId === 2, "сохранён несуществующий исполнитель");
    await page.click(`${sel} .ttl`); await page.keyboard.type(" ЛИШНЕЕ"); await page.keyboard.press("Escape"); await sleep(300);
    assert((await page.$eval(`${sel} .ttl`, e => e.value)) === "Проверить макеты и иконки", "Esc не отменил");
    assert(await page.$eval("#pv", e => !e.hidden), "Esc в поле не должен уводить назад");
    return task(newId);
  });

  await step("главный экран и календарь сразу учитывают изменения", async () => {
    await toHome();
    assert((await $eval("#wait", e => e.innerText)).includes("Проверить макеты и иконки"), "нет в «Ждём ответа»");
    await calTo("month", D("2026-10-27"));
    const l27 = await dayTasks(D("2026-10-27"));  // клетка «Месяца» — плашка, задача с временем — в списке дня
    assert(l27.some(t => t.t.includes("Проверить макеты") && t.t.includes("к 15:30")), "месяц, " + FX.ddmm("2026-10-27") + ": " + JSON.stringify(l27));
    await calTo("day", D("2026-10-27"));
    const p27 = await plateOf(D("2026-10-27"));
    assert(p27 && p27.n === l27.length && (await count("#calBody .dcol [data-task]")) === 0, "день " + FX.ddmm("2026-10-27") + ": плашка " + JSON.stringify(p27) + " или срок на сетке");
    return { ok: true };
  });

  await step("выполненная в календаре зачёркнута, переключатель скрывает", async () => {
    const d27 = D("2026-10-27");
    await calTo("week", d27);
    const n0 = (await plateOf(d27)).n;
    await page.evaluate(id => fetch(`/api/tasks/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state: "done" }) }), newId);
    await sleep(4800);
    const p1 = await plateOf(d27);
    assert(p1 && p1.n === n0, "«Показывать выполненные» включено — выполненная остаётся в счёте плашки: " + JSON.stringify({ n0, p1 }));  // 1.3.11 Д4
    const r1 = (await dayTasks(d27)).find(x => x.t.includes("Проверить макеты"));
    assert(r1 && r1.done && r1.strike && r1.t.includes("выполнено") && r1.t.includes("Вернуть"), "нет вида «выполнена» (зачёркнута, «выполнено», «Вернуть»): " + JSON.stringify(r1));
    await calTo("week", d27);
    await page.click("#calDone"); await sleep(300);
    const p2 = await plateOf(d27);
    assert((p2 ? p2.n : 0) === n0 - 1, "переключатель «Показывать выполненные» не убрал выполненную из плашки: " + JSON.stringify({ n0, p2 }));
    if (p2) assert(!(await dayTasks(d27)).some(x => x.t.includes("Проверить макеты")), "переключатель: выполненная осталась в списке дня");
    await calTo("week", d27);
    await page.click("#calDone");
    await page.evaluate(id => fetch(`/api/tasks/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state: "work" }) }), newId);
    await sleep(4500);
    return { ok: true };
  });

  await step("удалить → «Вернуть» (номер из ответа сервера) → снова удалить; номер не переиспользуется", async () => {
    await openProject("portal");
    const sel = `#openList .trow[data-id="${newId}"]`;
    await page.hover(sel); await page.click(`${sel} .del`);
    await waitFor(() => page.$$eval(sel, e => e.length === 0));
    assert(!task(newId), "не удалена");
    assert((await $eval("#toast", e => e.innerText)).includes("Вернуть"), "нет «Вернуть»");
    await page.hover("#toast"); await sleep(400);
    await page.click("#toast button");
    await waitFor(() => page.$$eval(sel, e => e.length === 1));
    assert(task(newId).time === "15:30", "вернулась не та");
    await page.hover(sel); await page.click(`${sel} .del`);
    await waitFor(() => page.$$eval(sel, e => e.length === 0));
    const r = await page.evaluate(() => fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ project: "portal", title: "Новая после удаления" }) }).then(r => r.json()));
    assert(r.item.id !== newId, "номер удалённой задачи выдан новой");
    await page.evaluate(id => fetch(`/api/tasks/${id}`, { method: "DELETE", headers: { "Content-Type": "application/json" } }), r.item.id);
    return { удалена: newId, новыйНомер: r.item.id };
  });

  await step("звезда: ☆ в проекте → блок «Приоритетные» (просроченные красным сверху), клик → задача, снять звезду в блоке", async () => {
    await page.click('#openList .trow[data-id="90"] .star');
    await waitFor(() => task(90).star === true, 5000, "star в data.json");
    await toHome();
    await page.evaluate(() => Promise.all([1, 92].map(id => fetch(`/api/tasks/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ star: true }) }))));
    await waitFor(() => page.$$eval("#prio .prow", e => e.length === 3), 7000, "блок не обновился");
    const rows = await page.$$eval("#prio .prow", els => els.map(e => ({ t: e.querySelector(".t1").textContent, late: e.classList.contains("late"), color: getComputedStyle(e.querySelector(".t1")).color })));
    assert(rows[0].t.includes("проверить вёрстку") && rows[0].late && rows[1].t === "Встреча по брендбуку" && rows[2].t.includes("шрифт заголовков"), "порядок: " + JSON.stringify(rows));
    assert(rows[0].color === "rgb(179, 54, 47)", "просроченная не красная: " + rows[0].color);
    await page.screenshot({ path: path.join(SHOTS, "14-приоритетные.png") });
    await page.click("#prio .prow:nth-child(2) a.task");
    await page.waitForSelector("#pv:not([hidden])"); await sleep(900);
    assert((await $eval("#pvName", e => e.value)) === "Брендбук", "не тот проект");
    await toHome();
    await page.$eval('#prio [data-unstar="92"]', e => e.closest('.prow').scrollIntoView({ block: 'nearest' })); await sleep(300);  // третья строка блока под строкой «Показать ещё» — сначала прокрутить к ней, как человек
    await page.click('#prio [data-unstar="92"]');
    await waitFor(() => page.$$eval("#prio .prow", e => e.length === 2), 5000, "звезда не снята в блоке");
    assert(!task(92).star, "data.json star");
    await page.evaluate(() => fetch("/api/tasks/1", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state: "done" }) }));
    await waitFor(() => page.$$eval("#prio .prow", e => e.length === 1), 7000, "выполненная не исчезла");
    await page.evaluate(() => fetch("/api/tasks/1", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state: "work" }) }));
    await sleep(4500);
    return rows;
  });

  await step("пауза проекта: переключатель + причина → внизу «Проектов», бледный, «пауза»; не в «Срочных» и не красный в календаре", async () => {
    const before = await page.evaluate(() => [...document.querySelectorAll("#sum .kpi b")].map(b => b.textContent));
    await openProject("kedr");
    await page.click("#pvPause");
    await waitFor(() => readData().projects.find(p => p.id === "kedr").pause, 5000, "pause в data.json");
    await page.waitForSelector("#pvPausePanel:not([hidden])");
    await page.type("#pvPzReason", "ждём заказчика"); await page.keyboard.press("Enter");
    await waitFor(() => readData().projects.find(p => p.id === "kedr").pause.reason === "ждём заказчика", 5000, "причина");
    await sleep(500);
    await page.screenshot({ path: path.join(SHOTS, "15-проект-на-паузе.png") });
    await toHome();
    const v = await page.evaluate(() => ({ last: [...document.querySelectorAll("#projects .proj")].pop().dataset.pid, cls: document.querySelector('.proj[data-pid="kedr"]').className, txt: document.querySelector('.proj[data-pid="kedr"]').innerText, hot: document.getElementById("hot").innerText, kpi: document.getElementById("kpiPause").title, late: document.querySelector("#sum .kpi b").textContent }));
    assert(v.last === "kedr" && v.cls.includes("paused") && v.txt.includes("на паузе") && v.txt.includes("ждём заказчика"), JSON.stringify(v));
    assert(!/Кедр/.test(v.hot), "задача проекта на паузе в «Срочных»");
    assert(v.kpi.includes("1 проект и 0 задач"), "счётчик: " + v.kpi);
    assert(+v.late === +before[0] - 1, `«просрочено» не уменьшилось: ${before[0]} → ${v.late}`);
    await calTo("week", D("2026-10-22"));
    const k = (await dayTasks(D("2026-10-22"))).filter(x => x.t.includes("Кедр"));  // список дня по плашке
    assert(k.length && k.every(x => x.pause && !x.late && x.t.includes("на паузе") && !/просрочено/.test(x.t)), "в календаре (список дня): " + JSON.stringify(k));
    await page.screenshot({ path: path.join(SHOTS, "16-счётчик-паузы.png") });
    return v;
  });

  await step("пауза задачи ⏸ → счётчик «1 проект · 1 задача» → список «На паузе» → снять одним кликом; история хранится", async () => {
    await openProject("zeta");
    await page.hover('#openList .trow[data-id="2"]');
    await page.click('#openList .trow[data-id="2"] .pzt');
    await waitFor(() => task(2).pause, 5000, "pause задачи");
    await page.waitForSelector('#openList .trow[data-id="2"] .pzb');
    await toHome();
    assert((await $eval("#kpiPause", e => e.title + "|" + e.querySelector("b").textContent)) === "На паузе: 1 проект и 1 задача|2", "счётчик");
    await page.click("#kpiPause");
    await page.waitForSelector("#pzv:not([hidden])"); await sleep(300);
    const list = await $eval("#pzv", e => e.innerText);
    assert(list.includes("Кедр") && list.includes("Разбор референсов") && list.includes("ждём заказчика"), "список: " + list);
    await page.screenshot({ path: path.join(SHOTS, "17-список-на-паузе.png") });
    await page.click('[data-resume-t="2"]');
    await waitFor(() => !task(2).pause && task(2).pauseHistory && task(2).pauseHistory.length === 1, 5000, "снятие паузы");
    await page.click("#pzv .backbtn");
    await page.waitForSelector("#home:not([hidden])"); await sleep(300);
    return { history: task(2).pauseHistory };
  });

  await step("счётчики страницы = /api/summary (одна логика для страницы и клиента API)", async () => {
    const s = await (await fetch(BASE + "api/summary")).json();
    const k = await page.evaluate(() => [...document.querySelectorAll("#sum .kpi b")].slice(0, 4).map(b => +b.textContent));
    const c = s.counts;
    assert(JSON.stringify(k) === JSON.stringify([c.late, c.today, c.wait, c.noDue]), `страница ${k} ≠ сервер ${JSON.stringify(c)}`);
    assert((await count("#prio .prow")) === c.priority, "приоритетные");
    return c;
  });

  await step("назад: кнопка, Esc, Alt+←, Alt+→, кнопка мыши, логотип; Backspace в поле НЕ уводит (FAIL-сценарий)", async () => {
    await openProject("lumen"); await page.click("#pv .backbtn"); await page.waitForSelector("#home:not([hidden])"); await sleep(400);
    await openProject("lumen"); await page.keyboard.down("Alt"); await page.keyboard.press("ArrowLeft"); await page.keyboard.up("Alt");
    await page.waitForSelector("#home:not([hidden])"); await sleep(400);
    const afterBack = await page.evaluate(() => ({ hash: location.hash, len: history.length, d: history.state && history.state.d }));
    await page.keyboard.down("Alt"); await page.keyboard.press("ArrowRight"); await page.keyboard.up("Alt");
    await page.waitForSelector("#pv:not([hidden])", { timeout: 4000 }).catch(async () => { throw new Error("Alt+→ не вернул в проект; после Alt+←: " + JSON.stringify(afterBack) + " сейчас: " + JSON.stringify(await screen())); }); await sleep(600);
    assert((await $eval("#pvName", e => e.value)) === "LUMEN", "Alt+→");
    await page.focus("#atTitle"); await page.keyboard.type("abc"); await page.keyboard.press("Backspace"); await sleep(400);
    const s1 = await screen();
    assert(s1.pv && (await $eval("#atTitle", e => e.value)) === "ab", "Backspace в поле увёл назад");
    await page.$eval("#atTitle", e => { e.value = ""; e.blur(); }); await page.focus("#pv .backbtn");
    await page.keyboard.press("Backspace"); await page.waitForSelector("#home:not([hidden])"); await sleep(400);
    await openProject("lumen");
    await page.mouse.move(900, 500);
    try { await page.mouse.click(900, 500, { button: "back" }); } catch { await page.evaluate(() => document.dispatchEvent(new MouseEvent("mouseup", { button: 3, bubbles: true }))); }
    await page.waitForSelector("#home:not([hidden])", { timeout: 4000 }); await sleep(400);
    await page.evaluate(() => { for (const a of document.querySelectorAll("#hot a.task")) if (a.textContent.includes("ZETA")) { a.click(); return; } });
    await page.waitForSelector("#pv:not([hidden])"); await sleep(700);
    const h = (await screen()).hash;
    assert(/^#\/p\/zeta\/t\/\d+$/.test(h), "задача — свой шаг истории: " + h);
    await page.evaluate(() => { location.hash = ""; }); await page.waitForSelector("#home:not([hidden])"); await sleep(300);
    return { задача: h };
  });

  await step("события ↔ проекты: авто по синониму, «Кеды» ≠ «Кедр», отвязка вручную сохраняется, «Сделать задачей»", async () => {
    const weekTxt = async d => { await calTo("week", d); await sleep(500); return page.$$eval("#calBody .ev", els => els.map(e => e.innerText.replace(/\s+/g, " "))); };
    const evTxt = (await weekTxt(D("2026-10-29"))).concat(await weekTxt(D("2026-10-30")));
    await calTo("week", D("2026-10-29"));
    assert(evTxt.some(t => t.includes("Встреча Ред Панда") && t.includes("Red Panda")), "нет метки проекта: " + evTxt);
    assert(evTxt.some(t => t.includes("Купить кеды") && !t.includes("·  Кедр") && !/· Кедр/.test(t)), "кеды привязан к Кедру: " + evTxt);
    await page.evaluate(() => { for (const b of document.querySelectorAll("#calBody .ev")) if (b.textContent.includes("Встреча Ред Панда")) { b.click(); return; } });
    await sleep(300);
    assert((await $eval("#calPop", e => e.innerText)).includes("найден по названию"), "нет «найден по названию»");
    await page.click('#calPop [data-pop="unlink"]');
    await waitFor(() => (readData().eventLinks || {})["panda-1@test"] === "", 5000, "отвязка не сохранена");
    await sleep(2500); // сервер успевает перекачать iCal (обновление раз в 2 с)
    const ev2 = await (await fetch(BASE + `api/events?from=${D("2026-10-26")}&to=${D("2026-11-02")}`)).json();
    const h = ev2.events.find(e => e.title === "Встреча Ред Панда");
    assert(h.project === null && h.link === "off", "после обновления iCal привязка вернулась: " + JSON.stringify(h));
    await calTo("week", D("2026-10-30"));
    await page.evaluate(() => { for (const b of document.querySelectorAll("#calBody .ev")) if (b.textContent.includes("Купить кеды")) { b.click(); return; } });
    await sleep(300);
    await page.select("#popProj", "kedr"); await page.click('#calPop [data-pop="task"]');
    await waitFor(() => readData().tasks.some(t => t.title === "Купить кеды" && t.project === "kedr" && t.due === D("2026-10-30") && t.time === "09:00"), 5000, "задача из события");
    await calTo("week", D("2026-10-29"));
    await page.evaluate(() => { for (const b of document.querySelectorAll("#calBody .ev")) if (b.textContent.includes("Сверка")) { b.click(); return; } });
    await sleep(300);
    await page.select("#popProj", "portal"); await page.click('#calPop [data-pop="link"]');
    await waitFor(() => (readData().eventLinks || {})["two-1@test"] === "portal", 5000, "ручная привязка");
    await page.click("#calToday"); await sleep(300);
    await openProject("portal");
    await waitFor(() => page.$eval("#pvEvents", e => e.innerText.includes("Сверка")), 6000, "нет в «События проекта»");
    await page.screenshot({ path: path.join(SHOTS, "18-проект-широкий-с-событиями.png") });
    await toHome();
    return { ok: true };
  });

  await step("сотрудники: добавить (Имя+Фамилия), карточка с задачами, заметка; удаление без выбора не проходит, переназначение, архив и возврат", async () => {
    await page.click("#peopleLink"); await page.waitForSelector("#ppl:not([hidden])"); await sleep(300);
    await page.type('#pplNew [name="firstName"]', "Егор"); await page.click("#pplAdd"); await sleep(300);
    assert((await $eval("#pplErr", e => e.textContent)).includes("обязательны"), "без фамилии добавился");
    await page.type('#pplNew [name="lastName"]', "Соколов"); await page.type('#pplNew [name="office"]', "Найроби"); await page.click("#pplAdd");
    await waitFor(() => readData().employees.some(e => e.name === "Егор Соколов" && e.office === "Найроби"), 5000, "не добавлен");
    await page.evaluate(() => { for (const a of document.querySelectorAll(".pcard")) if (a.textContent.includes("Нина")) { a.click(); return; } });
    await sleep(500);
    const card = await $eval("#pplOne", e => e.innerText);
    assert(card.includes("менеджер проектов") || (await page.$eval('[data-ef="role"]', e => e.value)).includes("менеджер проектов"), "должность Нины");
    assert(card.includes("Разбор референсов") || card.includes("проверить вёрстку"), "задачи Нины: " + card.slice(0, 200));
    await page.type("#noteText", "Обсудить нагрузку на ноябрь"); await page.click("#noteNew .btn");
    await waitFor(() => (readData().employees.find(e => e.firstName === "Нина").notes || []).length === 1, 5000, "заметка");
    await page.screenshot({ path: path.join(SHOTS, "19-сотрудник.png") });
    await page.click("#empDel"); await sleep(200);
    await page.click("#dlgYes"); await sleep(300);
    assert((await $eval("#dlgErr", e => e.textContent)).includes("Выберите"), "FAIL-сценарий: удалилось без выбора");
    assert(!readData().employees.find(e => e.firstName === "Нина").archived, "архивирован без выбора");
    await page.select("#dlgTo", "4"); await page.click("#dlgYes");
    await waitFor(() => readData().employees.find(e => e.firstName === "Нина").archived === true, 5000, "не в архиве");
    assert(readData().tasks.filter(t => t.state !== "done" && t.whoId === 2).length === 0, "задачи не переназначены");
    await page.waitForSelector("#pplArch:not([hidden])");
    await page.click("#pplArch summary"); await page.click('[data-restore="2"]');
    await waitFor(() => readData().employees.find(e => e.firstName === "Нина").archived === false, 5000, "не вернулся");
    await page.click("#ppl .backbtn"); await page.waitForSelector("#pplList:not([hidden])"); await sleep(300); // «Назад» — на шаг: к списку сотрудников
    await page.evaluate(() => { location.hash = ""; }); await page.waitForSelector("#home:not([hidden])"); await sleep(300);
    return { ok: true };
  });

  await step("П12: клик по пустому времени → напоминание (пустое не сохраняется, HTML экранируется), блок в своё время", async () => {
    await page.click('.seg button[data-v="day"]'); await sleep(300); await page.click("#calToday"); await sleep(800);
    await page.evaluate(() => { const hh = parseFloat(document.querySelector(".dw").style.getPropertyValue("--hh")); document.querySelector(".dwscroll").scrollTop = 8 * hh; });
    await sleep(200);
    const pt = await page.evaluate(() => { const c = document.querySelector("#calBody .dcol"), r = c.getBoundingClientRect(), hh = parseFloat(document.querySelector(".dw").style.getPropertyValue("--hh")); return { x: r.left + r.width * 0.85, y: r.top + 9 * hh + 3 }; });
    await page.mouse.click(pt.x, pt.y); await sleep(400);
    assert(await $eval("#calPop", e => e.matches(":popover-open") && e.innerText.includes("Новое напоминание")), "нет формы напоминания");
    assert((await $eval("#remTime", e => e.value)) === "09:00", "время слота: " + await $eval("#remTime", e => e.value));
    await page.click("#remSave"); await sleep(300);
    assert((await $eval("#remErr", e => e.textContent)).includes("Напишите"), "FAIL: пустое напоминание сохранилось");
    await page.type("#remText", "Позвонить подрядчику <b>срочно</b>"); await page.keyboard.press("Enter");
    await waitFor(() => (readData().reminders || []).some(r => r.text.startsWith("Позвонить подрядчику") && r.time === "09:00"), 5000, "не сохранилось в data.json");
    await waitFor(() => page.$$eval("#calBody .ev", els => els.some(e => e.textContent.includes("Позвонить подрядчику <b>") && e.style.getPropertyValue("--s") === "9")), 6000, "нет блока на сетке в 09:00");
    await page.screenshot({ path: path.join(SHOTS, "26-день-сроки-и-напоминания-на-сетке.png") });
    await page.click('.seg button[data-v="week"]'); await sleep(800);
    await page.screenshot({ path: path.join(SHOTS, "27-неделя-блоки-по-времени.png") });
    return { напоминание: readData().reminders[0] };
  });

  await step("П13: видимых подсказок клавиш нет; Esc возвращает с проекта, «На паузе» и «Сотрудников»", async () => {
    await openProject("lumen");
    const kbd = await page.$$eval("kbd", els => els.filter(e => e.getClientRects().length).length);
    await page.keyboard.press("Escape"); await page.waitForSelector("#home:not([hidden])"); await sleep(400);
    await page.click("#kpiPause"); await page.waitForSelector("#pzv:not([hidden])"); await sleep(300);
    await page.keyboard.press("Escape"); await page.waitForSelector("#home:not([hidden])"); await sleep(400);
    await page.click("#peopleLink"); await page.waitForSelector("#ppl:not([hidden])"); await sleep(300);
    await page.keyboard.press("Escape"); await page.waitForSelector("#home:not([hidden])"); await sleep(400);
    assert(kbd === 0, "видимые подсказки клавиш: " + kbd);
    return { ok: true };
  });

  await step("при обрыве правка проекта A не попадает в проект B и сохраняется в A, когда связь вернулась", async () => {
    await openProject("portal");
    const noteB = readData().projects.find(p => p.id === "lumen").note;
    await page.click("#pvNote"); stopServer();
    await page.keyboard.down("Control"); await page.keyboard.press("a"); await page.keyboard.up("Control");
    await page.keyboard.type("ЗАМЕТКА-ИЗ-A"); await page.keyboard.press("Tab"); await sleep(1500);
    await page.click("#pv .backbtn"); await page.waitForSelector("#home:not([hidden])"); await sleep(400);
    await page.click('.proj[data-pid="lumen"]'); await page.waitForSelector("#pv:not([hidden])"); await sleep(900);
    const shown = await $eval("#pvNote", e => e.value);
    assert(shown !== "ЗАМЕТКА-ИЗ-A", "черновик проекта A показан в проекте B");
    await startServer();
    await waitFor(() => readData().projects.find(p => p.id === "portal").note === "ЗАМЕТКА-ИЗ-A", 12000, "правка A не сохранилась после восстановления связи");
    assert(readData().projects.find(p => p.id === "lumen").note === noteB, "проект B затёрт");
    await toHome();
    return { B: shown };
  });

  await step("устаревший ответ опроса не затирает свежую правку", async () => {
    await openProject("lumen");
    const stale = await (await fetch(BASE + "api/data")).text();
    let held = false;
    await page.setRequestInterception(true);
    const h = req => { if (!held && req.method() === "GET" && req.url().endsWith("/api/data")) { held = true; setTimeout(() => req.respond({ status: 200, contentType: "application/json", headers: { ETag: '"stale"' }, body: stale }).catch(() => {}), 1500); } else req.continue().catch(() => {}); };
    page.on("request", h);
    await waitFor(async () => held, 6000, "опрос не перехвачен");
    await page.click('#openList .trow[data-id="92"] .ck');
    await waitFor(() => task(92).state === "done", 5000, "правка");
    await sleep(2000);
    page.off("request", h); await page.setRequestInterception(false);
    const inDone = await page.$$eval('#doneList .trow[data-id="92"]', e => e.length);
    assert(inDone === 1, "устаревший ответ вернул задачу в открытые");
    await page.click('#doneList .trow[data-id="92"] .ck'); await waitFor(() => task(92).state === "work", 5000, "возврат");
    await toHome();
    return { ok: true };
  });

  await step("фокус в календаре не сбрасывается при обновлении данных", async () => {
    await calTo("week", D("2026-10-24"));
    await page.focus("#calBody .dn"); const f0 = await page.evaluate(() => document.activeElement.dataset.fid);
    const n0 = ((await plateOf(D("2026-10-24"))) || { n: 0 }).n;  // перерисовка видна по числу на плашке
    const d = readData(); d.tasks.push({ id: 800, project: "zeta", title: "Правка клиента API во время фокуса", who: "", due: D("2026-10-24"), state: "work" });
    fs.writeFileSync(path.join(T, "data.json"), JSON.stringify(d, null, 2), "utf8");
    await waitFor(async () => (((await plateOf(D("2026-10-24"))) || { n: 0 }).n === n0 + 1), 9000, "не перерисовалось");
    const f1 = await page.evaluate(() => document.activeElement.dataset.fid);
    assert(f0 && f0 === f1, `фокус: ${f0} → ${f1}`);
    return { фокус: f1 };
  });

  await step("набранный текст не пропадает при обрыве и сохраняется, когда связь вернулась", async () => {
    await openProject("zeta");
    const sel = '#openList .trow[data-id="2"] .ttl';
    await page.click(sel); await page.keyboard.press("End"); await page.keyboard.type(" ДОПИСАНО");
    stopServer();
    await waitFor(() => page.$eval("#offline", e => !e.hidden), 9000, "баннер");
    const v = await page.evaluate(s => { const el = document.querySelector(s); return { val: el.value, ro: el.readOnly, focus: document.activeElement === el, add: atAdd.disabled, back: document.querySelector("#pv .backbtn").disabled }; }, sel);
    assert(v.val.endsWith(" ДОПИСАНО") && v.ro && v.add && !v.back, "при обрыве: " + JSON.stringify(v));
    await page.screenshot({ path: path.join(SHOTS, "07-нет-связи-с-сервером.png") });
    await page.$eval(sel, e => e.blur()); await sleep(800);
    const un = await page.$eval(sel, e => ({ val: e.value, unsaved: e.classList.contains("unsaved") }));
    assert(un.val.endsWith(" ДОПИСАНО") && un.unsaved, "после неудачного сохранения: " + JSON.stringify(un));
    await startServer();
    await waitFor(() => page.$eval("#offline", e => e.hidden), 9000, "баннер не исчез");
    await waitFor(() => task(2).title === "Разбор референсов ДОПИСАНО", 8000, "черновик не сохранился после восстановления связи");
    return { ...v, сохранено: task(2).title };
  });

  await step("правка data.json извне появляется сама", async () => {
    await toHome();
    const d = readData(); d.tasks.push({ id: 777, project: "zeta", title: "Правка через API", who: "Нина", due: "", state: "wait" });
    fs.writeFileSync(path.join(T, "data.json"), JSON.stringify(d, null, 2), "utf8");
    const t0 = Date.now();
    await waitFor(() => page.$eval("#wait", e => e.innerText.includes("Правка через API")), 9000, "не появилась");
    return { мс: Date.now() - t0 };
  });

  await step("FAIL: адрес календаря недоступен → кэш и метка «обновлено ЧЧ:ММ»", async () => {
    icsUp = false; await sleep(4500);
    await page.click("#calToday"); await sleep(800);
    const note = await $eval("#calNote", e => e.innerText);
    assert(/Показаны события, сохранённые в \d\d:\d\d/.test(note) && (await count("#calBody .it, #calBody .ev, #calBody .pl")) > 0, "метка: " + note);
    await page.screenshot({ path: path.join(SHOTS, "08-календарь-кэш-нет-связи-с-Google.png") });
    icsUp = true;
    return { ok: true };
  });

  await step("FAIL: нет связи с сервером погоды → блок погоды скрыт, остальное работает", async () => {
    wxUp = false; await sleep(7500);
    await page.reload({ waitUntil: "networkidle0" }); await page.waitForSelector("#projects .proj"); await sleep(1200);
    const v = await page.evaluate(() => ({ wx: document.getElementById("wx").hidden, clock: clock.textContent, projects: document.querySelectorAll("#projects .proj").length }));
    assert(v.wx && v.projects >= 7 && /\d\d:\d\d/.test(v.clock), JSON.stringify(v));
    await page.screenshot({ path: path.join(SHOTS, "20-без-погоды.png") });
    wxUp = true;
    return v;
  });

  await step("секретный адрес не попадает в страницу, API и журнал", async () => {
    const html = await page.content(), ev = await (await fetch(BASE + `api/events?from=${D("2026-10-01")}&to=${D("2026-11-01")}`)).text();
    const log = fs.readFileSync(path.join(T, "server.log"), "utf8");
    assert(!html.includes(SECRET) && !ev.includes(SECRET) && !log.includes(SECRET), "секрет утёк");
    return { ok: true };
  });

  await step("клавиатура: Tab до проекта, Enter открывает, Esc возвращает фокус", async () => {
    await page.focus('.proj[data-pid="lumen"]'); await page.keyboard.press("Enter");
    await page.waitForSelector("#pv:not([hidden])"); await sleep(900);
    const n = await $eval("#pvName", e => e.value);
    await toHome(); await sleep(400);
    const f = await page.evaluate(() => document.activeElement.dataset.pid);
    assert(n === "LUMEN" && f === "lumen", `${n} / ${f}`);
    await page.screenshot({ path: path.join(SHOTS, "12-фокус-с-клавиатуры.png") });
    return { ok: true };
  });

  await step("+ проект: двойной Enter создаёт один проект, сразу внутрь", async () => {
    const n0 = readData().projects.length;
    await page.click("#addProjBtn"); await page.type("#newProjName", "Тестовый проект");
    await page.keyboard.press("Enter"); await page.keyboard.press("Enter");
    await page.waitForSelector("#pv:not([hidden])"); await sleep(900);
    assert(readData().projects.length === n0 + 1, "создано проектов: " + (readData().projects.length - n0));
    assert((await $eval("#openList", e => e.innerText)).includes("Следующий шаг не назначен"), "пустое состояние");
    await toHome();
    return { ok: true };
  });

  await step("бледные элементы без прозрачности, статус проекта не только цветом", async () => {
    const v = await page.evaluate(() => {
      const op = s => [...document.querySelectorAll(s)].map(e => +getComputedStyle(e).opacity).filter(o => o < 1).length;
      return { ex: op(".ex, .ex *"), past: op(".ev.past, .tk.done, .mc.out .mdn"), sr: [...document.querySelectorAll(".proj .sr")].map(e => e.textContent).filter(t => t.includes("статус")).length };
    });
    assert(v.ex === 0 && v.past === 0 && v.sr >= 7, JSON.stringify(v));
    return v;
  });


  await step("склонения с числами (1, 2, 5, 11, 21, 22, 25) и сроки словами", async () => {
    const r = await page.evaluate(() => ({ d: [1, 2, 5, 11, 21, 22, 25].map(n => nDays(n)),
      t: [dueText("2026-10-21", "2026-10-23"), dueText("2026-10-25", "2026-10-23"), dueText("2026-11-03", "2026-10-23"), dueText("2026-10-22", "2026-10-23"),
          dueText("2026-10-23", "2026-10-23"), dueText("2026-10-24", "2026-10-23"), dueText("", "2026-10-23")],
      z: [1, 2, 5, 11, 21, 22, 25].map(n => n + " " + plural(n, "задача", "задачи", "задач")),
      proj: document.getElementById("projects").innerText, foot: document.querySelector("footer").innerText, wday: document.getElementById("calLabel").textContent }));
    assert(JSON.stringify(r.d) === JSON.stringify(["1 день", "2 дня", "5 дней", "11 дней", "21 день", "22 дня", "25 дней"]), "дни: " + r.d);
    assert(JSON.stringify(r.z) === JSON.stringify(["1 задача", "2 задачи", "5 задач", "11 задач", "21 задача", "22 задачи", "25 задач"]), "задачи: " + r.z);
    assert(JSON.stringify(r.t) === JSON.stringify(["просрочено на 2 дня (срок 21.10)", "срок 25.10, через 2 дня", "срок 03.11, через 11 дней", "просрочено на 1 день (срок 22.10)", "срок сегодня", "срок завтра", "без срока"]), "сроки: " + r.t);
    assert(/Следующий шаг: [^\n]+, до \d\d\.\d\d\./.test(r.proj) && r.proj.includes("Следующий шаг не назначен."), "строка проекта: " + r.proj.slice(0, 300));
    assert(!/дальше:|нет следующего шага| дн\b|·\s*через/.test(r.proj), "старые обороты: " + r.proj.slice(0, 300));
    assert(r.foot.includes("Записи с пометкой «Пример» — тестовые, удалим, когда появятся настоящие") && /Обновлено в \d\d:\d\d/.test(r.foot), "подвал: " + r.foot);
    return { дни: r.d.join(", "), сроки: r.t.slice(0, 2) };
  });

  await step("комментарии — пустой не уходит, Enter/Shift+Enter, HTML как текст, правка, удалить → «Вернуть», серая строка, обрыв связи", async () => {
    await openProject("zeta");
    const row = '#openList .trow[data-id="2"]';
    await page.click(`${row} [data-act="cm"]`); await page.waitForSelector(`${row} .cin`);
    await page.focus(`${row} .cin`); await page.keyboard.press("Enter"); await sleep(300);
    assert((await $eval(`${row} [data-cerr]`, e => e.textContent)).includes("пустой"), "нет ошибки на пустой");
    assert(!(task(2).comments || []).length, "FAIL-сценарий: пустой комментарий сохранился");
    await page.type(`${row} .cin`, "Позвонила заказчику"); await page.keyboard.press("Enter");
    await waitFor(() => (task(2).comments || []).length === 1, 5000, "комментарий не сохранён");
    await page.type(`${row} .cin`, "строка один"); await page.keyboard.down("Shift"); await page.keyboard.press("Enter"); await page.keyboard.up("Shift");
    await page.type(`${row} .cin`, "строка два"); await page.keyboard.press("Enter");
    await waitFor(() => (task(2).comments || []).length === 2, 5000, "второй не сохранён");
    assert(task(2).comments[1].text === "строка один\nстрока два", "Shift+Enter: " + JSON.stringify(task(2).comments[1].text));
    await page.type(`${row} .cin`, '<img src=x onerror="window.__x=1">'); await page.keyboard.press("Enter");
    await waitFor(() => (task(2).comments || []).length === 3, 5000, "третий не сохранён");
    await sleep(300);
    const feed = await page.evaluate(r => ({ img: !!document.querySelector(r + " .cfeed img"), txt: document.querySelector(r + " .cfeed").innerText, x: window.__x }), row);
    assert(!feed.img && feed.x === undefined && feed.txt.includes('<img src=x onerror="window.__x=1">'), "экранирование: " + JSON.stringify(feed).slice(0, 200));
    assert(/Вы, \d+ [а-я]+ в \d\d:\d\d/.test(feed.txt), "дата и время у комментария: " + feed.txt.slice(0, 120));
    assert(task(2).comments.every(c => c.author === "owner" && /^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(c.at)), "автор и время в data.json");
    await page.screenshot({ path: path.join(SHOTS, "28-комментарии-к-задаче.png") });
    // правка
    await page.evaluate(r => document.querySelector(r + ' .clist li [data-act="cedit"]').click(), row);
    await page.waitForSelector(`${row} .cedt`); await page.keyboard.down("Control"); await page.keyboard.press("a"); await page.keyboard.up("Control");
    await page.keyboard.type("Позвонила заказчику, ждём ответ"); await page.keyboard.press("Enter");
    await waitFor(() => task(2).comments[0].text === "Позвонила заказчику, ждём ответ" && task(2).comments[0].edited, 5000, "правка не сохранена");
    // удалить → Вернуть (тот же номер)
    const firstId = task(2).comments[0].id;
    await sleep(400);
    await page.evaluate(r => document.querySelector(r + ' .clist li [data-act="cdel"]').click(), row);
    await waitFor(() => task(2).comments.length === 2, 5000, "не удалён");
    await page.waitForSelector("#toast.show button"); await page.click("#toast button");
    await waitFor(() => (task(2).comments || []).some(c => c.id === firstId), 5000, "«Вернуть» не вернул");
    // серая строка с последним комментарием, когда лента закрыта
    await page.click(`${row} [data-fid="t2-cm"]`); await sleep(300);
    const last = await $eval(`${row} .clast`, e => e.textContent);
    assert(last.startsWith("Вы: <img"), "серая строка: " + last);
    assert((await $eval(`${row} [data-fid="t2-cm"]`, e => e.textContent.trim())) === "3", "значок с числом");
    // обрыв связи: текст в поле, сохранится, когда связь вернулась
    await page.click(`${row} [data-fid="t2-cm"]`); await page.waitForSelector(`${row} .cin`);
    await page.type(`${row} .cin`, "Черновик при обрыве");
    stopServer(); await page.keyboard.press("Enter");
    // отказ соединения с закрытым портом в Windows приходит через 1–2 с — пометку ждём до 6 с, а не 0,8 с;
    // при FAIL сервер поднимается сразу (иначе следующие шаги падают каскадом от ERR_CONNECTION_REFUSED)
    const offState = () => $eval(`${row} .cin`, e => ({ v: e.value, uns: e.classList.contains("unsaved") }));
    let off = await offState();
    for (let i = 0; i < 30 && off.v && !off.uns; i++) { await sleep(200); off = await offState(); }
    if (!(off.v === "Черновик при обрыве" && off.uns)) await startServer();
    assert(off.v === "Черновик при обрыве" && off.uns, "при обрыве текст пропал: " + JSON.stringify(off));
    await startServer();
    await waitFor(() => (task(2).comments || []).filter(c => c.text === "Черновик при обрыве").length === 1, 15000, "после возврата связи не сохранён");
    await sleep(600);
    assert((await page.$eval(`${row} .cin`, e => e.value).catch(() => "")) === "", "поле не очистилось после отправки");
    await toHome();
    return { комментариев: task(2).comments.length };
  });

  await step("комментарий при обрыве — офлайн браузера и остановка сервера с перезагрузкой окна: доходит ровно один раз", async () => {
    const row = '#openList .trow[data-id="2"]';
    const openFeed = async () => { await openProject("zeta"); if (!(await page.$(`${row} .cin`))) await page.click(`${row} [data-fid="t2-cm"]`); await page.waitForSelector(`${row} .cin`); };
    const cnt = t => (task(2).comments || []).filter(c => c.text === t).length;
    const box = () => page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith("moyden.outbox.v1")).map(k => localStorage.getItem(k)).join("|"));
    // (а) офлайн браузера: поле остаётся доступным, текст не пропадает, уходит после возврата сети
    await openFeed();
    await page.setOfflineMode(true);
    await waitFor(() => page.$eval("#offline", e => !e.hidden), 15000, "баннер обрыва не появился");
    await page.click(`${row} .cin`); await page.type(`${row} .cin`, "Офлайн: перезвонить в понедельник"); await page.keyboard.press("Enter"); await sleep(600);
    const a = await $eval(`${row} .cin`, e => ({ v: e.value, ro: e.readOnly, uns: e.classList.contains("unsaved") }));
    assert(a.v === "Офлайн: перезвонить в понедельник" && !a.ro && a.uns, "офлайн: текст пропал или поле заблокировано: " + JSON.stringify(a));
    assert((await box()).includes("Офлайн: перезвонить"), "офлайн: нет в очереди localStorage");
    await page.setOfflineMode(false);
    await waitFor(() => cnt("Офлайн: перезвонить в понедельник") === 1, 20000, "офлайн: после возврата сети не сохранён");
    await sleep(1500);
    assert(cnt("Офлайн: перезвонить в понедельник") === 1, "офлайн: дубль");
    assert((await box()) === "", "офлайн: очередь не очистилась");
    // (б) сервер остановлен, окно перезагружено: текст досылается из localStorage после запуска
    await page.type(`${row} .cin`, "Сервер стоп: отправить акт"); stopServer(); await page.keyboard.press("Enter"); await sleep(900);
    assert((await box()).includes("Сервер стоп: отправить акт"), "стоп: нет в очереди localStorage");
    await page.goto(BASE).catch(() => {}); await sleep(500);
    await startServer();
    await page.goto(BASE, { waitUntil: "networkidle0" });
    await waitFor(() => cnt("Сервер стоп: отправить акт") === 1, 20000, "стоп: после перезапуска не досылается из localStorage");
    await sleep(1500);
    assert(cnt("Сервер стоп: отправить акт") === 1 && (await box()) === "", "стоп: дубль или очередь не очистилась");
    await page.waitForSelector("#projects .proj");
    return { офлайн: 1, стоп: 1 };
  });

  await step("окно обоев не стирает очередь основного окна; после закрытия окна текст доходит ровно один раз", async () => {
    const row = '#openList .trow[data-id="2"]';
    const cnt = t => (task(2).comments || []).filter(c => c.text === t).length;
    const wp = await browser.newPage(); await wp.setViewport({ width: 1600, height: 900 });
    await wp.goto(BASE + "?wallpaper=1", { waitUntil: "networkidle0" });
    await page.bringToFront(); // фоновая вкладка в headless не крутит requestAnimationFrame — ожидания Puppeteer зависали
    await openProject("zeta"); if (!(await page.$(`${row} .cin`))) await page.click(`${row} [data-fid="t2-cm"]`); await page.waitForSelector(`${row} .cin`);
    await page.type(`${row} .cin`, "Два окна: проверить смету"); stopServer(); await page.keyboard.press("Enter"); await sleep(900);
    await wp.evaluate(() => window.dispatchEvent(new Event("online"))); await sleep(1500);
    const box = await wp.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith("moyden.outbox.v1")).map(k => localStorage.getItem(k)).join("|"));
    assert(box.includes("Два окна: проверить смету"), "FAIL-сценарий: окно обоев стёрло очередь основного окна");
    await page.goto("about:blank"); // «закрыли» основное окно: в памяти текста больше нет
    await startServer();
    await page.goto(BASE, { waitUntil: "networkidle0" });
    await waitFor(() => cnt("Два окна: проверить смету") === 1, 20000, "текст из очереди не дошёл");
    await sleep(2500);
    assert(cnt("Два окна: проверить смету") === 1, "дубль");
    await wp.close();
    await page.waitForSelector("#projects .proj");
    return { ok: true };
  });

  await step("два окна без связи пишут комментарий к одной задаче — доходят оба текста, каждый один раз", async () => {
    const row = '#openList .trow[data-id="2"]';
    const cnt = t => (task(2).comments || []).filter(c => c.text === t).length;
    const box = p => p.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith("moyden.outbox.v1")).map(k => localStorage.getItem(k)).join("|"));
    const feed = async p => { await p.waitForSelector(`.proj[data-pid="zeta"]`); await p.click(`.proj[data-pid="zeta"]`); await p.waitForSelector("#pv:not([hidden])"); await sleep(600);
      if (!(await p.$(`${row} .cin`))) await p.click(`${row} [data-fid="t2-cm"]`); await p.waitForSelector(`${row} .cin`); };
    if (!(await page.$eval("#home", e => e.hidden))) {} else { await page.evaluate(() => { location.hash = ""; }); await page.waitForSelector("#home:not([hidden])"); await sleep(400); }
    const p2 = await browser.newPage(); await p2.setViewport({ width: 1600, height: 900 });
    await p2.goto(BASE, { waitUntil: "networkidle0" }); await feed(p2);
    await page.bringToFront(); await feed(page);
    stopServer();
    await page.type(`${row} .cin`, "Окно 1: текст A"); await page.keyboard.press("Enter"); await sleep(900);
    await p2.bringToFront(); await p2.type(`${row} .cin`, "Окно 2: текст B"); await p2.keyboard.press("Enter"); await sleep(900);
    const b = await box(p2);
    assert(b.includes("Окно 1: текст A") && b.includes("Окно 2: текст B"), "FAIL-сценарий: одно окно затёрло запись другого: " + b.slice(0, 300));
    await page.bringToFront(); await page.goto("about:blank"); // окно 1 закрыто до возврата связи
    await p2.bringToFront();
    await startServer();
    await waitFor(() => cnt("Окно 1: текст A") === 1 && cnt("Окно 2: текст B") === 1, 30000, "не дошли оба текста: " + JSON.stringify((task(2).comments || []).map(c => c.text).slice(-4)));
    await sleep(6000);
    assert(cnt("Окно 1: текст A") === 1 && cnt("Окно 2: текст B") === 1, "дубль");
    await p2.close(); await page.bringToFront();
    await page.goto(BASE, { waitUntil: "networkidle0" }); await page.waitForSelector("#projects .proj");
    return { ok: true };
  });

  // ---------- очередь записи при обрыве связи ----------
  const ROW2 = '#openList .trow[data-id="2"]';
  const startsCnt = t => (task(2).comments || []).filter(c => c.text.startsWith(t)).length;
  const feed2 = async p => { if (!(await p.$eval("#home", e => e.hidden))) {} else { await p.evaluate(() => { location.hash = ""; }); await p.waitForSelector("#home:not([hidden])"); await sleep(400); }
    await p.waitForSelector('.proj[data-pid="zeta"]'); await p.click('.proj[data-pid="zeta"]'); await p.waitForSelector("#pv:not([hidden])"); await sleep(600);
    if (!(await p.$(`${ROW2} .cin`))) await p.click(`${ROW2} [data-fid="t2-cm"]`); await p.waitForSelector(`${ROW2} .cin`); };
  const putEntry = (p, e) => p.evaluate(e => { const k = "moyden.outbox.v1." + e.wid; const b = JSON.parse(localStorage.getItem(k) || "[]"); b.push(e); localStorage.setItem(k, JSON.stringify(b)); }, e); // ключ закрытого окна

  await step("окно живо, но заморожено (таймеры стоят) — его запись не подбирает другое окно; комментарий ровно один", async () => {
    const p2 = await browser.newPage(); await p2.setViewport({ width: 1600, height: 900 });
    await p2.goto(BASE, { waitUntil: "networkidle0" }); await feed2(p2);
    stopServer();
    await p2.type(`${ROW2} .cin`, "α: исходный"); await p2.keyboard.press("Enter"); await sleep(900);
    assert((await p2.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith("moyden.outbox.v1")).map(k => localStorage.getItem(k)).join("|"))).includes("α: исходный"), "нет в очереди");
    await page.bringToFront(); // окно 2 в фоне — его можно заморозить
    const cdp = await p2.target().createCDPSession(); await cdp.send("Page.setWebLifecycleState", { state: "frozen" });
    await startServer();
    await page.goto(BASE, { waitUntil: "networkidle0" });
    await sleep(22000); // дольше прежнего порога «сигнала жизни» (15 с + 5 с)
    assert(startsCnt("α:") === 0, "FAIL-сценарий: запись замороженного окна подобрало и отправило другое окно");
    await cdp.send("Page.setWebLifecycleState", { state: "active" }); await p2.bringToFront();
    await waitFor(() => startsCnt("α:") >= 1, 20000, "после разморозки окно не досылает свою запись");
    await sleep(4000);
    assert(startsCnt("α:") === 1, "дубль α: " + JSON.stringify((task(2).comments || []).map(c => c.text).filter(t => t.startsWith("α"))));
    await p2.close(); await page.bringToFront();
    return { ok: true };
  });

  await step("MED-β одновременный старт двух окон — запись закрытого окна подбирает одно окно; дописанный текст — один комментарий", async () => {
    const eid = "beta-" + Date.now();
    await putEntry(page, { wid: "закрытое-окно-β", eid, fid: "t2-cnew", value: "β: исходный, дописано",
      spec: { m: "POST", p: "/api/tasks/2/comments", b: { text: "β: исходный", author: "owner", clientId: "старый-номер" } } });
    await page.goto("about:blank");
    const p2 = await browser.newPage(); await p2.setViewport({ width: 1600, height: 900 });
    await Promise.all([page.goto(BASE, { waitUntil: "networkidle0" }), p2.goto(BASE, { waitUntil: "networkidle0" })]);
    await waitFor(() => startsCnt("β:") >= 1, 20000, "запись закрытого окна не отправлена");
    await sleep(8000);
    const t = (task(2).comments || []).filter(c => c.text.startsWith("β:")).map(c => c.text);
    assert(t.length === 1 && t[0] === "β: исходный, дописано", "β: " + JSON.stringify(t));
    assert(!(await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith("moyden.outbox.v1")).map(k => localStorage.getItem(k)).join("|"))).includes("β:"), "очередь не очистилась");
    await p2.close(); await page.bringToFront();
    return { тексты: t };
  });

  await step("MED-γ удалённый комментарий не воскресает от поздней досылки той же записи (надгробие)", async () => {
    const eid = "gamma-" + Date.now(), e = { wid: "спавшее-окно-γ", eid, fid: "t2-cnew", value: "γ: текст", spec: { m: "POST", p: "/api/tasks/2/comments", b: { text: "γ: текст", author: "owner" } } };
    await putEntry(page, e);
    await waitFor(() => startsCnt("γ:") === 1, 20000, "γ не отправлен");
    const c = task(2).comments.find(x => x.text === "γ: текст");
    const rd = await fetch(BASE + `api/tasks/2/comments/${c.id}`, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: "{}" });
    assert(rd.status === 200 && startsCnt("γ:") === 0, "не удалён");
    await putEntry(page, e); // спавшее окно проснулось и досылает ту же запись
    await sleep(12000);
    assert(startsCnt("γ:") === 0, "FAIL-сценарий: удалённый комментарий воскрес");
    assert(!(await page.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith("moyden.outbox.v1")).map(k => localStorage.getItem(k)).join("|"))).includes("γ:"), "запись не снята с очереди");
    await page.bringToFront(); await page.goto(BASE, { waitUntil: "networkidle0" }); // шаг β грузил вкладку в фоне — замер LCP после загрузки на переднем плане
    await page.waitForSelector("#projects .proj");
    return { ok: true };
  });

  await step("пока окно A ждёт зависший запрос, текст окна B сохраняется сразу; окно B закрыли — текст дошёл", async () => {
    const allBox = p => p.evaluate(() => Object.keys(localStorage).filter(k => k.startsWith("moyden.outbox.v1")).map(k => localStorage.getItem(k)).join("|"));
    await feed2(page);
    let hold = true; const held = [];
    await page.setRequestInterception(true);
    const onReq = r => { if (hold && r.method() === "POST" && /\/api\/tasks\/2\/comments$/.test(r.url())) { held.push(r); return; } r.continue().catch(() => {}); };
    page.on("request", onReq);
    await page.setOfflineMode(true);
    await waitFor(() => page.$eval("#offline", e => !e.hidden), 15000, "нет баннера обрыва");
    await page.type(`${ROW2} .cin`, "M1: окно A"); await page.keyboard.press("Enter"); await sleep(700);
    await page.setOfflineMode(false);
    await waitFor(() => held.length > 0, 25000, "окно A не начало досылку"); // окно A досылает, запрос висит
    const pB = await browser.newPage(); await pB.setViewport({ width: 1600, height: 900 });
    await pB.goto(BASE, { waitUntil: "networkidle0" }); await feed2(pB);
    await pB.setOfflineMode(true); await sleep(300);
    await pB.type(`${ROW2} .cin`, "M1: окно B"); await pB.keyboard.press("Enter"); await sleep(800);
    assert((await allBox(pB)).includes("M1: окно B"), "FAIL-сценарий: текст окна B не сохранён в очередь, пока окно A ждёт ответа");
    await pB.close();
    hold = false;
    await waitFor(() => startsCnt("M1: окно B") === 1, 40000, "текст закрытого окна B не дошёл");
    await waitFor(() => startsCnt("M1: окно A") === 1, 40000, "текст окна A не дошёл после зависшего запроса");
    await sleep(3000);
    assert(startsCnt("M1: окно B") === 1 && startsCnt("M1: окно A") === 1, "дубль");
    page.off("request", onReq); for (const r of held) r.abort().catch(() => {}); await page.setRequestInterception(false);
    await page.bringToFront(); await page.goto(BASE, { waitUntil: "networkidle0" }); await page.waitForSelector("#projects .proj");
    return { ok: true };
  });

  await step("срок в календаре — плашка → список дня (просрочено, исполнитель, число комментариев) → задача в проекте с подсветкой и комментариями; новый комментарий виден в списке", async () => {
    await calTo("month", D("2026-10-22"));
    await page.click(`[data-cell="${D("2026-10-22")}"] [data-tday]`); await page.waitForSelector("#lsv:not([hidden])"); await sleep(450);
    const row = () => page.evaluate(() => { const r = [...document.querySelectorAll("#lsBody .lrow")].find(x => x.innerText.includes("ZETA") && x.innerText.includes("Разбор референсов"));
      return r ? { t: r.innerText.replace(/\s+/g, " "), cm: (r.querySelector('.chip[title^="Комментариев"]') || {}).title || "", late: r.querySelector(".dot").classList.contains("late") } : null; });
    const r0 = await row(), nc = (task(2).comments || []).length;
    assert(r0 && r0.late && new RegExp("просрочено на \\d+ (день|дня|дней) \\(срок " + FX.ddmm("2026-10-22").replace(".", "\\.") + "\\)", "i").test(r0.t) && r0.cm === "Комментариев: " + nc,
      "строка списка: " + JSON.stringify({ r0, nc }));
    await page.screenshot({ path: path.join(SHOTS, "29-список-дня-по-плашке.png") });
    await page.evaluate(() => { for (const a of document.querySelectorAll("#lsBody .lrow a.task")) if (a.innerText.includes("ZETA") && a.innerText.includes("Разбор референсов")) { a.click(); return; } });
    await page.waitForSelector("#pv:not([hidden])"); await sleep(900);
    const p = await page.evaluate(() => ({ name: document.getElementById("pvName").value, flash: [...document.querySelectorAll("#openList .trow.flash .ttl")].map(e => e.value) }));
    assert(p.name === "ZETA" && p.flash.some(v => v.startsWith("Разбор референсов")), "проект с подсветкой задачи: " + JSON.stringify(p));  // название дописано прошлым шагом
    const rs = '#openList .trow[data-id="2"]';
    if (!(await page.$(`${rs} .cin`))) await page.click(`${rs} [data-act="cm"]`);
    await page.waitForSelector(`${rs} .cin`);
    const feed = await page.$eval(`${rs} .cfeed`, e => e.innerText);
    assert(feed.includes("Черновик при обрыве"), "комментарии задачи в проекте: " + feed.slice(0, 200));
    await page.type(`${rs} .cin`, "Из списка дня"); await page.keyboard.press("Enter");
    await waitFor(() => (task(2).comments || []).some(c => c.text === "Из списка дня"), 5000, "комментарий не сохранён");
    await sleep(500);
    await page.evaluate(() => document.activeElement && document.activeElement.blur());
    await page.keyboard.press("Escape"); await page.waitForSelector("#lsv:not([hidden])", { timeout: 5000 }); await sleep(400);  // из проекта — назад в список дня
    await toHome(); await calTo("month", D("2026-10-22"));
    await page.click(`[data-cell="${D("2026-10-22")}"] [data-tday]`); await page.waitForSelector("#lsv:not([hidden])"); await sleep(450);
    const r1 = await row(), nc1 = (task(2).comments || []).length;
    assert(r1 && nc1 === nc + 1 && r1.cm === "Комментариев: " + nc1, "число комментариев в списке не обновилось: " + JSON.stringify({ r1, nc1 }));
    await page.click("#lsv [data-back]"); await page.waitForSelector("#home:not([hidden])"); await sleep(500);
    return { строка: r0.t.slice(0, 120), комментариев: nc1 };
  });

  await step("даты проектов — веха со временем и без — в полосе «весь день», пауза серым, без дат — ничего; флажок", async () => {
    await page.evaluate(() => { const p = document.getElementById("calPop"); if (p.matches(":popover-open")) p.hidePopover(); document.querySelector('.seg button[data-v="day"]').click(); });
    const day = tdy();
    const api = (m, u, b) => fetch(BASE + u, { method: m, headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then(r => r.json());
    await api("PATCH", "api/projects/portal", { deadline: day, milestones: [{ title: "Встреча с клиентом", date: day, time: "15:00" }] });
    await api("PATCH", "api/projects/kedr", { pause: { reason: "проверка" }, milestones: [{ title: "Эскиз", date: day }] });
    await page.evaluate(() => document.getElementById("calToday").click()); await sleep(5000);
    const r = await page.evaluate(() => ({ grid: [...document.querySelectorAll("#calBody .dcol .ev.pj")].map(e => ({ t: e.innerText.replace(/\s+/g, " "), s: e.style.getPropertyValue("--s"), pz: e.classList.contains("pz") })),
      strip: [...document.querySelectorAll("#calBody .ad .pl.pj")].map(e => ({ t: e.innerText, pz: e.classList.contains("pz"), bg: getComputedStyle(e).backgroundColor })) }));
    assert(!r.grid.length && r.strip.some(x => x.t.includes("15:00 Магазин Портал · Этап: Встреча с клиентом")), "веха 15:00 — в полосе «весь день» со временем, не на сетке: " + JSON.stringify(r));
    assert(r.strip.some(x => x.t.includes("Магазин Портал · Срок сдачи") && !x.pz), "срок сдачи в полосе: " + JSON.stringify(r.strip));
    assert(r.strip.some(x => x.t.includes("Кедр · Этап: Эскиз") && x.pz), "проект на паузе — серый: " + JSON.stringify(r.strip));
    assert(!r.grid.concat(r.strip).some(x => x.t.includes("ZETA")), "FAIL-сценарий: у ZETA нет дат, но что-то нарисовано");
    await page.screenshot({ path: path.join(SHOTS, "30-даты-проектов-в-календаре.png") });
    await page.click("#calProj"); await sleep(300);
    assert((await count("#calBody .pj")) === 0, "флажок «Показывать даты проектов» не скрыл");
    await page.click("#calProj"); await sleep(300);
    await page.evaluate(() => [...document.querySelectorAll("#calBody .pl.pj")].find(e => e.innerText.includes("Срок сдачи")).click());
    await page.waitForSelector("#pv:not([hidden])"); await sleep(400);
    const ms = await $eval("#pvMs", e => e.innerText);
    assert(ms.includes("Встреча с клиентом") && ms.includes("Срок сдачи проекта"), "редактор этапов: " + ms);
    await page.type("#msTitle", "Сдача макета"); await page.keyboard.press("Enter"); await sleep(400);
    assert((await $eval("#msErr", e => e.textContent)).includes("дату"), "этап без даты должен не сохраняться");
    await toHome();
    await api("PATCH", "api/projects/kedr", { pause: null, milestones: [] });
    return { сетка: r.grid.length, полоса: r.strip.length };
  });

  await step("синхронизация Google: файл синхронизации → блоки как в Google (оранжевые, по длительности), «Обновлено из Google», снимок рядом с эталоном", async () => {
    await page.evaluate(() => { const p = document.getElementById("calPop"); if (p.matches(":popover-open")) p.hidePopover(); document.querySelector('.seg button[data-v="day"]').click(); });
    await fetch(BASE + "api/projects/portal", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ deadline: "", milestones: [] }) });
    await sleep(4500); // страница подхватит данные: веха 15:00 из прошлого шага делила бы колонку на две
    const nowIso = new Date(Date.now() - 10 * 60000).toISOString(); // фикстура, а не живой файл синхронизации 
    const sj = JSON.parse(fs.readFileSync(path.join(__dirname, "fixture-google-events.json"), "utf8").replaceAll("{TODAY}", tdy()).replaceAll("{NOW}", nowIso)); // + короткое событие на 15 минут ()
    sj.events.push({ id: "short15", calendar: sj.calendars[0].id, title: "Короткий звонок фрилансеру по макетам и иконкам", start: `${tdy()}T17:15:00+03:00`, end: `${tdy()}T17:30:00+03:00`, allDay: false });
    fs.writeFileSync(path.join(T, "google-events.json"), JSON.stringify(sj));
    await page.evaluate(() => document.getElementById("calToday").click()); await sleep(1500);
    await page.evaluate(() => document.getElementById("calToday").click()); await sleep(1200);
    const r = await page.evaluate(() => { const L = h => { const m = h.match(/[\d.]+/g), c = (h.startsWith("color(") ? m.slice(0, 3).map(Number) : m.slice(0, 3).map(v => v / 255)).map(v => v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4); return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]; };
      return { hh: parseFloat(getComputedStyle(document.querySelector("#calBody .dw")).getPropertyValue("--hh")),
        evs: [...document.querySelectorAll("#calBody .dcol .ev")].map(e => { const cs = getComputedStyle(e); return { t: e.innerText.replace(/\s+/g, " "), s: e.style.getPropertyValue("--s"), d: e.style.getPropertyValue("--d"), one: e.classList.contains("one"), h: e.clientHeight, sh: e.scrollHeight, lh: parseFloat(cs.lineHeight), w: e.getBoundingClientRect().width, col: e.closest(".dcol").getBoundingClientRect().width, bg: cs.backgroundColor, contrast: +((Math.max(L(cs.backgroundColor), L(cs.color)) + .05) / (Math.min(L(cs.backgroundColor), L(cs.color)) + .05)).toFixed(2) }; }),
        note: document.getElementById("calNote").innerText }; });
    const a = r.evs.find(e => e.t.includes("Напоминание: встреча по брендбуку")), b = r.evs.find(e => e.t.includes("разбор референсов Атлас"));
    const c = r.evs.find(e => e.t.includes("Короткий звонок"));
    assert(a && a.s === "15" && Math.abs(+a.d - 0.5) < 0.01 && a.one && a.t.startsWith("15:00 Напоминание: встреча по брендбуку"), "30 минут — одна строка: " + JSON.stringify(a));
    assert(b && b.s === "16" && Math.abs(+b.d - 1) < 0.01 && !b.one && b.t.includes("16:00–17:00"), "60 минут — две строки: " + JSON.stringify(b));
    assert(c && c.one && c.h >= 18, "15 минут — одна строка, высота не меньше 18: " + JSON.stringify(c));
    assert(r.hh >= 48, "час не меньше 48 px: " + r.hh);
    assert(r.evs.every(e => e.sh <= e.h + 1 && e.lh >= 13), "текст не выходит за границы блока: " + JSON.stringify(r.evs.filter(e => e.sh > e.h + 1)));
    const tk = await page.evaluate(d => { const b = document.querySelector(`#calBody .dwall [data-tday="${d}"]`); if (!b) return null; const cs = getComputedStyle(b);  // срок — плашкой в полосе
      const L = h => { const m = h.match(/[\d.]+/g), c = (h.startsWith("color(") ? m.slice(0, 3).map(Number) : m.slice(0, 3).map(v => v / 255)).map(v => v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4); return .2126 * c[0] + .7152 * c[1] + .0722 * c[2]; };
      return { t: b.textContent.trim(), bg: cs.backgroundColor, h: b.clientHeight, sh: b.scrollHeight, contrast: +((Math.max(L(cs.backgroundColor), L(cs.color)) + .05) / (Math.min(L(cs.backgroundColor), L(cs.color)) + .05)).toFixed(2) }; }, tdy());
    assert(tk && /^Задачи: \d+$/.test(tk.t) && tk.sh <= tk.h + 1 && tk.contrast >= 4.5 && !r.evs.some(e => /Встреча по брендбуку ·/.test(e.t)), "срок задачи — плашкой «Задачи: N» в полосе, не блоком 18:00: " + JSON.stringify({ tk, evs: r.evs.map(e => e.t) }));
    assert(a.w > a.col - 6, "блок на всю ширину колонки: " + a.w + "/" + a.col);
    assert(r.evs.every(e => e.contrast >= 4.5), "контраст текста на заливке: " + r.evs.map(e => e.contrast));
    assert(/Обновлено из Google в \d\d:\d\d/.test(r.note), "пометка: " + r.note);
    const rgb = s => { const m = (s.match(/[\d.]+/g) || []).slice(0, 3).map(Number); return s.startsWith("color(") ? m.map(v => v * 255) : m; };
    const [br, bgc, bb] = rgb(b.bg), [tr, tg, tb] = rgb(tk.bg);
    assert(br > 200 && br > bgc + 40 && bgc > bb, "событие Google не оранжевое: " + b.bg);
    assert(!(tr > tg + 40) && !(tb > tr + 60 && tb > tg + 20), "срок задачи не должен быть оранжевым или синим, как Google: " + tk.bg);
    await page.screenshot({ path: path.join(SHOTS, "31-день-как-google-синхронизация.png") });
    fs.unlinkSync(path.join(T, "google-events.json"));
    await page.evaluate(() => document.getElementById("calToday").click()); await sleep(800);
    return { событий: r.evs.length, контраст: r.evs.map(e => e.contrast) };
  });

  await step("завершить проект — без выбора действия не проходит, задачи выполнены, «Завершённые проекты», вне счётчиков, «Вернуть в работу»", async () => {
    const sum = async () => (await fetch(BASE + "api/summary")).json();
    await openProject("brand");
    const open1 = readData().tasks.filter(t => t.project === "brand" && t.state !== "done").map(t => t.id);
    await page.click("#pvFinish"); await page.waitForSelector("#dlg[open]");
    await page.click("#dlgYes"); await sleep(300);
    if (open1.length) {
      assert((await $eval("#dlgErr", e => e.textContent)).includes("Выберите"), "нет ошибки без выбора");
      assert(readData().projects.find(p => p.id === "brand").status !== "done", "FAIL-сценарий: завершился без выбора действия");
      await page.click('#dlg input[value="done"]');
    }
    await page.type("#finNote", "брендбук пройдена"); await page.click("#dlgYes");
    await waitFor(() => readData().projects.find(p => p.id === "brand").status === "done", 5000, "не завершён");
    const s = await sum();
    assert(open1.every(id => readData().tasks.find(t => t.id === id).state === "done"), "задачи не отмечены выполненными");
    assert(!open1.some(id => s.today.concat(s.late, s.noDue, s.priority).includes(id)) && s.finishedProjects.includes("brand"), "в счётчиках: " + JSON.stringify(s));
    assert((await $eval("#pvDoneTxt", e => e.textContent)).includes("Итог: Брендбук пройдена."), "плашка завершения");
    assert(await $eval("#pvStatus", e => !!e.closest("label").hidden), "у завершённого проекта виден список статуса");
    const r409 = await fetch(BASE + "api/projects/brand", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: "risk" }) });
    assert(r409.status === 409 && readData().projects.find(p => p.id === "brand").status === "done", "смена статуса вернула проект в работу");
    await toHome();
    assert(!(await page.$('.proj[data-pid="brand"]')), "завершённый проект остался в «Проектах»");
    await page.click('#projects a[href="#/done"]'); await page.waitForSelector("#dnv:not([hidden])"); await sleep(300);
    assert((await $eval("#dnList", e => e.innerText)).includes("Брендбук"), "нет в «Завершённых»");
    await page.screenshot({ path: path.join(SHOTS, "32-завершённые-проекты.png") });
    await page.click('[data-reopen="brand"]');
    await waitFor(() => readData().projects.find(p => p.id === "brand").status !== "done", 5000, "не вернулся в работу");
    const h = readData().projects.find(p => p.id === "brand").history.map(x => x.action);
    assert(JSON.stringify(h) === JSON.stringify(["finished", "reopened"]), "история: " + h);
    assert(open1.every(id => readData().tasks.find(t => t.id === id).state !== "done"), "задачи, закрытые завершением, не открылись снова");
    await toHome();
    assert(!!(await page.$('.proj[data-pid="brand"]')), "после возврата нет в «Проектах»");
    return { история: h };
  });

  await step("экран «Подключить Google Календарь» — плохой адрес не сохраняется, хороший проверяется и сохраняется, адрес показан маской", async () => {
    await page.evaluate(() => { location.hash = "#/google"; }); await page.waitForSelector('#gcv:not([hidden]) .gcf[data-gc="Личный"]'); await sleep(400);
    assert((await count("#gcv .gsteps [role=img]")) === 3, "картинки-шаги"); // мини-схемы вместо SVG
    const f = '.gcf[data-gc="Личный"]';
    await page.type(`${f} input`, "ftp://example.com/basic.ics"); await page.click(`${f} .btn`); await sleep(1500);
    assert((await $eval(`${f} .ferr`, e => e.textContent)).includes("Адрес не подходит"), "FAIL-сценарий: плохой адрес");
    assert(!JSON.parse(fs.readFileSync(path.join(T, "cal.json"), "utf8")).calendars[1].ical_url, "плохой адрес записан");
    await page.$eval(`${f} input`, e => { e.value = ""; });
    await page.type(`${f} input`, `http://127.0.0.1:${ICS_PORT}/${SECRET}/basic.ics`); await page.click(`${f} .btn`);
    await page.waitForFunction(s => (document.querySelector(s + " .ok") || {}).textContent?.includes("Подключён"), { timeout: 15000 }, f);
    const txt = await page.evaluate(() => document.body.innerHTML);
    assert(!txt.includes(SECRET), "секрет виден на странице");
    assert((await $eval(`${f} .ok`, e => e.textContent)).includes("127.0.0.1/…/basic.ics"), "маска адреса");
    await page.screenshot({ path: path.join(SHOTS, "35-подключить-google-календарь.png") });
    await page.click(`${f} [data-gcoff]`); await page.waitForFunction(s => (document.querySelector(s + " .m") || {}).textContent === "Не подключён", { timeout: 8000 }, f);
    await toHome();
    return { ok: true };
  });

  await step("метрики: LCP, CLS, INP (лабораторные, чистая загрузка на переднем плане + 2 действия)", async () => {
    // замер не наследует состояние предыдущих шагов (эмуляция обрыва, перехват запросов, вторая вкладка): в e2e после шага 
    // при перезагрузке однажды сдвигалась шапка (CLS 0,159), в отдельной диагностике (tests\проверочный экземпляр\диаг-cls.js) — 0,011; см. _ПЕРЕДАЧА.md
    await page.bringToFront(); await page.goto(BASE, { waitUntil: "networkidle0" }); await sleep(2500);
    await page.click('.seg button[data-v="week"]'); await sleep(500); await page.click('.seg button[data-v="day"]'); await sleep(700);
    const m = await page.evaluate(() => ({ LCP_ms: Math.round(window.__lcp), CLS: +window.__cls.toFixed(4), INP_ms: Math.round(window.__inp), сдвиги: (window.__shifts || []).slice(-8) }));
    assert(m.LCP_ms <= 2500 && m.CLS <= 0.1 && m.INP_ms <= 200, JSON.stringify(m));
    return m;
  });

  await step("окно 1920×1080 и 1280×800: без вылезаний", async () => {
    const out = {};
    for (const [w, h, f] of [[1920, 1080, "09-главный-1920x1080.png"], [1280, 800, "10-главный-1280x800.png"]]) {
      await page.setViewport({ width: w, height: h }); await page.click('.seg button[data-v="day"]'); await sleep(600);
      const o = await page.evaluate(() => { const bad = []; for (const el of document.querySelectorAll("#home section *")) { const r = el.getBoundingClientRect(), s = el.closest("section").getBoundingClientRect(); if (r.width && (r.right > s.right + 1 || r.left < s.left - 1) && getComputedStyle(el).position !== "absolute") bad.push(el.className || el.tagName); } return { hscroll: document.documentElement.scrollWidth > innerWidth, bad: bad.slice(0, 5) }; });
      assert(!o.hscroll && !o.bad.length, `${w}: ${JSON.stringify(o)}`);
      await page.screenshot({ path: path.join(SHOTS, f) }); out[w] = o;
    }
    await page.setViewport({ width: 1600, height: 900 });
    return out;
  });

  await step("режим обоев 1920×1080 и 2560×1440: на весь экран, без прокрутки, все кнопки на месте (), сброс без мыши; замер ЦП", async () => {
    const wp = await browser.newPage();
    await wp.emulateTimezone("Africa/Nairobi");
    const out = {};
    try {
    for (const [w, h, f] of [[1920, 1080, "21-обои-1920x1080.png"], [2560, 1440, "22-обои-2560x1440.png"]]) {
      await wp.setViewport({ width: w, height: h });
      await wp.goto(BASE + "?wallpaper=1&idle=3", { waitUntil: "networkidle0" }); await wp.waitForSelector("#projects .proj"); await sleep(1800);
      const o = await wp.evaluate(() => {
        const vis = s => { const e = document.querySelector(s); return !!e && getComputedStyle(e).display !== "none" && e.getClientRects().length > 0; };
        const over = [...document.querySelectorAll("#home section")].filter(s => s.scrollHeight > s.clientHeight + 1).map(s => s.querySelector("h2")?.textContent);
        const r = document.querySelector("footer").getBoundingClientRect();
        return { wp: document.documentElement.classList.contains("wp"), zoom: +document.documentElement.style.zoom, pageScroll: document.documentElement.scrollHeight > document.documentElement.clientHeight + 1 || document.body.scrollWidth > document.body.clientWidth + 1,
          calFull: vis("#calFull"), addProj: vis("#addProjBtn"), overflow: over, footerBottomVisual: Math.round(r.bottom), title: document.title };
      });
      assert(o.wp && !o.pageScroll && o.calFull && o.addProj && !o.overflow.length, `${w}: ${JSON.stringify(o)}`);
      assert(o.footerBottomVisual <= h - 40, `${w}: низ уходит под панель задач: ${o.footerBottomVisual}`);
      await wp.screenshot({ path: path.join(SHOTS, f) }); out[w] = o;
    }
    // без мыши: зашли в проект — через простой вернулось на главный, к «сегодня»
    await wp.click('.proj[data-pid="portal"]'); await wp.waitForSelector("#pv:not([hidden])");
    await wp.waitForSelector("#home:not([hidden])", { timeout: 12000 });
    out.сброс = "вернулось на главный";
    // ЦП: доля времени главного потока страницы за 30 с при обычном опросе
    const cdp = await wp.createCDPSession(); await cdp.send("Performance.enable");
    const m = async () => Object.fromEntries((await cdp.send("Performance.getMetrics")).metrics.map(x => [x.name, x.value]));
    const a = await m(); const t0 = Date.now(); await sleep(30000); const b = await m();
    out.ЦП_процент = +(100 * (b.TaskDuration - a.TaskDuration) / ((Date.now() - t0) / 1000)).toFixed(2);
    out.макет = { перерисовок: b.LayoutCount - a.LayoutCount, стилей: b.RecalcStyleCount - a.RecalcStyleCount };
    assert(out.ЦП_процент < 2, "опрос грузит ЦП: " + out.ЦП_процент + "%");
    } finally { await wp.close().catch(() => {}); await page.bringToFront(); }
    return out;
  });

  await step("prefers-reduced-motion: переходы без анимации", async () => {
    await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
    await openProject("kedr"); await toHome();
    await page.emulateMediaFeatures([]);
    return { ok: true };
  });

  await step("старый путь file:// не ломается молча", async () => {
    // Герметично: страница из файла пингует программу по умолчанию и переходит к ней. Настоящий порт не трогаем —
    // на машине разработчика там может работать его собственная программа с настоящими данными. Все обращения к 127.0.0.1
    // вне тестового сервера перехватываются заглушкой и записываются.
    const p2 = await browser.newPage(); await p2.setViewport({ width: 1600, height: 900 });
    const hits = [];
    await p2.setRequestInterception(true);
    p2.on("request", rq => { const u = rq.url();
      if (/^http:\/\/(127\.0\.0\.1|localhost):/.test(u) && !u.startsWith(BASE)) { hits.push(u);
        return rq.respond({ status: 200, contentType: "text/html; charset=utf-8", body: "<!doctype html><title>заглушка</title><p>заглушка программы</p>" }); }
      rq.continue(); });
    await p2.goto("file:///" + path.join(DASH, "дашборд.html").replace(/\\/g, "/"), { waitUntil: "networkidle0" }); await sleep(1500);
    const txt = await p2.evaluate(() => document.body.innerText + " | " + location.href);
    await p2.close();
    assert(hits.length && hits.every(u => u.startsWith("http://127.0.0.1:8648/")), "FAIL-сценарий: страница обратилась не к своему порту 8648: " + hits.join(", "));
    assert(hits.includes("http://127.0.0.1:8648/api/ping") && txt.endsWith("| http://127.0.0.1:8648/") && txt.includes("заглушка программы"),
      "страница из файла должна найти программу на 8648 и перейти к ней: " + txt + " | " + hits.join(", "));
    return { обращения: hits, адрес: txt.split(" | ").pop() };
  });

  await step("1.3.3 обои: ☆ и соседние кнопки при настоящем нажатии (90 мс), когда перерисовка отложена или приходит во время нажатия", async () => {
    const r = await require("./обои-клик.js").run({ browser, BASE, readData });
    await page.bringToFront();
    const bad = r.filter(x => !x.ok);
    assert(!bad.length, bad.map(x => x.name + ": " + x.error).join(" | "));
    return { проверок: r.length };
  });

  await step("1.3.4 отложенная перерисовка: нажатие без отпускания, списки, прямые перерисовки, обои без фокуса, CLS шапки", async () => {
    const r = await require("./обои-134.js").run({ browser, BASE, readData });
    await page.bringToFront();
    const bad = r.filter(x => !x.ok);
    assert(!bad.length, bad.map(x => x.name + ": " + x.error).join(" | "));
    return { проверок: r.length, итоги: r.map(x => x.info) };
  });

  await step("консоль без ошибок", async () => { assert(consoleErrors.length === 0, consoleErrors.join(" | ")); return { ошибок: 0 }; });

  await browser.close(); stopServer(); icsSrv.close(); wxSrv.close();
  const summary = { когда: new Date().toISOString(), всего: results.length, прошло: results.filter(r => r.ok).length, упало: results.filter(r => !r.ok).length, шаги: results };
  fs.writeFileSync(path.join(__dirname, "e2e-результат.json"), JSON.stringify(summary, null, 2));
  console.log(`ИТОГ: ${summary.прошло}/${summary.всего}`);
  try { cp.execFileSync(PY, [path.join(__dirname, "в_корзину.py"), T], { stdio: "ignore" }); } catch {} // тестовые данные — в Корзину, не насовсем
  process.exit(summary.упало ? 1 : 0);
})().catch(e => { console.error(e); stopServer(); process.exit(2); });
