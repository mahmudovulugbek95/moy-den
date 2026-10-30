// сквозные проверки в безголовом Chrome (на экран ничего не выводится), свой порт 8673; настоящие данные не трогает.
// П. 30: блоки главного экрана «Приоритетные», «Срочные», «Ждём ответа», «Проекты» с длинными списками (13 проектов, по 12 задач) —
//   окно 1600×900, 1536×864, 1100×800 и обои 2560×1392: ни один элемент не спрятан; кнопка «Показать ещё N», N = числу элементов ниже края
//   (оракул — elementFromPoint по краям элемента, не код страницы); нажатия показывают каждый элемент; «В начало» возвращает; колесо прокручивает
//   список, заголовок блока и шапка стоят; элемент из нижней части открывается; после «Назад» проект виден; перерисовка не сбрасывает прокрутку.
// П. 31: часы календаря «День» и «Неделя» колесом — карточка на главном (1600, 1100), полноэкранный ⤢ (1600, 2000×1009), обои (главный и ⤢):
//   при открытии видно «сейчас»; колесо доходит до 00:00 и 24:00, события 06:00 и 22:00 становятся видны; календарь и шапка не двигаются;
//   перерисовка не сбрасывает прокрутку; событие из прокрученной части открывается; обои после простоя возвращают «сейчас» и начало списков.
// Запуск: node tests\e2e_1313.js [папка исходников] [--shots папка]
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
const RES = process.env.MOYDEN_E2E_OUT || path.join(__dirname, "e2e_1313-результат.json");
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
const T = fs.mkdtempSync(path.join(os.tmpdir(), "moyden-e2e1313-"));
const PORT = 8673, BASE = `http://127.0.0.1:${PORT}/`;
const TODAY = FX.today, AD = n => FX.addDays(TODAY, n);

const data = JSON.parse(FX.dataJson(fs.readFileSync(path.join(__dirname, "fixture-data.json"), "utf8")));
const NEWP = ["Лендинг Альфа", "Блог Бета", "Магазин Гамма", "Портфолио Дельта", "Промо Эпсилон", "Каталог Зета"];
NEWP.forEach((n, i) => data.projects.push({ id: "np" + i, name: n, stage: "эскиз", status: "ok", deadline: "", owner: "Борис", note: "" }));
data.projects.push({ id: "fin1", name: "Сданный проект 1", stage: "сдан", status: "done", deadline: "", owner: "Борис", note: "" });
data.projects.push({ id: "fin2", name: "Сданный проект 2", stage: "сдан", status: "done", deadline: "", owner: "Борис", note: "" });
let id = 1000;
NEWP.forEach((n, i) => data.tasks.push({ id: id++, project: "np" + i, title: `макеты страниц и компонентов, этап ${i + 1}`, whoId: 2, due: AD(5 + i), state: "work" }));
for (let i = 0; i < 12; i++) data.tasks.push({ id: id++, project: "portal", title: `Приоритет ${i + 1}: важная задача`, whoId: 1, due: AD(8), state: "work", star: true });
for (let i = 0; i < 12; i++) data.tasks.push({ id: id++, project: "zeta", title: `Срочная задача номер ${i + 1}`, whoId: 2, due: AD(-2), state: "work" });
for (let i = 0; i < 12; i++) data.tasks.push({ id: id++, project: "kedr", title: `Ждём ответа ${i + 1}`, whoId: 1, due: AD(9), state: "wait" });
data.nextId = id + 10;
fs.writeFileSync(path.join(T, "data.json"), JSON.stringify(data, null, 1));
fs.copyFileSync(path.join(__dirname, "fixture-settings.json"), path.join(T, "settings.json"));
fs.writeFileSync(path.join(T, "cal.json"), '{"calendars": [{"name": "Рабочий", "ical_url": ""}, {"name": "Личный", "ical_url": ""}]}');
const ev = (eid, title, a, b) => ({ id: eid, calendar: "work", title, start: `${TODAY}T${a}:00+03:00`, end: `${TODAY}T${b}:00+03:00`, allDay: false });
fs.writeFileSync(path.join(T, "google-events.json"), JSON.stringify({ synced_at: new Date().toISOString(), calendars: [{ id: "work", name: "Рабочий", color: "#5B6CF0" }],
  events: [ev("e-early", "Ранняя встреча", "06:00", "06:45"), ev("e-mid", "Встреча с ГИПом по ZETA", "13:00", "14:00"), ev("e-late", "Поздний звонок", "22:00", "22:45")] }));
