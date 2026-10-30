// сквозные проверки в безголовом Chrome (на экран ничего не выводится), свой порт 8664; настоящие данные не трогает.
// П. 25: окно «Почему переносим?» (Отмена, Esc, «другое» без текста, перенос, раньше — без вопроса, снятие срока), метка «перенесён N×, план»,
// история срока (проект, список, обои; Назад, Esc), «вовремя / с опозданием», экран «Статистика сроков» (оракул — свой расчёт по data.json).
// П. 26: шапка в одну строку дата < часы < погода < помидоро < счётчики — 1600, 1536 (понедельник, 2 города), обои; 1100 — счётчики второй строкой.
// LOW: черновик комментария при перерисовке (14 циклов); «из календаря» в истории (Назад → Вперёд → Назад, кнопка мыши);
// «Открыть» после «Сделать задачей».
// П. 27: «Время вышло» — живой переход по отметке сервера (окно 1600, обои, 1100), размер карточки, «Ещё 5 минут», «Перерыв» с обоев после
// перезапуска, «Работа» после перерыва, «Сброс», выключатель звука. П. 28: шапка с приветствия, без значка и «Мой день».
// Запуск: node tests\e2e_1312.js [папка исходников] [--shots папка]
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
const RES = process.env.MOYDEN_E2E_OUT || path.join(__dirname, "e2e_1312-результат.json");
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
const T = fs.mkdtempSync(path.join(os.tmpdir(), "moyden-e2e1312-"));
const PORT = 8664, BASE = `http://127.0.0.1:${PORT}/`;
const TODAY = FX.today, AD = n => FX.addDays(TODAY, n);
const ddmm = d => d.slice(8, 10) + "." + d.slice(5, 7);
const stamp = (d, hm) => d + "T" + hm;

const data = JSON.parse(FX.dataJson(fs.readFileSync(path.join(__dirname, "fixture-data.json"), "utf8")));
data.tasks.push({ id: 700, project: "portal", title: "П25: перенос с причиной", whoId: 1, due: AD(3), state: "work" });
data.tasks.push({ id: 701, project: "zeta", title: "П25: уже переносили", whoId: 2, plan: AD(-10), due: AD(2), state: "work", dueHistory: [
  { from: AD(-10), to: AD(-1), at: stamp(TODAY, "09:00"), reason: "client", late: true },
  { from: AD(-1), to: AD(2), at: stamp(TODAY, "09:30"), reason: "other", text: "заказчик в отпуске", late: true }] });
data.tasks.push({ id: 702, project: "portal", title: "П25: выполнена с опозданием", whoId: 1, plan: AD(-5), due: AD(-5), state: "done", done: AD(-2) });
data.tasks.push({ id: 703, project: "portal", title: "П25: выполнена вовремя", whoId: 2, due: AD(1), state: "done", done: TODAY });
data.projects.find(p => p.id === "portal").deadline = TODAY;  // плашка проекта в календаре: проект «из календаря»
data.nextId = 800;
fs.writeFileSync(path.join(T, "data.json"), JSON.stringify(data, null, 1));
fs.copyFileSync(path.join(__dirname, "fixture-settings.json"), path.join(T, "settings.json"));
fs.writeFileSync(path.join(T, "cal.json"), '{"calendars": [{"name": "Рабочий", "ical_url": ""}, {"name": "Личный", "ical_url": ""}]}');
fs.writeFileSync(path.join(T, "google-events.json"), JSON.stringify({ synced_at: new Date().toISOString(), calendars: [{ id: "work", name: "Рабочий", color: "#E8833A" }],
  events: [{ id: "p25-ev", calendar: "work", title: "П25: встреча для задачи", start: TODAY + "T11:00:00+03:00", end: TODAY + "T12:00:00+03:00", allDay: false }] }));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const readData = () => JSON.parse(fs.readFileSync(path.join(T, "data.json"), "utf8").replace(/^\uFEFF/, ""));
