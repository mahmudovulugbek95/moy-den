// 1.3.9в сдвиг шапки при загрузке, когда погода приходит после первой отрисовки.
// Причина (замер CDP): блок #wx появлялся через ~40–80 мс после первой отрисовки,
// колонка .dt сужалась (359 → 295 px на 1600), строка «Мой день · приветствие · Сотрудники» уходила во второй ряд,
// шапка росла на 33 px, весь #home ехал вниз — CLS 0,157–0,162 в любой час (не только вечером). На обоях ещё и масштаб
// ставился в конце страницы, после ответа настроек.
// Проверка (каждая загрузка — новая вкладка): ответ /api/weather задержан на 400 мс — погода гарантированно приходит после первых кадров.
//  1) погода пришла (иначе проверка пустая), кадров до неё ≥ 1 и с ней ≥ 5;
//  2) высота шапки одна во всех кадрах с момента, когда шапка разобрана (есть #home), до конца загрузки;
//  3) CLS сдвигов с участием шапки или #home ≤ 0,02; общий CLS обычного окна ≤ 0,1 (на обоях — в сведениях).
// Случаи: 08/14/20 ч × окно 1600, 1100, 1920×1040, обои экранов 1920×1080 и 2560×1440 (окно обоев — рабочая область без полосы
// значков 120 px и панели задач 48 px); «прогноза нет» (сервер отвечает ok:false) — место держится, сдвига нет; «погода выключена
// в настройках» — со второй загрузки места под погоду нет (шапка как без погоды), сдвига нет.
// Безголовый Chrome, временная папка с фикстурами (данные пользователя не трогает), окна на экран не выводит, ничего не удаляет.
// Запуск: node tests\cls_139.js [--cities 2] [--shots <папка>] [--out <json>] [--only <часть имени случая>] [--repeat N]
//         для RED и проб: [--html <файл страницы вместо дашборд.html>] [--inject-css "<css>"]
const puppeteer = require(process.env.PUPPETEER_PATH || "puppeteer-core");
process.env.MOYDEN_TZ = process.env.MOYDEN_TZ || "Africa/Nairobi"; // сервер считает дни в том же поясе, что и страница (emulateTimezone)
const fs = require("fs"), path = require("path"), http = require("http"), cp = require("child_process"), os = require("os");
const DASH = path.resolve(__dirname, "..");
const FX = require("./фикстуры_по_дате.js");
const arg = k => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : null; };
const CITIES = +(arg("--cities") || 1), INJECT = arg("--inject-css"), HTML = arg("--html"), SHOTS = arg("--shots"), ONLY = arg("--only"), REPEAT = +(arg("--repeat") || 1);
const OUT = arg("--out") || path.join(__dirname, "cls_139-результат.json");
const T = fs.mkdtempSync(path.join(os.tmpdir(), "moyden-cls139-"));
const PORT = 8676 + CITIES * 10, WX_PORT = PORT + 2, BASE = `http://127.0.0.1:${PORT}/`, PY = (process.env.PY || path.join(DASH, ".venv", "Scripts", "python.exe"));
fs.writeFileSync(path.join(T, "data.json"), FX.dataJson(fs.readFileSync(path.join(__dirname, "fixture-data.json"), "utf8")));
const st = JSON.parse(fs.readFileSync(path.join(__dirname, "fixture-settings.json"), "utf8").replace(/^\uFEFF/, ""));
st.weather.cities.forEach((c, i) => { c.enabled = i < CITIES; });
fs.writeFileSync(path.join(T, "settings.json"), JSON.stringify(st));
fs.writeFileSync(path.join(T, "cal.json"), '{"calendars": []}');
const tdy = () => new Intl.DateTimeFormat("sv-SE", { timeZone: "Africa/Nairobi" }).format(new Date());
const wxSrv = http.createServer((q, r) => {   // подмена Open-Meteo, как в e2e.js
  r.writeHead(200, { "Content-Type": "application/json" });
  r.end(JSON.stringify({ utc_offset_seconds: 10800, current: { temperature_2m: 23.6, weather_code: 2, is_day: 1 },
    daily: { time: [tdy()], weather_code: [2], temperature_2m_max: [26.9], temperature_2m_min: [14.1], precipitation_probability_max: [10] } }));
}).listen(WX_PORT);
const sleep = ms => new Promise(r => setTimeout(r, ms));
const srv = cp.spawn(PY, [path.join(DASH, "server.py"), "--port", String(PORT), "--data", path.join(T, "data.json"), "--calendars", path.join(T, "cal.json"),
  "--cache", path.join(T, "cache"), "--log", path.join(T, "server.log"), "--settings", path.join(T, "settings.json"),
  "--weather-url", `http://127.0.0.1:${WX_PORT}/v1/forecast`, "--weather-cache", path.join(T, "wx.json"), "--weather-refresh", "600",
  "--google-sync", path.join(T, "g.json")], { stdio: "ignore", env: { ...process.env, MOYDEN_TEST_ICAL: "1" } });
