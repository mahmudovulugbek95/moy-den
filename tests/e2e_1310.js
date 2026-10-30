// сквозные проверки в безголовом Chrome (на экран ничего не выводится).
// на обоях «Завершить проект» — окно showModal ставит курсор в дату, и клавиатура просится; Esc закрывает окно.
// долгое нажатие на открыватель; фокус из кода сразу после щелчка не по открывателю — 0 запросов (мутант «любой щелчок»);
// ошибки форм на обоях без клавиатуры курсор в поле не ставят; опись окон и всплывашек на обоях.
// три блока колонки равной высоты (пусто, 1, 14 задач; 1600, 1100, обои 2560×1392; колесо);
// страница календаря (заголовок виден, ни один текст не обрезан, одна сетка; 1600, 1366, 1100, обои); цвета кнопок таймера (контраст AA).
// Esc возвращает клавиатуру ровно 1 раз; статусы ok и ok pr — настоящей отрисовкой.
// Запуск: node tests\e2e_1310.js [папка исходников] [--shots папка]
// Копия фикстуры с датами от сегодня, свой порт 8662; настоящие данные не трогает; временная папка — в Корзину. Итог: e2e_1310-результат.json
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
const RES = process.env.MOYDEN_E2E_OUT || path.join(__dirname, "e2e_1310-результат.json");
if (SHOTS) fs.mkdirSync(SHOTS, { recursive: true });
const T = fs.mkdtempSync(path.join(os.tmpdir(), "moyden-e2e1310-"));
const PORT = 8662, BASE = `http://127.0.0.1:${PORT}/`;
fs.writeFileSync(path.join(T, "data.json"), FX.dataJson(fs.readFileSync(path.join(__dirname, "fixture-data.json"), "utf8")));
fs.copyFileSync(path.join(__dirname, "fixture-settings.json"), path.join(T, "settings.json"));
fs.writeFileSync(path.join(T, "cal.json"), '{"calendars": [{"name": "Рабочий", "ical_url": ""}, {"name": "Личный", "ical_url": ""}]}');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const readData = () => JSON.parse(fs.readFileSync(path.join(T, "data.json"), "utf8").replace(/^\uFEFF/, ""));
const api = async (method, p, body) => { const r = await fetch(BASE + p.replace(/^\//, ""), { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); return r.json(); };
let srv;
const results = [];
async function step(name, fn) {
  try { const info = await fn(); results.push({ step: name, ok: true, info: info ?? null }); console.log("PASS", name, info ? JSON.stringify(info).slice(0, 400) : ""); }
  catch (e) { results.push({ step: name, ok: false, error: String(e && e.message || e) }); console.log("FAIL", name, String(e && e.message || e).slice(0, 600)); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }
async function waitFor(fn, ms = 8000, msg = "не дождался") { const t = Date.now(); while (Date.now() - t < ms) { if (await fn()) return true; await sleep(150); } throw new Error(msg); }
const shot = async (pg, name, el) => { if (!SHOTS) return; const p = path.join(SHOTS, name + ".png"); if (el) { const h = await pg.$(el); if (h) await h.screenshot({ path: p }); } else await pg.screenshot({ path: p }); };
const ymd = d => new Date(Date.now() + d * 86400000).toLocaleDateString("sv-SE", { timeZone: "Africa/Nairobi" });

(async () => {
  srv = cp.spawn((process.env.PY || path.join(DEV, ".venv", "Scripts", "python.exe")), [path.join(SRC, "server.py"), "--port", String(PORT), "--data", path.join(T, "data.json"),
    "--calendars", path.join(T, "cal.json"), "--cache", path.join(T, "cache"), "--settings", path.join(T, "settings.json"), "--log", path.join(T, "server.log"),
    "--weather-url", "http://127.0.0.1:9/none", "--weather-cache", path.join(T, "wx.json"), "--google-sync", path.join(T, "нет.json")], { stdio: "ignore" });
  for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE + "api/ping")).ok) break; } catch {} await sleep(200); }
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--lang=ru-RU"], protocolTimeout: 90000 });
  await require("./страж_localhost.js").install(browser, __filename);
  const errs = [];
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 900 });
  await page.emulateTimezone("Africa/Nairobi");
  page.on("pageerror", e => errs.push(String(e)));
  await page.goto(BASE, { waitUntil: "networkidle2" });
  await waitFor(() => page.evaluate(() => document.querySelectorAll(".proj").length > 0));
  const go = async (pg, hash) => { await pg.evaluate(h => { location.hash = h; }, hash); await sleep(600); };

  // ---------- обои: программа отвечает «клавиатура у обоев» (kbdReply) — запросы записываются ----------
  const wp = await browser.newPage();
  await wp.setViewport({ width: 2560, height: 1392 });
  await wp.emulateTimezone("Africa/Nairobi");
  wp.on("pageerror", e => errs.push("обои: " + String(e)));
  const posts = [];
  await wp.setRequestInterception(true);
  wp.on("request", r => {
    if (r.method() === "POST" && /\/api\/app\//.test(r.url())) { posts.push({ url: r.url().replace(BASE, "/"), body: r.postData() }); return r.respond({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, focus: "wallpaper", released: true }) }); }
    r.continue();
  });
  await wp.goto(BASE + "?wallpaper=1&left=0&bottom=0", { waitUntil: "networkidle2" });
  await waitFor(() => wp.evaluate(() => document.querySelectorAll(".proj").length > 0));
  const asks = () => posts.filter(p => p.url === "/api/app/wallpaper-focus").map(p => JSON.parse(p.body).fid);
  const noKbd = async () => { await wp.evaluate(() => { document.activeElement && document.activeElement.blur(); window.dispatchEvent(new Event("blur")); }); await sleep(100); posts.length = 0; };
  const active = pg => pg.evaluate(() => { const a = document.activeElement; return a && a !== document.body ? (a.id || a.dataset.fid || a.tagName) : null; });

  await step("обои — «Завершить проект»: курсор в «Дата завершения» и запрос клавиатуры для finDate; Esc закрывает окно, клавиатура возвращается", async () => {
    await wp.bringToFront();
    await go(wp, "#/p/zeta"); await sleep(400);
    await noKbd();
    await wp.click("#pvFinish"); await sleep(700);
    const st = await wp.evaluate(() => ({ open: document.getElementById("dlg").open, a: document.activeElement && document.activeElement.id }));
    const a = asks();
    assert(st.open && st.a === "finDate", "окно и фокус: " + JSON.stringify(st));
    assert(a.includes("finDate"), "FAIL-сценарий курсор в дате, а клавиатуру не попросили; запросы: " + JSON.stringify(posts));
    await wp.keyboard.press("Escape"); await sleep(1100);  // ждём и обработчик клавиш (350 мс), и закрытие окна (400 мс)
    const closed = await wp.evaluate(() => !document.getElementById("dlg").open);
    assert(closed, "Esc не закрыл окно");
    const relEsc = posts.filter(p => p.url === "/api/app/wallpaper-release").length;
    assert(relEsc === 1, "после Esc клавиатура возвращается ровно 1 раз, а не 0 и не 2: " + JSON.stringify(posts));
    // окно закрыто мышью («Отмена») — клавиатура тоже возвращается программе пользователя
    await noKbd(); await wp.click("#pvFinish"); await sleep(700);
    const a2 = asks(); await wp.click("#dlgNo"); await sleep(900);
    const rel = posts.filter(p => p.url === "/api/app/wallpaper-release").length;
    assert(a2.includes("finDate") && rel === 1, "«Отмена» мышью: " + JSON.stringify({ a2, rel, posts }));
    return { запросы: a, закрыто: closed, esc_возврат: relEsc, отмена_мышью_возврат: rel };
  });

  await step("обои — долгое нажатие (1,7 с) на «+ Новый проект» всё равно просит клавиатуру для поля", async () => {
    await go(wp, ""); await noKbd();
    const b = await (await wp.$("#addProjBtn")).boundingBox();
    await wp.mouse.move(b.x + b.width / 2, b.y + b.height / 2); await wp.mouse.down(); await sleep(1700); await wp.mouse.up(); await sleep(600);
    const a = asks();
    const open = await wp.evaluate(() => !document.getElementById("newProj").hidden);
    assert(open && a.includes("newProjName"), "FAIL-сценарий " + JSON.stringify({ open, posts }));
    await wp.keyboard.press("Escape"); await sleep(400);
    return { запросы: a };
  });

  await step("щелчок не по открывателю, затем фокус в поле из кода в пределах 1,5 с — запросов 0", async () => {
    await go(wp, "#/people/4"); await sleep(400); await noKbd();
    const out = {};
    for (const sel of ["header #date", "#pomoMore", "#ppl [data-back]"]) {
      posts.length = 0;
      await wp.click(sel); await sleep(80);
      if (sel === "#pomoMore") { await wp.evaluate(() => document.getElementById("pomoPop").hidePopover()); }
      if (sel === "#ppl [data-back]") { await wp.evaluate(() => { location.hash = "#/people/4"; }); await sleep(500); }
      await wp.evaluate(() => document.querySelector('[data-fid="e-role"]').focus()); await sleep(500);
      out[sel] = asks().length;
      await wp.evaluate(() => document.activeElement.blur()); await sleep(100);
    }
    assert(Object.values(out).every(n => n === 0), "фокус из кода после щелчка не по открывателю попросил клавиатуру: " + JSON.stringify(out));
    await go(wp, "");
    return out;
  });

  await step("обои без клавиатуры — «Добавить» с пустым названием, ошибка этапа и пустой адрес календаря: текст ошибки есть, курсор в поле не ставится, запросов 0", async () => {
    const out = {};
    await go(wp, "#/p/portal"); await sleep(500); await noKbd();
    await wp.click("#atAdd"); await sleep(500);
    out.задача = { ошибка: await wp.$eval("#atErr", e => e.textContent), фокус: await active(wp), запросов: asks().length };
    await noKbd();
    await wp.evaluate(() => document.getElementById("msForm").scrollIntoView({ block: "center" })); await sleep(200);
    await wp.click('#msForm button:not([type=button])'); await sleep(500);
    out.этап = { ошибка: await wp.$eval("#msErr", e => e.textContent), фокус: await active(wp), запросов: asks().length };
    await go(wp, "#/google"); await sleep(600); await noKbd();
    await wp.click('.gcf[data-gc="Личный"] .btn'); await sleep(500);
    out.календарь = { ошибка: await wp.$eval('.gcf[data-gc="Личный"] .ferr', e => e.textContent), фокус: await active(wp), запросов: asks().length };
    await go(wp, "");
    const bad = Object.entries(out).filter(([, v]) => !v.ошибка || v.запросов || (v.фокус && /atTitle|msTitle|^url$|INPUT/.test(v.фокус)));
    assert(!bad.length, "FAIL-сценарий 1.3.9 (курсор в поле без клавиатуры): " + JSON.stringify(out));
    return out;
  });

  await step("обои с клавиатурой — «Добавить» с пустым названием ставит курсор в «новая задача», новых запросов нет", async () => {
    await go(wp, "#/p/portal"); await sleep(500);
    await wp.click("#atTitle"); await sleep(600);  // щелчок по полю — запрос, программа отвечает «клавиатура у обоев»
    const first = asks().length;
    posts.length = 0;
    await wp.click("#atAdd"); await sleep(500);
    const f = await active(wp);
    assert(first >= 1 && f === "atTitle" && asks().length === 0, JSON.stringify({ first, фокус: f, posts }));
    await wp.keyboard.press("Escape"); await sleep(500); await go(wp, "");
    return { фокус: f };
  });

  await step("опись окон и всплывашек на обоях: «Удалить сотрудника», «Таймер: задача и длительности», напоминание из слота календаря", async () => {
    const out = {};
    await go(wp, "#/people/4"); await sleep(400); await noKbd();
    await wp.click("#empDel"); await sleep(600);
    out.удалить = { окно: await wp.evaluate(() => document.getElementById("dlg").open), фокус: await active(wp), запросы: asks() };
    await wp.click("#dlgNo"); await sleep(300);
    await go(wp, ""); await noKbd();
    await wp.click("#pomoMore"); await sleep(500);
    out.таймер = { открыт: await wp.evaluate(() => document.getElementById("pomoPop").matches(":popover-open")), фокус: await active(wp), запросы: asks() };
    await wp.evaluate(() => document.getElementById("pomoPop").hidePopover()); await sleep(200);
    await noKbd();
    const col = await (await wp.$("#calBody .dcol")).boundingBox(), sc = await (await wp.$("#calBody .dwscroll")).boundingBox();
    await wp.mouse.click(col.x + col.width / 2, sc.y + sc.height / 2); await sleep(700);
    out.слот = { открыт: await wp.evaluate(() => document.getElementById("calPop").matches(":popover-open")), фокус: await active(wp), запросы: asks() };
    await wp.keyboard.press("Escape"); await sleep(400);
    await go(wp, "");
    const ok = out.удалить.окно && !out.удалить.запросы.length && out.таймер.открыт && !out.таймер.запросы.length
      && out.слот.открыт && out.слот.фокус === "remText" && out.слот.запросы.includes("remText");
    assert(ok, JSON.stringify(out));
    return out;
  });

  // ---------- таймер: цвета и состояния кнопок ----------
  const lum = c => { const [r, g, b] = c.match(/[\d.]+/g).slice(0, 3).map(v => { v = +v / 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const ratio = (a, b) => { const x = lum(a), y = lum(b); return +((Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)).toFixed(2); };
  const btnStyle = (pg, id) => pg.evaluate(id => { const e = document.getElementById(id), s = getComputedStyle(e); return { text: e.textContent.trim(), bg: s.backgroundColor, fg: s.color, dis: e.disabled, go: e.classList.contains("go") }; }, id);
  await step("таймер: «Старт» зелёный с белым текстом, «Сброс» светло-красный и неактивен, когда сбрасывать нечего; идёт — «Пауза» нейтральная; контраст ≥ 4,5 (окно и обои)", async () => {
    await page.bringToFront(); await go(page, "");
    await api("POST", "api/pomodoro", { action: "reset" }); await sleep(2500);
    const out = {};
    for (const [pg, nm] of [[page, "окно"], [wp, "обои"]]) {
      await pg.bringToFront(); await sleep(1200);
      const s = await btnStyle(pg, "pomoStart"), r = await btnStyle(pg, "pomoReset");
      out[nm + " стоит"] = { старт: s, сброс: r, контраст_старт: ratio(s.fg, s.bg), контраст_сброс: ratio(r.fg, r.bg) };
      await shot(pg, `таймер-${nm}-стоит`, "#pomo");
    }
    await page.bringToFront(); await page.click("#pomoStart"); await sleep(1500);
    const s1 = await btnStyle(page, "pomoStart"), r1 = await btnStyle(page, "pomoReset");
    out["окно идёт"] = { старт: s1, сброс: r1, контраст_пауза: ratio(s1.fg, s1.bg) };
    await shot(page, "таймер-окно-идёт", "#pomo");
    await page.click("#pomoStart"); await sleep(1200);  // пауза: прошло > 0 с — сбрасывать есть что
    const r2 = await btnStyle(page, "pomoReset");
    out["окно пауза"] = { сброс: r2 };
    await page.focus("#pomoReset"); await page.click("#pomoReset"); await sleep(1500);
    const r3 = await btnStyle(page, "pomoReset"), f3 = await active(page);
    out["после сброса"] = { сброс: r3, фокус: f3 };
    // статусы «Подключён» (ok) и «Подключён … не обновляется» (ok pr) — настоящей отрисовкой страницы (renderGoogle), не вставленным образцом
    // ответ /api/calendars подменён (два подключённых календаря, второй не обновляется) — страница рисует статусы сама (урок: переменные страницы из let не видны в evaluate)
    const fakeCal = JSON.stringify({ calendars: [{ name: "Рабочий", connected: true, address: "https://calendar.google.com/…/basic.ics" }, { name: "Личный", connected: true, address: "https://calendar.google.com/…/basic.ics", problem: "адрес не отвечает" }] });
    const onCal = r => { if (r.method() === "GET" && /\/api\/calendars(\?|$)/.test(r.url())) return r.respond({ status: 200, contentType: "application/json", body: fakeCal }); r.continue(); };
    await page.setRequestInterception(true); page.on("request", onCal);
    try {
    await page.evaluate(() => { location.hash = "#/google"; }); await sleep(1200);
    out.темы = await page.evaluate(() => {
      const res = {}, h = document.documentElement, was = h.dataset.theme;
      const ok = document.querySelector('#gcList .gcf[data-gc="Рабочий"] .ok:not(.pr)'), pr = document.querySelector('#gcList .gcf[data-gc="Личный"] .ok.pr');
      if (!ok || !pr) return { ошибка: "нет статусов ok / ok pr: " + document.getElementById("gcList").innerText.slice(0, 200) };
      for (const th of ["light", "dark"]) { h.dataset.theme = th; const c = id => { const e = document.getElementById(id), x = getComputedStyle(e); return [x.color, x.backgroundColor]; };
        const st = e => [getComputedStyle(e).color, getComputedStyle(e.closest(".gcf")).backgroundColor];
        res[th] = { старт: c("pomoStart"), сброс: c("pomoReset"), подключён: st(ok), не_обновляется: st(pr) }; }
      h.dataset.theme = was || "light"; return res; });
    } finally { page.off("request", onCal); await page.setRequestInterception(false); }
    await page.evaluate(() => { location.hash = ""; }); await sleep(700);
    assert(!out.темы.ошибка, JSON.stringify(out.темы));
    out.контраст_тем = Object.fromEntries(Object.entries(out.темы).map(([th, v]) => [th, Object.fromEntries(Object.entries(v).map(([k, [f, b]]) => [k, ratio(f, b)]))]));
    const st = out["окно стоит"], sw = out["обои стоит"];
    const green = c => c.replace(/\s/g, "") === "rgb(46,125,85)";
    const ok = [st, sw].every(x => x.старт.go && x.старт.text === "Старт" && green(x.старт.bg) && x.старт.fg.replace(/\s/g, "") === "rgb(255,255,255)"
      && x.контраст_старт >= 4.5 && x.сброс.dis && x.контраст_сброс >= 4.5 && /179,54,47/.test(x.сброс.fg.replace(/\s/g, "")))
      && s1.text === "Пауза" && !s1.go && !green(s1.bg) && out["окно идёт"].контраст_пауза >= 4.5 && !r1.dis
      && !r2.dis && r3.dis && f3 === "pomoStart"
      && Object.values(out.контраст_тем).every(v => Object.values(v).every(x => x >= 4.5));
    assert(ok, JSON.stringify(out));
    return out;
  });

  // ---------- страница календаря ----------
  const calCheck = pg => pg.evaluate(() => {
    const R = e => e.getBoundingClientRect(), card = document.querySelector("#gcv .pvcard"), body = document.querySelector("#gcv .gcbody"), cr = R(card);
    const vis = e => { const r = R(e); return r.top >= cr.top - 0.5 && r.bottom <= cr.bottom + 0.5 && r.left >= cr.left - 0.5 && r.right <= cr.right + 0.5 && r.height > 0; };
    const cut = [];
    for (const e of document.querySelectorAll("#gcv button, #gcv .btn, #gcv label, #gcv .gcap, #gcv .gill *, #gcv .m, #gcv .ok, #gcv h1, #gcv h2, #gcv p, #gcv .gcf input")) {
      const cs = getComputedStyle(e); if (cs.display === "inline" || e.closest("svg") || !e.clientWidth) continue;
      if (e.scrollWidth > e.clientWidth + 1) cut.push((e.className || e.tagName) + ": " + e.textContent.trim().slice(0, 30) + ` ${e.scrollWidth}>${e.clientWidth}`);
    }
    for (const g of document.querySelectorAll("#gcv .gill")) { const gr = R(g); for (const c of g.querySelectorAll("*")) { const r = R(c); if (r.width && (r.left < gr.left - 0.5 || r.right > gr.right + 0.5 || r.top < gr.top - 0.5 || r.bottom > gr.bottom + 0.5)) cut.push("за рамкой: " + (c.className.baseVal ?? c.className) + " " + c.textContent.trim().slice(0, 20)); } }
    const li = [...document.querySelectorAll("#gcv .gsteps > li")], caps = [...document.querySelectorAll("#gcv .gcap")], forms = [...document.querySelectorAll("#gcv .gcf")];
    const st = R(document.querySelector("#gcv .gsteps")), gl = R(document.getElementById("gcList"));
    const head = [document.getElementById("gcH"), document.querySelector("#gcv [data-back]")];
    const headTop = head.map(e => vis(e));
    body.scrollTop = body.scrollHeight; const headBottom = head.map(e => vis(e)); const scrolled = body.scrollTop; body.scrollTop = 0;
    return { обрезано: cut, заголовок_виден: headTop.every(Boolean), заголовок_виден_внизу: headBottom.every(Boolean), прокрутка_внутри: scrolled,
      картинок: document.querySelectorAll("#gcv .gsteps [role=img]").length,
      ширина_шагов: Math.round(st.width), ширина_форм: Math.round(gl.width), левый_край: [Math.round(st.left), Math.round(gl.left)],
      подписи_верх: [...new Set(caps.map(c => Math.round(R(c).top)))], шаги_высота: [...new Set(li.map(e => Math.round(R(e).height)))],
      формы_верх: [...new Set(forms.map(f => Math.round(R(f).top)))],
      гориз_прокрутка: Math.max(card.scrollWidth - card.clientWidth, body.scrollWidth - body.clientWidth) };
  });
  const calOk = (r, two) => !r.обрезано.length && r.заголовок_виден && r.заголовок_виден_внизу && r.картинок === 3 && Math.abs(r.ширина_шагов - r.ширина_форм) <= 2
    && Math.abs(r.левый_край[0] - r.левый_край[1]) <= 2 && r.подписи_верх.length === 1 && r.шаги_высота.length === 1 && r.гориз_прокрутка <= 0 && (!two || r.формы_верх.length === 1);
  await step("страница календаря: 1600×900, 1366×768, 1100×700 — заголовок и «Назад» видны целиком (и при прокрутке вниз), ни один текст не обрезан, мини-схемы внутри рамок, одна сетка шагов и форм", async () => {
    await page.bringToFront();
    const out = {};
    for (const [w, h] of [[1600, 900], [1366, 768], [1100, 700]]) {
      await page.setViewport({ width: w, height: h }); await go(page, "#/google");
      await page.waitForSelector('#gcv:not([hidden]) .gcf[data-gc="Личный"]'); await sleep(500);
      out[`${w}×${h}`] = await calCheck(page);
      if (w === 1100) {  // содержимое не помещается — прокручивается только .gcbody, колесом; заголовок стоит
        const b = await (await page.$("#gcv .gcbody")).boundingBox(), h0 = await page.$eval("#gcH", e => e.getBoundingClientRect().top);
        await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2); await page.mouse.wheel({ deltaY: 500 }); await sleep(500);
        out["1100 колесо"] = await page.evaluate(h0 => ({ scrollTop: document.querySelector("#gcv .gcbody").scrollTop, сдвиг_заголовка: document.getElementById("gcH").getBoundingClientRect().top - h0 }), h0);
      }
      await shot(page, `календарь-${w}x${h}`);
    }
    await page.setViewport({ width: 1600, height: 900 }); await go(page, "");
    const bad = Object.entries(out).filter(([k, r]) => k.includes("колесо") ? !(r.scrollTop > 0 && Math.abs(r.сдвиг_заголовка) < 0.5) : !calOk(r, true) || (k === "1100×700" && !(r.прокрутка_внутри > 0)));
    assert(!bad.length, JSON.stringify(Object.fromEntries(bad)));
    return out;
  });
  await step("страница календаря на обоях 2560×1392: заголовок виден, текст не обрезан, одна сетка", async () => {
    await wp.bringToFront(); await go(wp, "#/google"); await sleep(600);
    const r = await calCheck(wp);
    await shot(wp, "календарь-обои-2560");
    await go(wp, "");
    assert(calOk(r, true), JSON.stringify(r));
    return r;
  });

  // ---------- три блока колонки ----------
  const colCheck = pg => pg.evaluate(() => {
    const R = e => e.getBoundingClientRect(), doc = R(document.documentElement), bs = getComputedStyle(document.body).overflowY;
    const lim = bs === "hidden" ? doc.bottom : R(document.body).top + document.body.scrollHeight;  // окно ≤ 1100 px прокручивается целиком: блок не обрезан, если до него можно докрутить
    const ft = document.querySelector("body > footer"), ftop = ft ? R(ft).top : Infinity;  // подвал не налезает на блоки
    return [...document.querySelectorAll("#home > .col > section")].map(s => {
      const r = R(s), h = s.querySelector("h2"), hr = R(h), l = s.querySelector(".lst"), lr = R(l), em = l.querySelector(".empty");
      return { id: l.id, h: +r.height.toFixed(1), низ_в_окне: r.bottom <= lim + 0.5 && r.bottom <= ftop + 0.5, заголовок_виден: hr.top >= r.top - 0.5 && hr.bottom <= r.bottom + 0.5,
        прокрутка: l.scrollHeight > l.clientHeight + 1, пусто_целиком: em ? (l.scrollHeight <= l.clientHeight + 1 && R(em).bottom <= lr.bottom + 0.5) : null, задач: l.querySelectorAll(".task").length };
    });
  });
  const setBlocks = async ns => {  // ns — число задач в «Приоритетных», «Срочных», «Ждём ответа»
    const d = readData();
    for (const t of d.tasks) if (t.state !== "done" && (t.project !== "blk")) await api("PATCH", `api/tasks/${t.id}`, { state: "done" });
    for (const t of d.tasks) if (t.project === "blk" && t.state !== "done") await api("DELETE", `api/tasks/${t.id}`);
    for (let i = 0; i < ns[0]; i++) { const a = await api("POST", "api/tasks", { project: "blk", title: `Приоритет ${i + 1}`, due: ymd(5), state: "work" }); await api("PATCH", `api/tasks/${a.item ? a.item.id : a.id}`, { star: true }); }
    for (let i = 0; i < ns[1]; i++) await api("POST", "api/tasks", { project: "blk", title: `Срочная задача номер ${i + 1}`, due: ymd(-2), state: "work" });
    for (let i = 0; i < ns[2]; i++) await api("POST", "api/tasks", { project: "blk", title: `Ждём ответа ${i + 1}`, due: ymd(6), state: "wait" });
    await sleep(5500);
  };
  await step("три блока колонки равной высоты (±2 px): пусто, 1, 14 задач и разное наполнение 1/14/0 × 1600×900, 1100×800, обои 2560×1392; заголовок со счётчиком виден; пустой текст целиком; длинный список прокручивается колесом (и на обоях)", async () => {
    const pr = await api("POST", "api/projects", { name: "Блоки 1.3.10" }); const pid = (pr.item || pr).id;
    if (pid !== "blk") { const d = readData(); d.projects.find(p => p.id === pid).id = "blk"; fs.writeFileSync(path.join(T, "data.json"), JSON.stringify(d, null, 1)); await sleep(3000); }
    const out = {}, bad = [];
    for (const ns of [[0, 0, 0], [1, 1, 1], [14, 14, 14], [1, 14, 0]]) {  // и разное наполнение — жалоба пользователя была на него
      await setBlocks(ns); const n = ns.join("-");
      for (const [pg, w, h, nm] of [[page, 1600, 900, "1600"], [page, 1100, 800, "1100"], [wp, 2560, 1392, "обои"]]) {
        await pg.bringToFront(); if (pg === page) await pg.setViewport({ width: w, height: h }); await go(pg, ""); await sleep(900);
        const r = await colCheck(pg), hs = r.map(x => x.h);
        const k = `${n} задач, ${nm}`;
        out[k] = r;
        if (Math.max(...hs) - Math.min(...hs) > 2 || !r.every(x => x.заголовок_виден && x.низ_в_окне) || r.some(x => x.пусто_целиком === false)
          || r.some((x, i) => (ns[i] === 14) !== x.прокрутка || x.задач !== ns[i])) bad.push(k);
        if (ns[1] === 14) {  // колесо: список уезжает, заголовок со счётчиком стоит на месте
          const box = await (await pg.$("#hot")).boundingBox(), h0 = await pg.$eval("#hHot", e => e.getBoundingClientRect().top);
          await pg.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await pg.mouse.wheel({ deltaY: 400 }); await sleep(500);
          const s = await pg.evaluate(() => ({ top: document.getElementById("hot").scrollTop, h: document.getElementById("hHot").getBoundingClientRect().top }));
          out[k + " колесо"] = { scrollTop: s.top, заголовок_сдвиг: +(s.h - h0).toFixed(1) };
          if (!(s.top > 0) || Math.abs(s.h - h0) > 0.5) bad.push(k + " колесо");
          await pg.evaluate(() => { document.getElementById("hot").scrollTop = 0; });
        }
        await shot(pg, `блоки-${n}-${nm}`);
      }
    }
    await page.setViewport({ width: 1600, height: 900 });
    assert(!bad.length, "не так: " + bad.join("; ") + " | " + JSON.stringify(Object.fromEntries(bad.map(b => [b, out[b] || out[b.replace(" колесо", "")]]))));
    return Object.fromEntries(Object.entries(out).map(([k, v]) => [k, Array.isArray(v) ? v.map(x => x.h) : v]));
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