const task = id => readData().tasks.find(t => t.id === id);
let srv;
const results = [];
const ONLY = process.env.MOYDEN_E2E_ONLY ? new RegExp(process.env.MOYDEN_E2E_ONLY) : null;
async function step(name, fn) {
  if (ONLY && !ONLY.test(name)) return;
  try { const info = await fn(); results.push({ step: name, ok: true, info: info ?? null }); console.log("PASS", name, info ? JSON.stringify(info).slice(0, 500) : ""); }
  catch (e) { results.push({ step: name, ok: false, error: String(e && e.message || e) }); console.log("FAIL", name, String(e && e.message || e).slice(0, 900)); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }
async function waitFor(fn, ms = 8000, msg = "не дождался") { const t = Date.now(); while (Date.now() - t < ms) { if (await fn()) return true; await sleep(150); } throw new Error(msg); }
const shot = async (pg, name) => { if (!SHOTS) return; await pg.screenshot({ path: path.join(SHOTS, name + ".png") }); };

// независимый оракул статистики: свой расчёт по data.json (не функции сервера)
function statsOracle(f, t) {
  const d = readData(), emp = new Map(d.employees.map(e => [e.id, e])), rows = {};
  for (const x of d.tasks) {
    if (x.example) continue;
    const doneIn = x.state === "done" && x.done >= f && x.done <= t;
    const mv = (x.dueHistory || []).filter(h => h.from && (!h.to || h.to > h.from) && h.at.slice(0, 10) >= f && h.at.slice(0, 10) <= t);
    if (!doneIn && !mv.length) continue;
    const e = emp.get(x.whoId), name = e ? [e.firstName, e.lastName].filter(Boolean).join(" ") : (x.who || "Без исполнителя");
    const r = rows[name] || (rows[name] = { done: 0, moves: 0 });
    if (doneIn) r.done++;
    r.moves += mv.length;
  }
  return rows;
}
const WX1 = JSON.stringify({ ok: true, cities: [{ name: "Найроби", code: 3, isDay: true, now: 20, max: 20, min: 10, precip: 0 }] });
const WX2 = JSON.stringify({ ok: true, cities: [{ name: "Найроби", code: 2, isDay: true, now: 24, max: 27, min: 14, precip: 10 }, { name: "Токио", code: 2, isDay: true, now: 18, max: 21, min: 13, precip: 70 }] });
async function weather(pg, body) { await pg.setRequestInterception(true); pg.on("request", r => { if (/\/api\/weather(\?|$)/.test(r.url())) return r.respond({ status: 200, contentType: "application/json", body: body() }); r.continue(); }); }

(async () => {
  srv = cp.spawn((process.env.PY || path.join(DEV, ".venv", "Scripts", "python.exe")), [path.join(SRC, "server.py"), "--port", String(PORT), "--data", path.join(T, "data.json"),
    "--calendars", path.join(T, "cal.json"), "--cache", path.join(T, "cache"), "--settings", path.join(T, "settings.json"), "--log", path.join(T, "server.log"),
    "--weather-url", "http://127.0.0.1:9/none", "--weather-cache", path.join(T, "wx.json"), "--google-sync", path.join(T, "google-events.json"), "--refresh", "2"], { stdio: "ignore" });
  for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE + "api/ping")).ok) break; } catch {} await sleep(200); }
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--lang=ru-RU"], protocolTimeout: 180000 });
  await require("./страж_localhost.js").install(browser, __filename);
  const errs = [];
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 900 });
  await page.emulateTimezone("Africa/Nairobi");
  page.on("pageerror", e => errs.push(String(e)));
  let wxBody = WX1;
  await weather(page, () => wxBody);
  await page.goto(BASE, { waitUntil: "networkidle2" });
  await waitFor(() => page.evaluate(() => document.querySelectorAll(".proj").length > 0));
  const go = async (pg, hash) => { await pg.evaluate(h => { location.hash = h; }, hash); await sleep(700); };
  const dlg = pg => pg.evaluate(() => { const d = document.getElementById("dlg"); return { open: d.open, title: d.open ? (d.querySelector("h3") || {}).textContent : null, text: d.open ? d.textContent : "" }; });
  // как пользователь: фокус на кнопке срока (по ней щёлкают, чтобы открыть выбор даты), затем выбор даты — change у скрытого поля
  const setDue = (pg, id, v) => pg.$eval(`#pv .trow[data-id="${id}"] .pick`, (e, v) => { e.closest(".duew").querySelector(".when").focus(); e.value = v; e.dispatchEvent(new Event("change", { bubbles: true })); }, v);
  const view = pg => pg.evaluate(() => (document.querySelector('.seg button[aria-pressed="true"][data-v]') || {}).dataset?.v || null);

  await step(" окно «Почему переносим?»: позже — окно с 6 причинами; Отмена и Esc — срок прежний; «другое» без текста — ошибка; с причиной — перенесено, метка «перенесён 1×, план»; раньше — без окна, в историю; снятие срока — с причиной", async () => {
    await go(page, "#/p/portal"); await waitFor(() => page.$(`#pv .trow[data-id="700"]`));
    const out = {};
    await setDue(page, 700, AD(6)); await sleep(400);
    out.окно = await dlg(page);
    const radios = await page.$$eval("#dlg input[name=mv]", rs => rs.map(r => r.parentElement.textContent.trim()));
    out.причины = radios;
    assert(out.окно.open && /Почему переносим/.test(out.окно.title) && radios.length === 6, "FAIL перенос позже без окна причины: " + JSON.stringify(out));
    await shot(page, "п25-окно-причины");
    await page.click("#dlgNo"); await sleep(600);
    out.отмена = { срок: task(700).due, поле: await page.$eval(`#pv .trow[data-id="700"] .pick`, e => e.value), окно: (await dlg(page)).open };
    assert(out.отмена.срок === AD(3) && out.отмена.поле === AD(3) && !out.отмена.окно, "Отмена изменила срок: " + JSON.stringify(out.отмена));
    await setDue(page, 700, AD(6)); await sleep(400); await page.keyboard.press("Escape"); await sleep(600);
    out.esc = { срок: task(700).due, окно: (await dlg(page)).open };
    assert(out.esc.срок === AD(3) && !out.esc.окно, "Esc изменил срок");
    await setDue(page, 700, AD(6)); await sleep(400);
    await page.click('#dlg input[value="other"]'); await sleep(200); await page.click("#dlgYes"); await sleep(400);
    out.без_текста = { ошибка: await page.$eval("#dlgErr", e => e.textContent), окно: (await dlg(page)).open, срок: task(700).due };
    await shot(page, "п25-окно-причины-ошибка");
    assert(out.без_текста.окно && /почему/i.test(out.без_текста.ошибка) && out.без_текста.срок === AD(3), "«другое» без текста прошло: " + JSON.stringify(out.без_текста));
    await page.type("#mvText", "ждём ГАП"); await page.click("#dlgYes");
    await waitFor(async () => task(700).due === AD(6), 6000, "перенос с причиной не сохранён");
    await sleep(800);
    const t = task(700);
    out.запись = t.dueHistory;
    await waitFor(() => page.$(`#pv .trow[data-id="700"] [data-act=hist]`), 5000, "метки нет: " ).catch(() => null);
    out.метка = await page.$eval(`#pv .trow[data-id="700"] [data-act=hist]`, e => e.textContent).catch(() => null);
    if (!out.метка) out.строка = await page.$eval(`#pv .trow[data-id="700"]`, e => (e.querySelector(".tmeta") || e).outerHTML.slice(0, 2500)).catch(e => "нет строки: " + e.message);
    assert(t.plan === AD(3) && t.dueHistory.length === 1 && t.dueHistory[0].reason === "other" && t.dueHistory[0].text === "ждём ГАП" && t.dueHistory[0].late === false
      && out.метка === `перенесён 1×, план ${ddmm(AD(3))}`, "запись или метка: " + JSON.stringify(out));
    await setDue(page, 700, AD(4)); await sleep(500);
    out.раньше = { окно: (await dlg(page)).open };
    await waitFor(async () => task(700).due === AD(4), 6000, "перенос раньше не сохранён");
    out.раньше.история = task(700).dueHistory.length;
    assert(!out.раньше.окно && out.раньше.история === 2, "раньше: " + JSON.stringify(out.раньше));
    await sleep(800); await setDue(page, 700, ""); await sleep(400);
    out.снять = await dlg(page);
    assert(out.снять.open && /снимаем срок/.test(out.снять.title), "снятие срока без окна");
    await page.click('#dlg input[value="priority"]'); await page.click("#dlgYes");
    await waitFor(async () => task(700).due === "", 6000, "снятие срока не сохранено");
    const h = task(700).dueHistory.at(-1);
    assert(h.to === "" && h.reason === "priority" && task(700).plan === AD(3), "снятие: " + JSON.stringify(h));
    await shot(page, "п25-проект-метка");
    const ch = await page.$eval(`#pv .trow[data-id="700"]`, r => { const h = e => e ? Math.round(e.getBoundingClientRect().height) : null; return { метка: h(r.querySelector("[data-act=hist]")), срок: h(r.querySelector(".mchip.when")) }; });
    assert(ch.метка && ch.метка === ch.срок, "метка «перенесён» не по размеру соседней метки срока (снимок п5): " + JSON.stringify(ch));
    return { причины: radios, метка: out.метка, записей: task(700).dueHistory.length, высота: ch };
  });

  await step(" история срока: метка в проекте → окно (2 записи: было → стало, дата, причина, «после просрочки»); «Назад» и Esc закрывают; метка в списке «Все задачи» открывает историю, а не задачу", async () => {
    await go(page, "#/p/zeta"); await waitFor(() => page.$(`#pv .trow[data-id="701"] [data-act=hist]`));
    const lbl = await page.$eval(`#pv .trow[data-id="701"] [data-act=hist]`, e => e.textContent);
    assert(lbl === `перенесён 2×, план ${ddmm(AD(-10))}`, "метка: " + lbl);
    await page.click(`#pv .trow[data-id="701"] [data-act=hist]`); await sleep(400);
    const d1 = await dlg(page), items = await page.$$eval("#dlg .hlist li", ls => ls.map(l => l.textContent));
    await shot(page, "п25-история");
    assert(d1.open && /История срока/.test(d1.title) && items.length === 2 && /заказчик в отпуске/.test(items[0]) && /после просрочки/.test(items[0])
      && /ждём данные или решение заказчика/.test(items[1]) && items[1].includes(`${ddmm(AD(-10))}.`), "история: " + JSON.stringify({ d1, items }));
    await page.click("#dlgNo"); await sleep(300);
    const closed1 = !(await dlg(page)).open;
    await page.click(`#pv .trow[data-id="701"] [data-act=hist]`); await sleep(300); await page.keyboard.press("Escape"); await sleep(300);
    const closed2 = !(await dlg(page)).open, stillProject = await page.evaluate(() => location.hash);
    assert(closed1 && closed2 && stillProject === "#/p/zeta", "закрытие: " + JSON.stringify({ closed1, closed2, stillProject }));
    await go(page, "#/list/all"); await waitFor(() => page.$('#lsv [data-hist="701"]'));
    const lc = await page.$eval('#lsv [data-hist="701"]', e => { const s = e.parentElement.querySelector(".chip:not([data-hist])"); return { метка: Math.round(e.getBoundingClientRect().height), соседняя: s ? Math.round(s.getBoundingClientRect().height) : null }; });
    assert(lc.метка === lc.соседняя, "метка «перенесён» в списке не по размеру соседних: " + JSON.stringify(lc));
    await page.click('#lsv [data-hist="701"]'); await sleep(400);
    const d2 = await dlg(page), h2 = await page.evaluate(() => location.hash);
    await page.keyboard.press("Escape"); await sleep(300);
    assert(d2.open && /История срока/.test(d2.title) && h2 === "#/list/all", "список: " + JSON.stringify({ d2: d2.title, h2 }));
    return { метка: lbl, записей: items.length };
  });

  await step(" выполнение: «с опозданием на 3 дня» от планового срока, «вовремя»; ✓ сохраняет doneLate, «Вернуть» снимает", async () => {
    await go(page, "#/p/portal"); await page.evaluate(() => { const d = document.getElementById("doneBox"); if (d) d.open = true; }); await sleep(300);
    const txt = id => page.$eval(`#pv .trow[data-id="${id}"] .mtext`, e => e.textContent).catch(() => null);
    const a = await txt(702), b = await txt(703);
    assert(/с опозданием на 3 дня/.test(a || "") && /вовремя/.test(b || ""), "FAIL нет «вовремя / с опозданием»: " + JSON.stringify({ a, b }));
    await page.$eval(`#pv .trow[data-id="701"]`, () => 0).catch(() => 0);
    await go(page, "#/p/zeta"); await page.click(`#pv .trow[data-id="701"] [data-act=toggle]`);
    await waitFor(async () => task(701).state === "done", 6000, "не выполнилась");
    const dl = task(701).doneLate;
    await sleep(900); await page.evaluate(() => { const d = document.getElementById("doneBox"); if (d) d.open = true; }); await sleep(300);
    await page.click(`#pv .trow[data-id="701"] [data-act=toggle]`);
    await waitFor(async () => task(701).state === "work", 6000, "не вернулась");
    assert(dl === 10 && !("doneLate" in task(701)), "doneLate: " + JSON.stringify({ dl, после: task(701).doneLate }));
    return { a, b, doneLate: dl };
  });

  await step(" «Статистика сроков»: вход с главного; по сотрудникам и по проектам — числа = свой расчёт по data.json; строка → список задач; прошлый месяц; пустой период — литературная фраза; «Назад» — на главный", async () => {
    await go(page, ""); await page.click("#statsLink");
    await waitFor(() => page.evaluate(() => location.hash.startsWith("#/stats") && !document.getElementById("stv").hidden && !!document.querySelector("#stBody table")), 8000, "FAIL экрана нет");
    const [f, t] = await page.evaluate(() => (location.hash.match(/^#\/stats\/(\d{4}-\d{2}-\d{2})\/(\d{4}-\d{2}-\d{2})$/) || [, null, null]).slice(1));
    const fFirst = TODAY.slice(0, 8) + "01", m1 = +TODAY.slice(5, 7), lastDay = FX.addDays((m1 === 12 ? (+TODAY.slice(0, 4) + 1) + "-01" : TODAY.slice(0, 5) + String(m1 + 1).padStart(2, "0")) + "-01", -1);
    const fx = f || fFirst, tx = t || lastDay;
    assert(fx === fFirst && tx === lastDay && /этот месяц/.test(await page.$eval("#stDet", e => e.textContent)), "период «этот месяц»: " + fx + "…" + tx);
    const rows = await page.$$eval("#stBody table:first-of-type tbody tr", trs => trs.filter(tr => tr.querySelector(".stbtn")).map(tr => ({ name: tr.querySelector(".stbtn").textContent, td: [...tr.querySelectorAll("td")].map(td => td.textContent) })));
    const or = statsOracle(fx, tx), bad = [];
    for (const r of rows) { const o = or[r.name]; if (!o || +r.td[0] !== o.done || parseInt(r.td[2]) !== o.moves) bad.push({ r, o }); }
    if (rows.length !== Object.keys(or).length) bad.push({ строк: rows.length, оракул: Object.keys(or) });
    await shot(page, "п25-статистика");
    assert(!bad.length, "числа ≠ оракулу: " + JSON.stringify(bad));
    const tot = await page.$eval("#stBody .sttot", e => e.textContent.trim());
    assert(/; средняя задержка — \d+(,\d+)? дн\.$/.test(tot) && !/\.\./.test(tot), "итоговая фраза статистики (снимок п5: «3 дн..»): " + tot);
    await page.click("#stBody table:first-of-type .stbtn"); await sleep(400);
    const sub = await page.$$eval("#stBody .sttasks li", ls => ls.map(l => l.textContent));
    assert(sub.length >= 1 && (await page.$eval("#stBody table:first-of-type .stbtn", e => e.getAttribute("aria-expanded"))) === "true", "список задач строки не открылся");
    await page.click('#stv [data-per="prev"]'); await sleep(900);
    const prev = await page.evaluate(() => { const m = location.hash.match(/^#\/stats\/(\d{4}-\d{2}-\d{2})\/(\d{4}-\d{2}-\d{2})$/) || []; return [m[1], m[2], location.hash, document.getElementById("stDet").textContent]; });
    assert(prev[0] < fFirst && prev[1] < fFirst && /прошлый месяц/.test(prev[3]), "прошлый месяц: " + JSON.stringify(prev));
    await page.$eval("#stFrom", e => { e.value = "2020-01-01"; }); await page.$eval("#stTo", e => { e.value = "2020-01-31"; }); await page.click("#stApply"); await sleep(1000);
    const empty = await page.$eval("#stBody", e => e.textContent);
    assert(/ни одна задача не выполнена и ни один срок не перенесён/.test(empty), "пустое: " + empty);
    await page.$eval("#stFrom", e => { e.value = "2020-02-01"; }); await page.click("#stApply"); await sleep(300);
    const err = await page.$eval("#stErr", e => e.textContent);
    assert(/позже конца/.test(err), "неверный период без сообщения: " + err);
    await page.keyboard.press("Escape"); await sleep(800);
    const home = await page.evaluate(() => ({ h: location.hash, home: !document.getElementById("home").hidden }));
    assert(home.home && (home.h === "" || home.h === "#"), "Esc со статистики: " + JSON.stringify(home));
    return { период: [fx, tx], строк: rows.length, задач_в_строке: sub.length, пусто: empty.slice(0, 80) };
  });

  // ----------: шапка ----------
  const headerCheck = pg => pg.evaluate(() => {
    const B = s => { const e = document.querySelector(s), b = e.getBoundingClientRect(); return { l: b.left, r: b.right, t: b.top, b: b.bottom }; };
    const d = B("#date"), w = B("#wday"), c = B("#clock"), x = B("#wx"), p = B("#pomo"), s = B("#sum"), hdr = document.querySelector("header"), hr = hdr.getBoundingClientRect();
    const z = (hr.right - hr.left) / hdr.offsetWidth || 1, pr = parseFloat(getComputedStyle(hdr).paddingRight) * z;
    const lines = e => { const g = document.createRange(); g.selectNodeContents(e); return new Set([...g.getClientRects()].filter(q => q.width > 0).map(q => Math.round(q.top))).size; };
    const cut = [...document.querySelectorAll("header .dt *, #clock, #wx *, #pomo *, #sum *")].filter(e => e.getClientRects().length && e.scrollWidth > Math.ceil(e.getBoundingClientRect().width / z) + 1).map(e => e.id || e.className || e.tagName);
    return { порядок: Math.max(d.r, w.r) < c.l && c.r < x.l && x.r <= p.l + 0.5, счётчики_после_помидоро: p.r <= s.l + 0.5, одна_строка: s.t < p.b && p.t < s.b,
      зазор_погода_помидоро: +((p.l - x.r) / z).toFixed(1), зазор_помидоро_счётчики: +((s.l - p.r) / z).toFixed(1), справа: +((hr.right - pr - Math.max(s.r, p.r)) / z).toFixed(1),
      строк_прогноза: lines(document.querySelector("#wx .wxd")), обрезано: cut, гориз: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1, ширина: innerWidth, день: document.getElementById("wday").textContent };
  });
  const wp = await browser.newPage();
  await wp.setViewport({ width: 2560, height: 1392 }); await wp.emulateTimezone("Africa/Nairobi");
  wp.on("pageerror", e => errs.push("обои: " + String(e)));
  await weather(wp, () => WX1);
  await wp.goto(BASE + "?wallpaper=1&left=0&bottom=0", { waitUntil: "networkidle2" });
  const MON = AD((7 - FX.dow(TODAY)) % 7 || 7), MON_MS = Date.parse(MON + "T10:00:00+03:00");
  const mon = await browser.newPage();
  await mon.setViewport({ width: 1536, height: 864 }); await mon.emulateTimezone("Africa/Nairobi");
  mon.on("pageerror", e => errs.push("понедельник: " + String(e)));
  await mon.evaluateOnNewDocument(ms => { const R = Date, off = ms - R.now(); class F extends R { constructor(...a) { if (a.length) super(...a); else super(R.now() + off); } static now() { return R.now() + off; } } window.Date = F; }, MON_MS);
  await weather(mon, () => WX2);
  await mon.goto(BASE, { waitUntil: "networkidle2" });

  await step(" шапка: 1600, 1536 (понедельник, 2 города), обои — одна строка, по x дата < часы < погода < помидоро < счётчики, без пустоты (зазоры ≤ 30, справа ≤ 2), прогноз ≤ 2 строк, ничего не обрезано; 1100 — счётчики второй строкой, помидоро у правого края", async () => {
    const out = {}, bad = [];
    const cases = [[page, 1600, 900, "1600", WX1], [page, 1600, 900, "1600-2города", WX2], [mon, 1536, 864, "1536-понедельник-2города", WX2], [page, 1536, 864, "1536", WX1], [wp, 2560, 1392, "обои", WX1], [page, 1100, 800, "1100", WX2]];
    for (const [pg, w, h, nm, wx] of cases) {
      await pg.bringToFront(); if (pg === page) { wxBody = wx; await pg.setViewport({ width: w, height: h }); }
      await go(pg, ""); await pg.reload({ waitUntil: "networkidle2" });
      await waitFor(() => pg.evaluate(() => !document.getElementById("wx").hidden && !!document.querySelector("#wx .wxd") && document.querySelectorAll("#sum .kpi").length === 5), 12000, nm + ": шапка не готова");
      await sleep(700);
      const r = await headerCheck(pg); out[nm] = r;
      const one = nm !== "1100";
      if (!r.порядок || r.строк_прогноза > 2 || r.обрезано.length || r.гориз || r.справа > 2
        || (one && (!r.одна_строка || !r.счётчики_после_помидоро || r.зазор_погода_помидоро > 30 || r.зазор_помидоро_счётчики > 30))
        || (!one && r.одна_строка) || (nm.includes("понедельник") && r.день !== "Понедельник")) bad.push(nm);
      await shot(pg, `шапка-${nm}`);
    }
    wxBody = WX1; await page.setViewport({ width: 1600, height: 900 }); await go(page, ""); await page.reload({ waitUntil: "networkidle2" });
    assert(!bad.length, "FAIL 1.3.11 (на 1536–1599 счётчики второй строкой, пустота за погодой): " + bad.join(", ") + " | " + JSON.stringify(out));
    return out;
  });

  // ---------- LOW: вид календаря при «Назад / Вперёд» и «Открыть» после «Сделать задачей» ----------
  await step("LOW : проект из календаря (плашка) в «Неделе» → Назад → Вперёд → Назад и кнопка мыши «назад» — вид «Неделя» держится; «Сделать задачей» → «Открыть» → Назад — тоже", async () => {
    await page.bringToFront(); await go(page, ""); await page.click('.seg button[data-v="week"]'); await sleep(600);
    const pl = `#calBody [data-proj="portal"]`; await waitFor(() => page.$(pl), 6000, "плашки проекта нет");
    await page.click(pl); await waitFor(() => page.evaluate(() => location.hash === "#/p/portal"), 5000, "проект не открылся");
    await sleep(600); await page.evaluate(() => history.back()); await sleep(900);
    const v1 = await view(page);
    await page.evaluate(() => history.forward()); await sleep(900);
    const h2 = await page.evaluate(() => location.hash);
    await page.evaluate(() => document.dispatchEvent(new MouseEvent("mouseup", { button: 3, bubbles: true }))); await sleep(1100);  // кнопка мыши «назад»
    const v2 = await view(page), h3 = await page.evaluate(() => location.hash);
    assert(v1 === "week" && h2 === "#/p/portal" && (h3 === "" || h3 === "#") && v2 === "week", "FAIL после «Вперёд» и «Назад» вид сброшен: " + JSON.stringify({ v1, h2, h3, v2 }));
    const ev = `#calBody [data-ev]`; await waitFor(() => page.evaluate(s => [...document.querySelectorAll(s)].some(e => /П25: встреча/.test(e.textContent)), ev), 8000, "события нет");
    await page.evaluate(s => [...document.querySelectorAll(s)].find(e => /П25: встреча/.test(e.textContent)).click(), ev); await sleep(500);
    await page.select("#popProj", "portal"); await page.click('#calPop [data-pop="task"]');
    await waitFor(() => page.evaluate(() => /Задача добавлена/.test(document.getElementById("toast").textContent)), 6000, "задача не создана");
    await page.click("#toast button"); await waitFor(() => page.evaluate(() => /^#\/p\/portal/.test(location.hash)), 5000, "«Открыть» не открыл проект");
    await sleep(700); await page.evaluate(() => history.back()); await sleep(900);
    const v3 = await view(page);
    assert(v3 === "week", "FAIL «Открыть» после «Сделать задачей» — возврат сбросил вид: " + v3);
    await page.click('.seg button[data-v="day"]'); await sleep(300);
    return { v1, v2, v3 };
  });

  await step("недописанный комментарий на обоях не пропадает при перерисовке от опроса — 14 циклов «набор → уход из поля → правка с сервера»", async () => {
    // отдельная страница обоев (одна активная вкладка); простой 20 с вместо 2 мин — уход на главный посреди набора ловится за 14 циклов
    const wp = await browser.newPage(); await wp.setViewport({ width: 2440, height: 1392 }); await wp.emulateTimezone("Africa/Nairobi");
    wp.on("pageerror", e => errs.push("обои-черновик: " + String(e)));
    await wp.goto(BASE + "?wallpaper=1&idle=20#/p/zeta", { waitUntil: "networkidle0" }); await wp.bringToFront();
    await waitFor(() => wp.$('#pv .trow[data-id="2"] [data-act=cm]'), 8000, "строки задачи 2 нет"); await sleep(500);
    await wp.click('#pv .trow[data-id="2"] [data-act=cm]'); await waitFor(() => wp.$('[data-fid="t2-cnew"]'));
    const f = '[data-fid="t2-cnew"]', lost = [];
    for (let i = 0; i < 14; i++) {
      await wp.$eval(f, e => { e.value = ""; e.dispatchEvent(new Event("input", { bubbles: true })); e.focus(); e.blur(); }); await sleep(300);
      const title = "Проба " + i;
      await fetch(BASE + "api/tasks/701", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ title }) });  // задача того же проекта ZETA — список перерисовывается
      await sleep(Math.round((i * 370) % 4000));
      await wp.focus(f); await wp.keyboard.type("недописанный"); await wp.$eval(f, e => e.blur());
      const okr = await waitFor(() => wp.evaluate(t => [...document.querySelectorAll("#pv .ttl")].some(x => x.value === t), title), 9000).catch(() => false);
      if (!okr) { lost.push({ i, перерисовка: false, экран: await wp.evaluate(() => location.hash) }); break; }
      await sleep(300);
      const v = await wp.$eval(f, e => e.value);
      if (v !== "недописанный") lost.push({ i, v });
    }
    await wp.$eval(f, e => { e.value = ""; e.dispatchEvent(new Event("input", { bubbles: true })); e.focus(); e.blur(); });
    const h = await wp.evaluate(() => location.hash); await wp.close();
    assert(!lost.length && h === "#/p/zeta", "FAIL 1.3.9–черновик пропал (обои ушли на главный посреди набора без нажатий клавиш): " + JSON.stringify({ lost, h }));
    return { циклов: 14, экран: h };
  });

  await step(" обои: метка в проекте → история; «Назад» закрывает; клавиатура обоям не запрашивается (полей нет)", async () => {
    await wp.bringToFront(); await go(wp, "#/p/zeta");
    await waitFor(() => wp.$(`#pv .trow[data-id="701"] [data-act=hist]`), 8000, "метки нет на обоях");
    await wp.click(`#pv .trow[data-id="701"] [data-act=hist]`); await sleep(400);
    const d = await dlg(wp);
    await shot(wp, "п25-обои-история");
    await wp.click("#dlgNo"); await sleep(300);
    assert(d.open && /История срока/.test(d.title) && !(await dlg(wp)).open, "обои: " + JSON.stringify(d.title));
    await go(wp, "");
    return { окно: d.title };
  });

  // ---------- 1.3.12 «Время вышло» и шапка ----------
  const SRV = () => cp.spawn((process.env.PY || path.join(DEV, ".venv", "Scripts", "python.exe")), [path.join(SRC, "server.py"), "--port", String(PORT), "--data", path.join(T, "data.json"),
    "--calendars", path.join(T, "cal.json"), "--cache", path.join(T, "cache"), "--settings", path.join(T, "settings.json"), "--log", path.join(T, "server.log"),
    "--weather-url", "http://127.0.0.1:9/none", "--weather-cache", path.join(T, "wx.json"), "--google-sync", path.join(T, "google-events.json"), "--refresh", "2"], { stdio: "ignore" });
  async function restartPomo(st) {  // таймер в нужном состоянии: сервер останавливается, файл таймера — заданный, сервер снова (как перезапуск программы)
    try { cp.execSync(`taskkill /PID ${srv.pid} /T /F`, { stdio: "ignore" }); } catch {}
    for (let i = 0; i < 50; i++) { try { await fetch(BASE + "api/ping"); } catch { break; } await sleep(200); }
    fs.writeFileSync(path.join(T, "помидоро.json"), JSON.stringify(Object.assign({ v: 1, durations: { work: 25, short: 5, long: 15, every: 4 }, phase: "work", running: false,
      startedAt: null, elapsed: 0, cycle: 0, task: null, days: {}, tasks: {}, seq: 0 }, st)));
    srv = SRV();
    for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE + "api/ping")).ok) return; } catch {} await sleep(200); }
    throw new Error("сервер не поднялся после перезапуска");
  }
  const pomoApi = async body => (await fetch(BASE + "api/pomodoro", body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : { cache: "no-store" })).json();
  const card = pg => pg.evaluate(() => { const b = id => document.getElementById(id), r = b("pomo").getBoundingClientRect(), s = b("sum").getBoundingClientRect();
    return { due: b("pomo").classList.contains("due"), run: b("pomo").classList.contains("run"), phase: b("pomoPhase").textContent, time: b("pomoTime").textContent,
      start: b("pomoStart").textContent.trim(), aria: b("pomoStart").getAttribute("aria-label"), plus: !b("pomoPlus").hidden && b("pomoPlus").getBoundingClientRect().height > 0,
      plusText: b("pomoPlus").textContent, info: !b("pomoInfo").hidden, reset: !b("pomoReset").disabled, bg: getComputedStyle(b("pomo")).backgroundColor, title: b("pomo").title,
      w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10, x: Math.round(r.x * 10) / 10, y: Math.round(r.y * 10) / 10, sumY: Math.round(s.y), sumX: Math.round(s.x) }; });
  const near = (a, b, d = 0.6) => Math.abs(a - b) <= d;

  await step(" шапка: строка начинается с приветствия — значка и надписи «Мой день» нет (1600, обои, 1100), «Сотрудники» — сразу за приветствием в той же строке", async () => {
    const hd = pg => pg.evaluate(() => { const h = document.querySelector("header .hello"), hl = document.getElementById("hello"), pl = document.getElementById("peopleLink"), dt = document.querySelector("header .dt");
      const a = hl.getBoundingClientRect(), b = pl.getBoundingClientRect();
      return { brand: !!document.getElementById("brand") || !!h.querySelector("img"), first: h.firstElementChild && h.firstElementChild.id, text: h.innerText.replace(/\s+/g, " ").trim(),
        dx: Math.round(a.left - dt.getBoundingClientRect().left), gap: Math.round(b.left - a.right), line: Math.abs(b.top - a.top) < 6 }; });
    const out = {};
    await page.bringToFront(); await go(page, ""); out.w1600 = await hd(page); await shot(page, "п28-шапка-1600");
    await wp.bringToFront(); await go(wp, ""); out.обои = await hd(wp); await shot(wp, "п28-шапка-обои");
    await page.bringToFront(); await page.setViewport({ width: 1100, height: 800 }); await sleep(500); out.w1100 = await hd(page); await shot(page, "п28-шапка-1100");
    await page.setViewport({ width: 1600, height: 900 }); await sleep(400);
    for (const [k, v] of Object.entries(out))
      assert(!v.brand && v.first === "hello" && !/Мой день/.test(v.text) && /^(Доброе утро|Добрый день|Добрый вечер|Доброй ночи) Сотрудники$/.test(v.text)
        && v.dx >= -1 && v.dx <= 2 && v.gap >= 0 && v.gap <= 40 && v.line, k + ": " + JSON.stringify(v));
    return out;
  });

  await step(" «Время вышло»: отрезок кончился по отметке сервера — карточка в окне (1600) и на обоях подсвечена, «Время вышло», 00:00, кнопка «Перерыв» (подпись «Перерыв: начать, 5 минут»), «Ещё 5 минут», «Сброс»; размер карточки и место счётчиков не меняются; перерыв сам не идёт", async () => {
    await restartPomo({ running: true, startedAt: Date.now() / 1000 - 1500 + 14 });  // помидор кончится через 14 с
    await page.bringToFront(); await page.reload({ waitUntil: "networkidle2" }); await wp.reload({ waitUntil: "networkidle2" });
    await waitFor(() => page.evaluate(() => document.getElementById("pomo").classList.contains("run")), 8000, "таймер не идёт после перезапуска");
    const before = await card(page), beforeWp = await card(wp);
    await waitFor(() => page.evaluate(() => document.getElementById("pomo").classList.contains("due")), 30000, "окно: «Время вышло» не появилось");
    await sleep(300);
    const c = await card(page); await shot(page, "п27-время-вышло-1600");
    await wp.bringToFront();
    await waitFor(() => wp.evaluate(() => document.getElementById("pomo").classList.contains("due")), 15000, "обои: «Время вышло» не появилось");
    const cw = await card(wp); await shot(wp, "п27-время-вышло-обои");
    await page.bringToFront(); await page.setViewport({ width: 1100, height: 800 }); await sleep(500); const c11 = await card(page); await shot(page, "п27-время-вышло-1100");
    await page.setViewport({ width: 1600, height: 900 }); await sleep(400);
    const s = await pomoApi();
    for (const [k, v] of [["окно", c], ["обои", cw], ["1100", c11]])
      assert(v.due && !v.run && v.phase === "Время вышло" && v.time === "00:00" && v.start === "Перерыв" && v.aria === "Перерыв: начать, 5 минут" && v.plus
        && v.plusText === "Ещё 5 минут" && !v.info && v.reset && /Время вышло в \d\d:\d\d: помидор закончился/.test(v.title), k + ": " + JSON.stringify(v));
    assert(c.bg !== before.bg, "карточка не подсвечена: " + c.bg);
    assert(near(c.w, before.w) && near(c.h, before.h) && near(c.x, before.x) && near(c.y, before.y) && c.sumY === before.sumY && c.sumX === before.sumX,
      "окно: карточка или счётчики сдвинулись: " + JSON.stringify({ before, c }));
    assert(near(cw.w, beforeWp.w) && near(cw.h, beforeWp.h) && cw.sumY === beforeWp.sumY && cw.sumX === beforeWp.sumX, "обои: сдвиг: " + JSON.stringify({ beforeWp, cw }));
    assert(s.waiting && s.phase === "short" && !s.running && s.expired.from === "work" && s.today === 1 && Math.abs(s.remaining - 300) < 0.01, "сервер: " + JSON.stringify(s));
    await sleep(2500);
    const s2 = await pomoApi();
    assert(s2.waiting && !s2.running && Math.abs(s2.remaining - 300) < 0.01, "перерыв пошёл сам: " + JSON.stringify(s2));
    return { окно: c, обои: { phase: cw.phase, start: cw.start }, w1100: { phase: c11.phase, w: c11.w }, размер: [before.w, before.h, c.w, c.h] };
  });

  await step(" «Ещё 5 минут»: та же работа ещё 5:00 без второго помидора; подсветка снята в окне и на обоях; «Сброс» — 25:00", async () => {
    await page.bringToFront(); await page.click("#pomoPlus");
    await waitFor(async () => (await pomoApi()).running, 4000, "«Ещё 5 минут» не дошло до сервера");
    const s = await pomoApi(); await sleep(400);
    const c = await card(page);
    assert(s.phase === "work" && s.duration === 300 && s.extra === 300 && !s.waiting && s.today === 1 && s.remaining > 290, "сервер: " + JSON.stringify(s));
    assert(!c.due && c.run && c.phase === "Работа" && /^0[45]:\d\d$/.test(c.time) && !c.plus && c.info && c.start === "Пауза" && !c.aria, "окно: " + JSON.stringify(c));
    const focus = await page.evaluate(() => document.activeElement && document.activeElement.id);
    assert(focus === "pomoStart", "фокус потерялся вместе со спрятанной кнопкой: " + focus);
    await wp.bringToFront();
    await waitFor(() => wp.evaluate(() => !document.getElementById("pomo").classList.contains("due") && document.getElementById("pomo").classList.contains("run")), 12000, "обои остались «Время вышло»");
    await page.bringToFront(); await page.click("#pomoReset");
    await waitFor(async () => { const x = await pomoApi(); return !x.running && x.phase === "work" && x.remaining === 1500; }, 4000, "«Сброс» не дошёл");
    await sleep(400);
    const r = await card(page);
    assert(!r.due && r.time === "25:00" && r.start === "Старт", "после «Сброса»: " + JSON.stringify(r));
    return { сервер: { phase: s.phase, duration: s.duration, today: s.today }, после: r.time };
  });

  await step(" «Перерыв» с обоев: после перезапуска программы «Время вышло» на месте; кнопка на обоях начинает перерыв 5:00 с этой минуты; окно показывает перерыв", async () => {
    await restartPomo({ phase: "short", cycle: 1, days: { [TODAY]: 1 }, waiting: { from: "work", at: Date.now() / 1000 - 40 } });
    await wp.bringToFront(); await wp.reload({ waitUntil: "networkidle2" }); await page.reload({ waitUntil: "networkidle2" });
    await waitFor(() => wp.evaluate(() => document.getElementById("pomo").classList.contains("due")), 8000, "обои: «Время вышло» после перезапуска не показано");
    await wp.click("#pomoStart");
    await waitFor(async () => (await pomoApi()).running, 4000, "«Перерыв» с обоев не дошёл до сервера");
    const s = await pomoApi(); await sleep(400);
    const c = await card(wp);
    assert(s.phase === "short" && s.running && !s.waiting && s.remaining > 295 && s.today === 1, "сервер: " + JSON.stringify(s));
    assert(!c.due && c.run && c.phase === "Перерыв" && /^0[45]:\d\d$/.test(c.time), "обои: " + JSON.stringify(c));
    await page.bringToFront();
    await waitFor(() => page.evaluate(() => document.getElementById("pomoPhase").textContent === "Перерыв"), 12000, "окно не узнало о перерыве");
    return { обои: c.phase + " " + c.time };
  });

  await step(" конец перерыва: «Время вышло», кнопка «Работа» («Работа: начать, 25 минут»), «Ещё 5 минут» — перерыва; нажатие начинает работу", async () => {
    await restartPomo({ phase: "work", cycle: 1, days: { [TODAY]: 1 }, waiting: { from: "short", at: Date.now() / 1000 - 5 } });
    await page.bringToFront(); await page.reload({ waitUntil: "networkidle2" });
    await waitFor(() => page.evaluate(() => document.getElementById("pomo").classList.contains("due")), 8000, "«Время вышло» нет");
    const c = await card(page), plusAria = await page.$eval("#pomoPlus", e => e.getAttribute("aria-label"));
    await shot(page, "п27-конец-перерыва-1600");
    assert(c.start === "Работа" && c.aria === "Работа: начать, 25 минут" && plusAria === "Ещё 5 минут перерыва" && /перерыв закончился/.test(c.title), JSON.stringify({ c, plusAria }));
    await page.click("#pomoStart");
    await waitFor(async () => { const x = await pomoApi(); return x.running && x.phase === "work"; }, 4000, "«Работа» не дошла");
    await page.click("#pomoReset"); await sleep(300);
    return { start: c.start, aria: c.aria };
  });

  await step(" выключатель звука в окне таймера: включён по умолчанию; снять — сервер sound=false (и после перезагрузки страницы галочка снята); включить снова", async () => {
    await page.bringToFront();
    const open = async () => { await page.click("#pomoMore"); await page.waitForSelector("#pomoPop:popover-open"); await sleep(200); };
    await open();
    const was = await page.$eval("#pmSound", e => e.checked), label = await page.$eval("#pmSound", e => e.parentElement.textContent.trim());
    await page.click("#pmSound");
    await waitFor(async () => (await pomoApi()).sound === false, 4000, "выключение звука не дошло до сервера");
    await page.keyboard.press("Escape"); await page.reload({ waitUntil: "networkidle2" }); await sleep(500);
    await open();
    const after = await page.$eval("#pmSound", e => e.checked);
    await shot(page, "п27-звук-выключен");
    await page.click("#pmSound");
    await waitFor(async () => (await pomoApi()).sound === true, 4000, "включение звука не дошло до сервера");
    await page.keyboard.press("Escape");
    assert(was === true && after === false && label === "Звук, когда время вышло", JSON.stringify({ was, after, label }));
    return { было: was, после_перезагрузки: after };
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