const stop = () => { try { cp.execSync(`taskkill /PID ${srv.pid} /T /F`, { stdio: "ignore" }); } catch {} wxSrv.close(); };
const results = [];
const step = async (name, fn) => { try { const info = await fn(); results.push({ name, ok: true, info }); console.log("PASS", name, JSON.stringify(info).slice(0, 260)); }
  catch (e) { results.push({ name, ok: false, error: String(e.message || e) }); console.log("FAIL", name, String(e.message || e).slice(0, 700)); } };
const assert = (c, m) => { if (!c) throw new Error(m); };

// mode: "погода" — прогноз есть; "нет прогноза" — сервер отвечает ok:false; "выключена" — в настройках weather.on=false
async function load(browser, { w, h, hour, mode, wp, keepMemory }, shot) {
  const page = await browser.newPage();
  await page.setViewport({ width: w, height: h }); await page.emulateTimezone("Africa/Nairobi");
  const t = new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Nairobi", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date()).split(":");
  await page.evaluateOnNewDocument((off, keep) => { if (!keep) try { localStorage.removeItem("wxOff"); } catch {}
    const R = Date; class Dt extends R { constructor(...a) { if (a.length) super(...a); else super(R.now() + off); } static now() { return R.now() + off; } } window.Date = Dt; },
    ((hour - +t[0]) * 60 - +t[1]) * 60000, !!keepMemory);
  await page.setRequestInterception(true);
  page.on("request", async r => {
    const u = r.url();
    if (u.includes("/api/weather")) {
      if (mode !== "погода") return r.respond({ status: 200, contentType: "application/json", body: '{"ok":false,"cities":[]}' });
      const body = await (await fetch(u)).text(); await sleep(400); return r.respond({ status: 200, contentType: "application/json", body });
    }
    if (mode === "выключена" && u.includes("/api/settings")) { const j = await (await fetch(u)).json(); j.weather = { on: false }; return r.respond({ status: 200, contentType: "application/json", body: JSON.stringify(j) }); }
    if ((INJECT || HTML) && r.resourceType() === "document") { const html = HTML ? fs.readFileSync(HTML, "utf8") : await (await fetch(u)).text();
      return r.respond({ status: 200, contentType: "text/html; charset=utf-8", body: INJECT ? html.replace("</head>", `<style>${INJECT}</style></head>`) : html }); }
    r.continue();
  });
  await page.evaluateOnNewDocument(() => {   // высота шапки на каждом кадре и CLS
    window.__hh = []; window.__cls = []; const H = () => document.querySelector("header");
    new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls.push({ v: e.value,
      hdr: (e.sources || []).some(s => s.node && s.node.nodeType === 1 && (s.node.id === "home" || !!s.node.closest("header"))),
      src: (e.sources || []).map(s => s.node && s.node.nodeType === 1 ? (s.node.id || s.node.className || s.node.tagName) : "?").slice(0, 5) }); }).observe({ type: "layout-shift", buffered: true });
    let n = 0; const raf = () => { const h = H(), wx = document.getElementById("wx");
      if (h && wx) window.__hh.push({ h: Math.round(h.getBoundingClientRect().height), wx: !wx.hidden && !!wx.textContent, full: !!document.getElementById("home") });
      if (++n < 150) requestAnimationFrame(raf); };
    requestAnimationFrame(raf);
  });
  await page.goto(BASE + (wp ? "?wallpaper=1&left=0&bottom=0" : ""), { waitUntil: "networkidle0" }); await sleep(1500); // адрес обоев — как у окна обоев в app.py
  const m = await page.evaluate(() => { const hl = document.querySelector(".hello"), r = e => { const b = e.getBoundingClientRect(); return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; };
    const F = window.__hh.filter(x => x.full);
    return { cls: +window.__cls.reduce((a, x) => a + x.v, 0).toFixed(4), cls_шапки: +window.__cls.filter(x => x.hdr).reduce((a, x) => a + x.v, 0).toFixed(4),
      сдвиги: window.__cls.filter(x => x.v > 0.001).map(x => ({ v: +x.v.toFixed(4), шапка: x.hdr, src: x.src })),
      погода: !document.getElementById("wx").hidden && !!document.getElementById("wx").textContent, высоты: [...new Set(F.map(x => x.h))],
      высоты_до_разбора_шапки: [...new Set(window.__hh.filter(x => !x.full).map(x => x.h))], кадров_до_погоды: F.filter(x => !x.wx).length, кадров_с_погодой: F.filter(x => x.wx).length,
      шапка: r(document.querySelector("header")), строк_приветствия: Math.round(hl.getBoundingClientRect().height / hl.firstElementChild.getBoundingClientRect().height),
      wx: r(document.getElementById("wx")), dt: r(document.querySelector(".dt")), zoom: document.documentElement.style.zoom || "1", hello: document.getElementById("hello").textContent }; });
  if (shot) { fs.mkdirSync(SHOTS, { recursive: true }); await page.screenshot({ path: path.join(SHOTS, shot), clip: { x: 0, y: 0, width: w, height: Math.min(h, wp ? 420 : 260) } }); }
  await page.close();
  return m;
}