const sleep = ms => new Promise(r => setTimeout(r, ms));
const readData = () => JSON.parse(fs.readFileSync(path.join(T, "data.json"), "utf8").replace(/^\uFEFF/, ""));
const api = async (method, p, body) => { const r = await fetch(BASE + p.replace(/^\//, ""), { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); return r.json(); };
let srv;
const results = [];
const ONLY = process.env.MOYDEN_E2E_ONLY ? new RegExp(process.env.MOYDEN_E2E_ONLY) : null;
async function step(name, fn) {
  if (ONLY && !ONLY.test(name)) return;
  try { const info = await fn(); results.push({ step: name, ok: true, info: info ?? null }); console.log("PASS", name, info ? JSON.stringify(info).slice(0, 700) : ""); }
  catch (e) { results.push({ step: name, ok: false, error: String(e && e.message || e) }); console.log("FAIL", name, String(e && e.message || e).slice(0, 1500)); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }
async function waitFor(fn, ms = 8000, msg = "не дождался") { const t = Date.now(); while (Date.now() - t < ms) { if (await fn()) return true; await sleep(150); } throw new Error(msg); }
const shot = async (pg, name) => { if (!SHOTS) return; await pg.screenshot({ path: path.join(SHOTS, name + ".png") }); };

// ---------- оракул списка: что видно глазом (elementFromPoint по верхнему и нижнему краю элемента), без функций страницы ----------
const LIST = (pg, lid) => pg.evaluate(lid => {
  const L = document.getElementById(lid), sec = L.closest("section"), R = e => e.getBoundingClientRect(), lr = R(L);
  const items = [...L.children].filter(e => e.matches(".proj,.task,.prow"));
  const hitOwn = (e, y) => { const r = R(e), x = r.left + Math.min(40, r.width / 2); const h = document.elementFromPoint(x, y); return !!h && e.contains(h); };
  const fully = e => { const r = R(e); return r.height > 0 && r.top >= lr.top - 1 && r.bottom <= lr.bottom + 1 && hitOwn(e, r.top + 3) && hitOwn(e, r.bottom - 3); };
  const key = e => { const a = e.matches("a") ? e : e.querySelector("a[href]"); return e.dataset.pid || (a && a.getAttribute("href")) || (e.querySelector(".t1") || e).textContent.trim().slice(0, 60); };  // href — у двух задач бывает один заголовок
  const below = items.filter(e => !fully(e) && R(e).top >= lr.top - 1);
  const btns = [...sec.querySelectorAll("button, [role=button]")].filter(b => b.offsetParent && /Показать ещё|В начало/.test(b.textContent));
  const more = btns.find(b => /Показать ещё/.test(b.textContent)), top = btns.find(b => /В начало/.test(b.textContent));
  const old = [...sec.querySelectorAll(".more-n")].find(e => e.offsetParent && /^и ещё/.test(e.textContent.trim()));
  return { items: items.length, спрятано: items.filter(e => e.hidden || !e.offsetParent).length, ниже: below.length, видно: items.filter(fully).map(key),
    ниже_ключи: below.map(key), кнопка: more ? more.textContent.trim() : null, N: more ? +((more.textContent.match(/\d+/) || [])[0]) : null, в_начало: top ? top.textContent.trim() : null,
    старое: old ? old.textContent.trim() : null, scrollTop: Math.round(L.scrollTop), прокрутка: L.scrollHeight > L.clientHeight + 1, overflow: getComputedStyle(L).overflowY,
    h2: +R(sec.querySelector("h2")).top.toFixed(1), блок: [+R(sec).top.toFixed(1), +R(sec).height.toFixed(1)], шапка: +R(document.querySelector("header")).height.toFixed(1),
    hash: location.hash };
}, lid);
let REV = 0;  // заметка всегда новая — данные меняются, перерисовка обязана быть
const markRender = (pg, sel) => pg.evaluate(sel => { const e = document.querySelector(sel); if (e) e.__m1313 = 1; return !!e; }, sel);
const waitRender = (pg, sel) => waitFor(() => pg.evaluate(sel => { const e = document.querySelector(sel); return !!e && !e.__m1313; }, sel), 15000, "перерисовки не было: " + sel);
const ITEM = lid => `#${lid} > .proj, #${lid} > .prow, #${lid} > .task`;
const clickText = async (pg, lid, re) => {  // кнопка блока по тексту; нажатие с паузой 90 мс, как рукой
  const h = await pg.evaluateHandle((lid, src) => { const sec = document.getElementById(lid).closest("section"), re = new RegExp(src);
    return [...sec.querySelectorAll("button, [role=button]")].find(b => b.offsetParent && re.test(b.textContent)) || null; }, lid, re.source);
  const el = h.asElement(); if (!el) return false;
  const b = await el.boundingBox(); await pg.mouse.move(b.x + b.width / 2, b.y + b.height / 2); await pg.mouse.down(); await sleep(90); await pg.mouse.up(); await sleep(350);
  return true;
};
const wheelAt = async (pg, sel, dy, times = 1) => {
  const b = await (await pg.$(sel)).boundingBox(); await pg.mouse.move(b.x + b.width / 2, b.y + Math.min(b.height / 2, 120));
  for (let i = 0; i < times; i++) { await pg.mouse.wheel({ deltaY: dy }); await sleep(120); }
  await sleep(450);
};

// ---------- оракул календаря ----------
const CAL = pg => pg.evaluate(() => {
  const sc = document.querySelector("#calBody .dwscroll"); if (!sc) return null;
  const R = e => e.getBoundingClientRect(), r = R(sc);
  const vis = el => { if (!el) return null; const b = R(el); return b.top >= r.top - 1 && b.bottom <= r.bottom + 1; };
  const ev = t => [...sc.querySelectorAll(".ev")].find(b => b.textContent.includes(t));
  const lbl = h => [...sc.querySelectorAll(".hrs span")].find(s => s.textContent.trim() === h);
  const cal = document.querySelector("section.cal");
  return { overflow: getComputedStyle(sc).overflowY, scrollHeight: sc.scrollHeight, clientHeight: sc.clientHeight, scrollTop: Math.round(sc.scrollTop),
    внизу: sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 2, сейчас: vis(sc.querySelector(".now")), ранняя_06: vis(ev("Ранняя")), поздний_22: vis(ev("Поздний")),
    "01:00": vis(lbl("01:00")), "23:00": vis(lbl("23:00")), панель: +R(document.querySelector(".calbar")).top.toFixed(1), календарь: [+R(cal).top.toFixed(1), +R(cal).height.toFixed(1)],
    шапка: +R(document.querySelector("header")).height.toFixed(1), hh: getComputedStyle(document.querySelector("#calBody .dw")).getPropertyValue("--hh").trim(),
    вид: (document.querySelector('.seg button[aria-pressed="true"]') || {}).dataset?.v || null };
});

(async () => {
  srv = cp.spawn((process.env.PY || path.join(DEV, ".venv", "Scripts", "python.exe")), [path.join(SRC, "server.py"), "--port", String(PORT), "--data", path.join(T, "data.json"),
    "--calendars", path.join(T, "cal.json"), "--cache", path.join(T, "cache"), "--settings", path.join(T, "settings.json"), "--log", path.join(T, "server.log"),
    "--weather-url", "http://127.0.0.1:9/none", "--weather-cache", path.join(T, "wx.json"), "--google-sync", path.join(T, "google-events.json"), "--refresh", "2"], { stdio: "ignore" });
  for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE + "api/ping")).ok) break; } catch {} await sleep(200); }
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--lang=ru-RU"], protocolTimeout: 180000 });
  await require("./страж_localhost.js").install(browser, __filename);
  const errs = [];
  const mk = async (w, h, q) => { const pg = await browser.newPage(); await pg.setViewport({ width: w, height: h }); await pg.emulateTimezone("Africa/Nairobi");
    pg.on("pageerror", e => errs.push((q || "окно") + ": " + String(e))); await pg.goto(BASE + (q || ""), { waitUntil: "networkidle2" });
    await waitFor(() => pg.evaluate(() => document.querySelectorAll(".proj").length > 0 && !!document.querySelector("#calBody .dw,#calBody .mgrid,#calBody table")), 10000, "страница не загрузилась"); await sleep(600); return pg; };
  const page = await mk(1600, 900);
  const wp = await mk(2560, 1392, "?wallpaper=1&left=0&bottom=0");
  const go = async (pg, hash) => { await pg.evaluate(h => { location.hash = h; }, hash); await sleep(700); };
  const places = [[page, 1600, 900, "окно 1600"], [page, 1536, 864, "окно 1536"], [page, 1100, 800, "окно 1100"], [wp, 2560, 1392, "обои"]];
  const setVp = async (pg, w, h) => { if (pg === page) { await pg.setViewport({ width: w, height: h }); await sleep(500); } await pg.bringToFront(); };

  // ================= =================
  for (const lid of ["projects", "prio", "hot", "wait"]) {
    await step(` «${lid}»: ничего не спрятано, «Показать ещё N» = числу элементов ниже края, нажатия показывают каждый, «В начало», колесо, шапка и блок стоят — 1600, 1536, 1100, обои`, async () => {
      const out = {}, bad = [];
      for (const [pg, w, h, nm] of places) {
        await setVp(pg, w, h); await go(pg, ""); await pg.evaluate(lid => { document.getElementById(lid).scrollTop = 0; }, lid); await sleep(500);
        const s0 = await LIST(pg, lid), seen = new Set(s0.видно), trail = [];
        const o = out[nm] = { до: { элементов: s0.items, спрятано: s0.спрятано, ниже: s0.ниже, кнопка: s0.кнопка, старое: s0.старое, overflow: s0.overflow } };
        if (s0.спрятано) bad.push(`${nm}: спрятано ${s0.спрятано} из ${s0.items} (${s0.старое || "без подписи"})`);
        if (!(s0.ниже > 0)) bad.push(`${nm}: тест без смысла — всё помещается (ниже ${s0.ниже})`);
        if (s0.N !== s0.ниже) bad.push(`${nm}: кнопка «${s0.кнопка}» — N ${s0.N}, ниже края ${s0.ниже}`);
        if (nm === "окно 1600" || nm === "окно 1536" || nm === "обои") await shot(pg, `п30-${lid}-${nm}-до`);
        // нажатия «Показать ещё N»: каждое показывает первый элемент, который был ниже; до «В начало»
        for (let k = 0; k < 20; k++) {
          const s = await LIST(pg, lid); if (s.N == null) break;
          const first = s.ниже_ключи[0];
          if (!(await clickText(pg, lid, /Показать ещё/))) break;
          const a = await LIST(pg, lid); a.видно.forEach(x => seen.add(x)); trail.push({ N: s.N, ниже: s.ниже, после_ниже: a.ниже, кнопка_после: a.кнопка || a.в_начало });
          if (!a.видно.includes(first)) bad.push(`${nm}: после «Показать ещё» не виден «${first}»`);
          if (a.N != null && a.N !== a.ниже) bad.push(`${nm}: после нажатия N ${a.N}, ниже края ${a.ниже}`);
          if (a.h2 !== s0.h2 || a.блок[0] !== s0.блок[0] || a.блок[1] !== s0.блок[1] || a.шапка !== s0.шапка) bad.push(`${nm}: сдвиг заголовка/блока/шапки ${JSON.stringify([s0.h2, a.h2, s0.блок, a.блок, s0.шапка, a.шапка])}`);
        }
        o.нажатия = trail;
        { const e0 = await LIST(pg, lid);  // перерисовка у самого конца списка — прокрутка не сжимается, «В начало» на месте
          await markRender(pg, ITEM(lid)); await api("PATCH", "api/projects/fin1", { note: "у конца " + (++REV) }); await waitRender(pg, ITEM(lid)); await sleep(400);
          const e1 = await LIST(pg, lid); o.перерисовка_у_конца = { было: e0.scrollTop, стало: e1.scrollTop, кнопка: e1.в_начало };
          if (Math.abs(e1.scrollTop - e0.scrollTop) > 1 || !e1.в_начало) bad.push(`${nm}: перерисовка у конца — scrollTop ${e0.scrollTop} → ${e1.scrollTop}, «В начало» ${e1.в_начало}`); }
        const end = await LIST(pg, lid);
        if (seen.size !== s0.items) bad.push(`${nm}: за нажатия видно ${seen.size} из ${s0.items}`);
        if (!end.в_начало || end.ниже !== 0) bad.push(`${nm}: в конце нет «В начало» или остались ниже (${end.ниже})`);
        if (nm === "окно 1600" || nm === "окно 1536" || nm === "обои") await shot(pg, `п30-${lid}-${nm}-конец`);
        // элемент из нижней части открывается так же, как обычный
        const lastKey = await pg.evaluate(lid => { const L = document.getElementById(lid), it = [...L.children].filter(e => e.matches(".proj,.task,.prow")).pop(); const a = it.matches("a") ? it : it.querySelector("a"); return a ? a.getAttribute("href") : null; }, lid);
        const lastA = await pg.evaluateHandle(lid => { const L = document.getElementById(lid), it = [...L.children].filter(e => e.matches(".proj,.task,.prow")).pop(); return (it.matches("a") ? it : it.querySelector("a")).querySelector(".t1"); }, lid);
        const lb = await lastA.asElement().boundingBox();
        await pg.mouse.move(lb.x + 10, lb.y + lb.height / 2); await pg.mouse.down(); await sleep(90); await pg.mouse.up(); await sleep(800);
        const opened = await pg.evaluate(() => location.hash);
        o.открыт = { href: lastKey, hash: opened };
        if (!lastKey || decodeURIComponent(opened) !== decodeURIComponent(lastKey)) bad.push(`${nm}: нижний элемент не открылся (${lastKey} → ${opened})`);
        await go(pg, ""); await sleep(400);
        if (lid === "projects") {  // после «Назад» проект, из которого вернулись, виден — и когда он открыт не из списка (календарь, ссылка), а список в начале
          const pid = (lastKey.match(/#\/p\/([^/?]+)/) || [])[1];
          await pg.evaluate(() => { document.getElementById("projects").scrollTop = 0; }); await sleep(300);
          await go(pg, lastKey); await go(pg, ""); await sleep(400);
          const back = await LIST(pg, lid);
          o.после_назад = { видно: back.видно.includes(pid), scrollTop: back.scrollTop };
          if (!back.видно.includes(pid)) bad.push(`${nm}: после «Назад» проект ${pid} не виден`);
        }
        if (lid === "projects" && nm === "окно 1600") {  // Tab по проектам — фокус не прячется под строкой внизу
          await pg.evaluate(() => { document.getElementById("projects").scrollTop = 0; }); await sleep(300);
          const st = await LIST(pg, lid), last = st.видно[st.видно.length - 1];
          await pg.focus(`#projects .proj[data-pid="${last}"]`); await pg.keyboard.press("Tab"); await sleep(400);
          const fv = await pg.evaluate(() => { const a = document.activeElement, r = a.getBoundingClientRect(), L = document.getElementById("projects");
            const hit = y => { const h = document.elementFromPoint(r.left + 20, y); return !!h && a.contains(h); };
            return { pid: a.dataset && a.dataset.pid, внутри: L.contains(a), виден: hit(r.top + 3) && hit(r.bottom - 3), scrollTop: L.scrollTop }; });
          o.tab = fv;
          if (!fv.внутри || !fv.pid || fv.pid === last || !fv.виден) bad.push(`${nm}: Tab — фокус ${JSON.stringify(fv)}`);
          await pg.evaluate(() => document.activeElement.blur());
        }
        // «В начало» → список с начала, снова «Показать ещё N»
        await pg.evaluate(lid => { const L = document.getElementById(lid); L.scrollTop = L.scrollHeight; }, lid); await sleep(300);
        await clickText(pg, lid, /В начало/);
        const b0 = await LIST(pg, lid);
        o.в_начало = { scrollTop: b0.scrollTop, кнопка: b0.кнопка };
        if (b0.scrollTop !== 0 || b0.N !== s0.N) bad.push(`${nm}: «В начало» → scrollTop ${b0.scrollTop}, кнопка «${b0.кнопка}» (было N ${s0.N})`);
        // колесо над списком: список уезжает, заголовок, блок и шапка стоят
        await wheelAt(pg, `#${lid}`, 300);
        const wv = await LIST(pg, lid);
        o.колесо = { scrollTop: wv.scrollTop, h2: [s0.h2, wv.h2], N: wv.N, ниже: wv.ниже };
        if (!(wv.scrollTop > 0) || wv.h2 !== s0.h2 || wv.блок[1] !== s0.блок[1] || wv.шапка !== s0.шапка) bad.push(`${nm}: колесо — scrollTop ${wv.scrollTop}, заголовок ${s0.h2}→${wv.h2}`);
        if (wv.N != null && wv.N !== wv.ниже) bad.push(`${nm}: после колеса N ${wv.N}, ниже ${wv.ниже}`);
        // перерисовка (данные изменились) не сбрасывает прокрутку
        await markRender(pg, ITEM(lid)); await api("PATCH", "api/projects/fin1", { note: "проверка перерисовки " + (++REV) }); await waitRender(pg, ITEM(lid)); await sleep(400);
        const rr = await LIST(pg, lid);
        o.перерисовка = { было: wv.scrollTop, стало: rr.scrollTop };
        if (Math.abs(rr.scrollTop - wv.scrollTop) > 1) bad.push(`${nm}: перерисовка сбросила прокрутку ${wv.scrollTop} → ${rr.scrollTop}`);
        await pg.evaluate(lid => { document.getElementById(lid).scrollTop = 0; }, lid);
      }
      await page.setViewport({ width: 1600, height: 900 });
      assert(!bad.length, bad.join(" | ") + " || " + JSON.stringify(out).slice(0, 1500));
      return out;
    });
  }

  // ================= =================
  const calPlaces = [[page, 1600, 900, "", "главный 1600"], [page, 1100, 800, "", "главный 1100"], [page, 1600, 900, "#/cal", "⤢ 1600"], [page, 2000, 1009, "#/cal", "⤢ 2000×1009"], [page, 1100, 800, "#/cal", "⤢ 1100"],
    [wp, 2560, 1392, "", "обои главный"], [wp, 2560, 1392, "#/cal", "обои ⤢"]];
  for (const view of ["day", "week"]) {
    await step(` вид «${view === "day" ? "День" : "Неделя"}»: колесо над сеткой — карточка на главном (1600, 1100), ⤢ (1600, 2000×1009), обои (главный, ⤢): при открытии видно «сейчас», доступны все 24 часа (06:00 и 22:00), календарь и шапка стоят, перерисовка не сбрасывает, событие открывается`, async () => {
      const out = {}, bad = [];
      for (const [pg, w, h, hash, nm] of calPlaces) {
        await setVp(pg, w, h); await go(pg, hash);
        await pg.click(`.seg button[data-v="${view}"]`); await sleep(300);
        await pg.click("#calToday"); await sleep(900);
        await pg.evaluate(() => { const c = document.querySelector("section.cal").getBoundingClientRect(); if (c.bottom > innerHeight) window.scrollBy(0, c.bottom - innerHeight + 4); }); await sleep(300);
        const s0 = await CAL(pg), o = out[nm] = { открытие: s0 };
        if (!s0) { bad.push(`${nm}: нет сетки`); continue; }
        if (s0.вид !== view) bad.push(`${nm}: вид ${s0.вид}`);
        if (s0.сейчас === false) bad.push(`${nm}: при открытии «сейчас» не видно`);
        if (!(s0.scrollHeight > s0.clientHeight + 1)) bad.push(`${nm}: прокручивать нечего (высота ${s0.scrollHeight} ≤ ${s0.clientHeight})`);
        await shot(pg, `п31-${view}-${nm.replace(/[⤢×]/g, "x")}-открытие`);
        await wheelAt(pg, "#calBody .dwscroll", 700, 8);
        const dn = await CAL(pg); o.вниз = dn;
        if (!dn.внизу || !dn["23:00"] || !dn.поздний_22) bad.push(`${nm}: колесо вниз — scrollTop ${s0.scrollTop}→${dn.scrollTop}, внизу ${dn.внизу}, 23:00 ${dn["23:00"]}, 22:00 ${dn.поздний_22}`);
        if (dn.панель !== s0.панель || dn.календарь[0] !== s0.календарь[0] || dn.календарь[1] !== s0.календарь[1] || dn.шапка !== s0.шапка) bad.push(`${nm}: сдвиг ${JSON.stringify([s0.панель, dn.панель, s0.календарь, dn.календарь, s0.шапка, dn.шапка])}`);
        await shot(pg, `п31-${view}-${nm.replace(/[⤢×]/g, "x")}-вниз`);
        // событие из прокрученной части открывается
        const lateBtn = await pg.evaluateHandle(() => [...document.querySelectorAll("#calBody .dwscroll .ev")].find(b => b.textContent.includes("Поздний")) || null);
        if (lateBtn.asElement()) {
          const lb = await lateBtn.asElement().boundingBox(); await pg.mouse.move(lb.x + lb.width / 2, lb.y + lb.height / 2); await pg.mouse.down(); await sleep(90); await pg.mouse.up(); await sleep(500);
          const pop = await pg.evaluate(() => { const p = document.getElementById("calPop"); return p.matches(":popover-open") ? p.textContent.slice(0, 80) : null; });
          o.событие = pop;
          if (!pop || !/Поздний/.test(pop)) bad.push(`${nm}: событие 22:00 не открылось (${pop})`);
          await pg.keyboard.press("Escape"); await sleep(300);
          await pg.evaluate(() => { const p = document.getElementById("calPop"); if (p.matches(":popover-open")) p.hidePopover(); });
        }
        // перерисовка не сбрасывает прокрутку (данные изменились → renderAll → renderCalendar)
        const before = (await CAL(pg)).scrollTop;
        await markRender(pg, "#calBody .dw"); await api("PATCH", "api/projects/fin1", { note: "календарь " + (++REV) }); await waitRender(pg, "#calBody .dw"); await sleep(400);
        const rr = await CAL(pg); o.перерисовка = { было: before, стало: rr.scrollTop };
        if (Math.abs(rr.scrollTop - before) > 1) bad.push(`${nm}: перерисовка сбросила прокрутку ${before} → ${rr.scrollTop}`);
        await wheelAt(pg, "#calBody .dwscroll", -700, 10);
        const up = await CAL(pg); o.вверх = up;
        if (up.scrollTop !== 0 || !up["01:00"] || !up.ранняя_06) bad.push(`${nm}: колесо вверх — scrollTop ${up.scrollTop}, 01:00 ${up["01:00"]}, 06:00 ${up.ранняя_06}`);
        await shot(pg, `п31-${view}-${nm.replace(/[⤢×]/g, "x")}-вверх`);
      }
      await page.evaluate(() => window.scrollTo(0, 0)); await page.setViewport({ width: 1600, height: 900 }); await go(page, ""); await go(wp, "");
      assert(!bad.length, bad.join(" | ") + " || " + JSON.stringify(out).slice(0, 1200));
      return Object.fromEntries(Object.entries(out).map(([k, v]) => [k, { hh: v.открытие && v.открытие.hh, высота: v.открытие && [v.открытие.scrollHeight, v.открытие.clientHeight], открытие: v.открытие && v.открытие.scrollTop, вниз: v.вниз && v.вниз.scrollTop, вверх: v.вверх && v.вверх.scrollTop }]));
    });
  }

  await step("обои после простоя: календарь снова показывает «сейчас», списки — с начала (idle=3)", async () => {
    const w2 = await mk(2560, 1392, "?wallpaper=1&left=0&bottom=0&idle=3"); await w2.bringToFront();
    const nowH = await w2.evaluate(() => +new Intl.DateTimeFormat("ru-RU", { timeZone: "Africa/Nairobi", hour: "2-digit", hour12: false }).format(new Date()));
    await wheelAt(w2, "#calBody .dwscroll", nowH < 12 ? 700 : -700, 10);
    await wheelAt(w2, "#projects", 400);
    const a = await CAL(w2), la = await LIST(w2, "projects");
    await sleep(6500);
    const b = await CAL(w2), lb = await LIST(w2, "projects");
    await w2.close();
    const r = { час: nowH, после_колеса: { сейчас: a.сейчас, scrollTop: a.scrollTop, проекты: la.scrollTop }, после_простоя: { сейчас: b.сейчас, scrollTop: b.scrollTop, проекты: lb.scrollTop } };
    assert(la.scrollTop > 0, "колесо не прокрутило проекты на обоях: " + JSON.stringify(r));
    assert(a.сейчас === false, "предусловие: после колеса «сейчас» должно уйти из вида: " + JSON.stringify(r));
    assert(b.сейчас === true && lb.scrollTop === 0, "после простоя: " + JSON.stringify(r));
    return r;
  });

  await step("«На паузе» — причина с точкой в конце не даёт «..»", async () => {
    const r0 = await api("PATCH", "api/projects/panda", { pause: { reason: "ждём решения заказчика." } });
    assert(r0.ok, "пауза не поставлена: " + JSON.stringify(r0).slice(0, 200));
    await page.bringToFront(); await page.setViewport({ width: 1600, height: 900 }); await sleep(3000); await go(page, "#/paused"); await sleep(600);
    const t = await page.$eval("#pzv", e => e.innerText);
    await go(page, ""); await api("PATCH", "api/projects/panda", { pause: null });
    const line = (t.match(/Причина:[^\n]*/) || [""])[0];
    assert(/Причина: ждём решения заказчика\./.test(t) && !/заказчика\.\./.test(t), "строка паузы: " + line);
    return { строка: line };
  });

  // ---------- L1, L2 — свой сервер (порт 8674) без событий вне 07–21, часы страницы сдвинуты ----------
  await step("⤢ 2000×1200 и обои ⤢ 2560×2000 при 14:00 (hh > 48) — прокрутка к 23:00, затем срок проекта сегодня (полоса «весь день» растёт, hh меняется), задача на сегодня выполнена, срок снят — сетка остаётся на вечере; L2: «Назад» из проекта — часы там же; L1: 23:52 — «сейчас» видно при открытии", async () => {
    const T2 = fs.mkdtempSync(path.join(os.tmpdir(), "moyden-e2e1313a-")), PORT2 = 8674, BASE2 = `http://127.0.0.1:${PORT2}/`;
    const d2 = JSON.parse(FX.dataJson(fs.readFileSync(path.join(__dirname, "fixture-data.json"), "utf8")));
    d2.tasks.push({ id: 1500, project: "zeta", title: "Задача на сегодня для ", whoId: 1, due: TODAY, state: "work" }); d2.nextId = 1600;
    fs.writeFileSync(path.join(T2, "data.json"), JSON.stringify(d2, null, 1));
    fs.copyFileSync(path.join(__dirname, "fixture-settings.json"), path.join(T2, "settings.json"));
    fs.writeFileSync(path.join(T2, "cal.json"), '{"calendars": [{"name": "Рабочий", "ical_url": ""}]}');
    fs.writeFileSync(path.join(T2, "google-events.json"), JSON.stringify({ synced_at: new Date().toISOString(), calendars: [{ id: "work", name: "Рабочий", color: "#5B6CF0" }],
      events: [ev("e2-mid", "Встреча днём", "13:00", "14:00")] }));
    const s2 = cp.spawn((process.env.PY || path.join(DEV, ".venv", "Scripts", "python.exe")), [path.join(SRC, "server.py"), "--port", String(PORT2), "--data", path.join(T2, "data.json"),
      "--calendars", path.join(T2, "cal.json"), "--cache", path.join(T2, "cache"), "--settings", path.join(T2, "settings.json"), "--log", path.join(T2, "server.log"),
      "--weather-url", "http://127.0.0.1:9/none", "--weather-cache", path.join(T2, "wx.json"), "--google-sync", path.join(T2, "google-events.json"), "--refresh", "2"], { stdio: "ignore" });
    const api2 = async (method, p2, body) => { const r = await fetch(BASE2 + p2, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); return r.json(); };
    const out = {}, bad = [], pages = [];
    try {
      for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE2 + "api/ping")).ok) break; } catch {} await sleep(200); }
      const at = hm => Date.parse(`${TODAY}T${hm}:00+03:00`) - Date.now();
      const mk2 = async (w, h, q, hm) => { const pg = await browser.newPage(); pages.push(pg); await pg.setViewport({ width: w, height: h }); await pg.emulateTimezone("Africa/Nairobi");
        await pg.evaluateOnNewDocument(off => { const R = Date; class Dt extends R { constructor(...a) { if (a.length) super(...a); else super(R.now() + off); } static now() { return R.now() + off; } } window.Date = Dt; }, at(hm));
        pg.on("pageerror", e2 => errs.push("" + q + ": " + String(e2))); await pg.goto(BASE2 + q, { waitUntil: "networkidle2" });
        await waitFor(() => pg.evaluate(() => document.querySelectorAll(".proj").length > 0 && !!document.querySelector("#calBody .dw")), 10000, "страница не загрузилась"); await sleep(600); return pg; };
      const hhOf = pg => pg.evaluate(() => parseFloat(getComputedStyle(document.querySelector("#calBody .dw")).getPropertyValue("--hh")));
      const change = async (pg, what, fn) => { await markRender(pg, "#calBody .dw"); await fn(); await waitRender(pg, "#calBody .dw"); await sleep(500); const c = await CAL(pg); return { что: what, hh: await hhOf(pg), "23:00": c["23:00"], scrollTop: c.scrollTop }; };
      for (const [w, h, q, nm] of [[2000, 1200, "", "⤢ 2000×1200"], [2560, 2000, "?wallpaper=1&left=0&bottom=0", "обои ⤢ 2560×2000"]]) {
        const pg = await mk2(w, h, q, "14:00"); await pg.bringToFront();
        await go(pg, "#/cal"); await pg.click('.seg button[data-v="day"]'); await sleep(300); await pg.click("#calToday"); await sleep(900);
        const hh0 = await hhOf(pg), c0 = await CAL(pg); const o = out[nm] = { hh_до: hh0, открытие: { сейчас: c0.сейчас, "23:00": c0["23:00"] } };
        if (!(hh0 > 48)) bad.push(`${nm}: тест без смысла — hh ${hh0} ≤ 48`);
        if (c0.сейчас === false) bad.push(`${nm}: при открытии «сейчас» не видно`);
        await wheelAt(pg, "#calBody .dwscroll", 700, 8);
        const c1 = await CAL(pg); o.вечер = { "23:00": c1["23:00"], scrollTop: c1.scrollTop };
        if (!c1["23:00"]) bad.push(`${nm}: колесо не довело до 23:00`);
        const steps = [await change(pg, "срок проекта сегодня", () => api2("PATCH", "api/projects/panda", { deadline: TODAY })),
          await change(pg, "задача на сегодня выполнена", () => api2("PATCH", "api/tasks/1500", { state: "done" })),
          await change(pg, "срок проекта снят", () => api2("PATCH", "api/projects/panda", { deadline: "" }))];
        o.шаги = steps;
        if (!steps.some(x => x.hh !== hh0)) bad.push(`${nm}: тест без смысла — hh не менялась (${steps.map(x => x.hh)})`);
        for (const x of steps) if (!x["23:00"]) bad.push(`${nm}: «${x.что}» (hh ${hh0}→${x.hh}) — сетка ушла с вечера (scrollTop ${x.scrollTop})`);
        await shot(pg, `окно-${nm.replace(/[⤢×]/g, "x")}`);
        await api2("PATCH", "api/tasks/1500", { state: "work" });
        if (q === "") {  // L2: главный → колесо к вечеру → проект → «Назад» — часы там же
          await go(pg, ""); await pg.click("#calToday"); await sleep(900); await wheelAt(pg, "#calBody .dwscroll", 700, 8);
          const b0 = await CAL(pg); await go(pg, "#/p/panda"); await sleep(400); await pg.goBack(); await sleep(1200);
          const b1 = await CAL(pg); out.L2 = { до: { "23:00": b0["23:00"], scrollTop: b0.scrollTop }, после: { "23:00": b1["23:00"], scrollTop: b1.scrollTop }, hash: await pg.evaluate(() => location.hash) };
          if (!b0["23:00"] || !b1["23:00"]) bad.push(`L2: «Назад» из проекта сбросил часы ${JSON.stringify(out.L2)}`);
        }
      }
      const pn = await mk2(1600, 900, "", "23:52"); await pn.bringToFront(); await pn.click("#calToday"); await sleep(900);  // L1
      const cn = await CAL(pn); out.L1 = { сейчас: cn.сейчас, внизу: cn.внизу, scrollTop: cn.scrollTop };
      await shot(pn, "часы-23-52");
      if (cn.сейчас !== true) bad.push(`L1: в 23:52 при открытии «сейчас» не видно ${JSON.stringify(out.L1)}`);
    } finally { for (const pg of pages) await pg.close().catch(() => {}); s2.kill(); }
    assert(!bad.length, bad.join(" | ") + " || " + JSON.stringify(out).slice(0, 1500));
    return out;
  });

  await step("ошибок страницы нет", async () => { assert(!errs.length, errs.join(" | ")); return { ошибок: 0 }; });
  await browser.close();
  srv.kill();
  const ok = results.filter(r => r.ok).length;
  fs.writeFileSync(RES, JSON.stringify({ src: SRC, ok, total: results.length, results }, null, 1));
  console.log(`ИТОГ: ${ok}/${results.length}`);
  process.exit(ok === results.length ? 0 : 1);
})().catch(e => { console.log("FAIL набор упал:", e && e.stack || e); try { srv && srv.kill(); } catch {} console.log("ИТОГ: 0/1"); process.exit(2); });