(async () => {
  for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE + "api/ping")).ok) break; } catch {} await sleep(200); }
  for (let i = 0; i < 50; i++) { try { const j = await (await fetch(BASE + "api/weather")).json(); if (j.ok && j.cities.length === CITIES) break; } catch {} await sleep(200); } // прогноз в кэше сервера
  const browser = await puppeteer.launch({ executablePath: process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe", headless: true, args: ["--window-size=1600,900", "--lang=ru-RU"] });
  await require("./страж_localhost.js").install(browser, __filename);
  { // прогрев: первая загрузка в чистом профиле запоминает число городов (localStorage.wxN), как первая загрузка у пользователя после смены настроек
    const p = await browser.newPage(); await p.setViewport({ width: 1600, height: 900 }); await p.goto(BASE, { waitUntil: "networkidle0" }); await sleep(800); await p.close(); }
  const cases = [];
  const WIN = [[1600, 900, false], [1100, 800, false], [1920, 1040, false], [1800, 1032, true], [2440, 1392, true]];
  for (const hour of [8, 14, 20]) for (const [w, h, wp] of WIN) cases.push({ w, h, hour, mode: "погода", wp });
  for (const [w, h, wp] of [[1600, 900, false], [1100, 800, false], [1800, 1032, true]]) cases.push({ w, h, hour: 20, mode: "нет прогноза", wp });
  for (const [w, h, wp] of [[1600, 900, false], [1800, 1032, true]]) { cases.push({ w, h, hour: 14, mode: "выключена", wp, first: true }); cases.push({ w, h, hour: 14, mode: "выключена", wp, keepMemory: true }); }
  const name = c => `${c.wp ? "обои " : ""}${c.w}×${c.h}, ${c.hour}:00, ${c.mode}${c.first ? " (первая загрузка)" : c.keepMemory ? " (вторая загрузка)" : ""}, городов ${CITIES}`;
  for (let k = 0; k < REPEAT; k++) for (const c of cases.filter(c => !ONLY || name(c).includes(ONLY))) {
    const shot = SHOTS && k === 0 ? `шапка-${c.wp ? "обои-" : ""}${c.w}-${c.hour}ч-${c.mode.replace(/ /g, "-")}${c.first ? "-1" : c.keepMemory ? "-2" : ""}-${CITIES}г.png` : null;
    await step(`${name(c)}: шапка не сдвигается`, async () => {
      const m = await load(browser, c, shot);
      assert(m.погода === (c.mode === "погода"), `погода ${c.mode === "погода" ? "не пришла — проверка пустая" : "видна, хотя её нет"}: ${JSON.stringify(m)}`);
      if (c.mode === "погода") assert(m.кадров_до_погоды > 0 && m.кадров_с_погодой >= 5, `кадров до погоды ${m.кадров_до_погоды}, с погодой ${m.кадров_с_погодой} — сдвиг от погоды не измерен: ${JSON.stringify(m)}`);
      if (c.mode === "выключена" && !c.first) assert(m.wx[2] === 0, `погода выключена, а место под неё держится: ${JSON.stringify(m)}`);
      if (c.first) return m; // первая загрузка после выключения погоды: место снимается один раз, по ответу настроек — в сведениях
      assert(m.высоты.length === 1 && m.cls_шапки <= 0.02 && (c.wp || m.cls <= 0.1),
        `CLS шапки ${m.cls_шапки} (общий ${m.cls}), высоты шапки по кадрам ${JSON.stringify(m.высоты)}: ${JSON.stringify(m)}`);
      return m;
    });
  }
  await browser.close(); stop();
  const bad = results.filter(r => !r.ok).length;
  fs.writeFileSync(OUT, JSON.stringify({ когда: new Date().toISOString(), городов: CITIES, html: HTML, inject: INJECT, повторов: REPEAT, всего: results.length, упало: bad, шаги: results }, null, 1), "utf8");
  console.log(`ИТОГ: ${results.length - bad}/${results.length}`);
  // временная папка T (фикстуры, несколько КБ) остаётся в %TEMP%: тест ничего не удаляет
  process.exit(bad ? 1 : 0);
})().catch(e => { console.error(e); stop(); process.exit(2); });
