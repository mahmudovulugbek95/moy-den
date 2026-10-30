// сквозные проверки в безголовом Chrome (на экран ничего не выводится): блок «Срочные» и слово «горит»; счётчики → список
// задач по проектам (☆, ○ и «Вернуть», Назад, Esc, «Задач нет»); таймер «Помидоро» (Старт/Пауза/Сброс, отсчёт от отметки сервера —
// замороженная страница и перезагрузка, привязка к задаче, длительности, конец отрезка, окно и обои — одно состояние); обои:
// запрос клавиатуры только для поля ввода (☆, карточка, пустое место, счётчик, кнопки таймера — без запроса); шапка не налезает.
// Запуск: node tests\e2e_138.js [папка исходников]
// Копия фикстуры с датами от сегодня, свой порт 8658; настоящие данные не трогает; временная папка — в Корзину. Итог: e2e_138-результат.json
const PUPPETEER = process.env.PUPPETEER_PATH || "puppeteer-core";
process.env.MOYDEN_TZ = process.env.MOYDEN_TZ || "Africa/Nairobi"; // сервер считает дни в том же поясе, что и страница (emulateTimezone)
const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const puppeteer = require(PUPPETEER);
const fs = require("fs"), path = require("path"), cp = require("child_process"), os = require("os");
const FX = require("./фикстуры_по_дате.js");
const DEV = path.resolve(__dirname, "..");
const SRC = path.resolve(process.argv[2] || DEV);
const T = fs.mkdtempSync(path.join(os.tmpdir(), "moyden-e2e138-"));
const PORT = 8658, BASE = `http://127.0.0.1:${PORT}/`;
fs.writeFileSync(path.join(T, "data.json"), FX.dataJson(fs.readFileSync(path.join(__dirname, "fixture-data.json"), "utf8")));
fs.copyFileSync(path.join(__dirname, "fixture-settings.json"), path.join(T, "settings.json"));
fs.writeFileSync(path.join(T, "cal.json"), '{"calendars": []}');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const readData = () => JSON.parse(fs.readFileSync(path.join(T, "data.json"), "utf8").replace(/^\uFEFF/, ""));
const task = id => readData().tasks.find(t => t.id === id);
const api = async (method, p, body) => { const r = await fetch(BASE + p.replace(/^\//, ""), { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }); return r.json(); };
const pomo = () => api("GET", "api/pomodoro").catch(() => ({}));
let srv;
const results = [];
const ONLY = process.env.MOYDEN_E2E_ONLY ? new RegExp(process.env.MOYDEN_E2E_ONLY) : null;  // разбор только выбранные шаги (повторы)
async function step(name, fn) {
  if (ONLY && !ONLY.test(name)) return;
  try { const info = await fn(); results.push({ step: name, ok: true, info: info ?? null }); console.log("PASS", name, info ? JSON.stringify(info).slice(0, 300) : ""); }
  catch (e) { results.push({ step: name, ok: false, error: String(e && e.message || e) }); console.log("FAIL", name, String(e && e.message || e).slice(0, 400)); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }
async function waitFor(fn, ms = 8000, msg = "не дождался") { const t = Date.now(); while (Date.now() - t < ms) { if (await fn()) return true; await sleep(150); } throw new Error(msg); }
const secOf = s => { const p = String(s || "").trim().split(":").map(Number); return p.some(isNaN) ? NaN : p.reduce((a, b) => a * 60 + b, 0); };

(async () => {
  srv = cp.spawn((process.env.PY || path.join(DEV, ".venv", "Scripts", "python.exe")), [path.join(SRC, "server.py"), "--port", String(PORT), "--data", path.join(T, "data.json"),
    "--calendars", path.join(T, "cal.json"), "--cache", path.join(T, "cache"), "--settings", path.join(T, "settings.json"), "--log", path.join(T, "server.log"),
    "--weather-url", "http://127.0.0.1:9/none", "--weather-cache", path.join(T, "wx.json"), "--google-sync", path.join(T, "нет.json")], { stdio: "ignore" });
  for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE + "api/ping")).ok) break; } catch {} await sleep(200); }
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--lang=ru-RU"], protocolTimeout: 60000 });
  await require("./страж_localhost.js").install(browser, __filename);
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 900 });
  await page.emulateTimezone("Africa/Nairobi");
  const errs = [];
  page.on("pageerror", e => errs.push(String(e)));
  const go = async hash => { await page.evaluate(h => { location.hash = h; }, hash); await sleep(500); };
  await page.goto(BASE, { waitUntil: "networkidle2" });
  await waitFor(() => page.evaluate(() => document.querySelectorAll(".proj").length > 0));

  // ---------- 23: «Срочные» ----------
  await step("23: блок называется «Срочные», пустой — «Срочных задач нет. Можно заняться важным.»", async () => {
    await go("");
    const h = await page.$eval("#hHot", e => e.childNodes[0].textContent.trim());
    assert(h === "Срочные", "заголовок блока: " + h);
    const urgent = [1, 2, 3, 90];
    for (const id of urgent) await api("PATCH", `api/tasks/${id}`, { state: "done" });
    await waitFor(() => page.$eval("#hot", e => e.innerText.trim() === "Срочных задач нет. Можно заняться важным."), 9000,
      "пустой блок: " + await page.$eval("#hot", e => e.innerText));
    for (const id of urgent) await api("PATCH", `api/tasks/${id}`, { state: "work" });
    await waitFor(() => page.$eval("#hot", e => e.querySelectorAll(".task").length === 4), 9000, "задачи не вернулись в «Срочные»");
    return { заголовок: h };
  });

  await step("23: слова «горит» нет нигде в интерфейсе (все экраны: текст, заголовок окна, подсказки, подписи для чтения с экрана)", async () => {
    const found = [];
    for (const h of ["", "#/p/portal", "#/paused", "#/done", "#/people", "#/people/1", "#/list/late", "#/list/today", "#/list/wait", "#/list/nodue", "#/google", "#/cal"]) {
      await go(h);
      const t = await page.evaluate(() => [document.title, document.body.innerText, ...[...document.querySelectorAll("[title],[aria-label],[placeholder]")].map(e => [e.title, e.getAttribute("aria-label"), e.getAttribute("placeholder")].join(" "))].join(" "));
      if (/горит|горящ/i.test(t)) found.push(h || "главный");
    }
    const src = fs.readFileSync(path.join(SRC, "дашборд.html"), "utf8");
    assert(!found.length && !/горит|горящ/i.test(src), "«горит» на экранах: " + found + (/горит/i.test(src) ? " и в исходнике страницы" : ""));
    await go("");
  });

  // ---------- 21: счётчики → список ----------
  const kpiClick = async (pg, i) => { await pg.evaluate(k => document.querySelectorAll("#sum .kpi")[k].click(), i); await sleep(600); };
  await step("21: все пять счётчиков — кнопки; «просрочено» → экран-список, сгруппированный по проектам: название, проект, исполнитель, срок", async () => {
    await go("");
    const tags = await page.$$eval("#sum .kpi", els => els.map(e => e.tagName + ":" + e.querySelector("span").textContent));
    assert(tags.length === 5 && tags.every(t => t.startsWith("BUTTON:")), "счётчики: " + tags);
    await kpiClick(page, 0);
    const v = await page.evaluate(() => ({ hash: location.hash, shown: !!document.querySelector("#lsv:not([hidden])"), h1: document.querySelector("#lsv h1")?.innerText,
      groups: [...document.querySelectorAll("#lsv h2")].map(h => h.childNodes[0].textContent.trim()),
      rows: [...document.querySelectorAll("#lsv .lrow")].map(r => ({ id: r.dataset.id, t: r.innerText.replace(/\s+/g, " ") })), title: document.title }));
    assert(v.hash === "#/list/late" && v.shown, "экран списка: " + JSON.stringify(v).slice(0, 300));
    assert(/^Просрочено/.test(v.h1) && /3 задачи/.test(v.h1), "заголовок: " + v.h1);
    assert(JSON.stringify(v.groups.slice().sort()) === JSON.stringify(["ZETA", "Магазин Портал", "Кедр"].sort()), "группы по проектам: " + v.groups);
    const r2 = v.rows.find(r => r.id === "2");
    assert(r2 && r2.t.includes("Разбор референсов") && r2.t.includes("ZETA") && r2.t.includes("Нина") && /просрочено на 1 день/.test(r2.t), "строка задачи: " + JSON.stringify(r2));
    assert(/Просрочено — Мой день/.test(v.title), "заголовок окна: " + v.title);
    return { группы: v.groups, строк: v.rows.length };
  });

  await step("21: «На сегодня», «Ждём ответа», «Без срока» — свои задачи; «На паузе» открывается как раньше; пустая категория — «Задач нет.»", async () => {
    const out = {};
    for (const [i, hash, id] of [[1, "#/list/today", "1"], [2, "#/list/wait", "91"], [3, "#/list/nodue", "92"]]) {
      await go(""); await kpiClick(page, i);
      const v = await page.evaluate(() => ({ hash: location.hash, ids: [...document.querySelectorAll("#lsv .lrow")].map(r => r.dataset.id) }));
      assert(v.hash === hash && JSON.stringify(v.ids) === JSON.stringify([id]), `${hash}: ${JSON.stringify(v)}`);
      out[hash] = v.ids;
    }
    await go(""); await kpiClick(page, 4);
    assert(await page.evaluate(() => location.hash === "#/paused" && !document.getElementById("pzv").hidden), "«На паузе» не открылся");
    await api("PATCH", "api/tasks/92", { state: "done" });
    await go("#/list/nodue");
    await waitFor(() => page.$eval("#lsv", e => e.innerText.includes("Задач нет.")), 9000, "пустая категория: " + await page.$eval("#lsv", e => e.innerText));
    await api("PATCH", "api/tasks/92", { state: "work" });
    return out;
  });

  await step("21: ☆ прямо в списке ставит и снимает звезду; ○ — «выполнено», строка уходит, «Вернуть» возвращает", async () => {
    await go(""); await kpiClick(page, 0);
    await page.click('#lsv .lrow[data-id="2"] .star');
    await waitFor(() => task(2).star === true, 5000, "звезда не сохранилась");
    await waitFor(() => page.$eval('#lsv .lrow[data-id="2"] .star', b => b.getAttribute("aria-pressed") === "true"), 5000, "кнопка звезды не нажата");
    await page.click('#lsv .lrow[data-id="2"] .star');
    await waitFor(() => !task(2).star, 5000, "звезда не снялась");
    await page.click('#lsv .lrow[data-id="3"] .ck');
    await waitFor(() => task(3).state === "done", 5000, "не отмечено выполненным");
    await waitFor(() => page.evaluate(() => !document.querySelector('#lsv .lrow[data-id="3"]')), 5000, "выполненная осталась в списке");
    const toast = await page.$eval("#toast", e => e.innerText);
    assert(/Вернуть/.test(toast), "нет «Вернуть»: " + toast);
    await page.click("#toast button");
    await waitFor(() => task(3).state === "work", 5000, "«Вернуть» не вернул");
    await waitFor(() => page.evaluate(() => !!document.querySelector('#lsv .lrow[data-id="3"]')), 5000, "вернувшаяся задача не в списке");
    return { toast };
  });

  await step("21: щелчок по задаче — в задачу в проекте; «Назад» — к списку; Esc и кнопка «Назад» — на главный", async () => {
    await go(""); await kpiClick(page, 0);
    await page.click('#lsv .lrow[data-id="3"] a.task');
    await waitFor(() => page.evaluate(() => location.hash === "#/p/kedr/t/3" && !document.getElementById("pv").hidden), 5000, "не открылась задача в проекте");
    const back = await page.$eval("#pv .backto", e => e.textContent);
    assert(/Просрочено/.test(back), "подпись «Назад»: " + back);
    await page.click("#pv .backbtn");
    await waitFor(() => page.evaluate(() => location.hash === "#/list/late"), 5000, "«Назад» не вернул к списку");
    await page.keyboard.press("Escape");
    await waitFor(() => page.evaluate(() => !document.getElementById("home").hidden), 5000, "Esc не вернул на главный");
    await kpiClick(page, 0);
    await page.click("#lsv .backbtn");
    await waitFor(() => page.evaluate(() => !document.getElementById("home").hidden), 5000, "кнопка «Назад» не вернула на главный");
    return { назад: back };
  });

  // ---------- 22: помидоро ----------
  const pv = pg => pg.evaluate(() => ({ phase: document.getElementById("pomoPhase")?.textContent, time: document.getElementById("pomoTime")?.textContent,
    btn: document.getElementById("pomoStart")?.textContent.trim(), info: document.getElementById("pomoInfo")?.textContent, run: document.getElementById("pomo")?.classList.contains("run") }));
  await step("22: таймер в шапке: «Работа 25:00», Старт → идёт (сервер), Пауза → стоит, Сброс → 25:00", async () => {
    await go("");
    let v = await pv(page);
    assert(v.phase === "Работа" && v.time === "25:00" && v.btn === "Старт", "начальное состояние: " + JSON.stringify(v));
    await page.click("#pomoStart");
    try {
      await waitFor(async () => (await pomo()).running === true, 4000, "Старт не дошёл до сервера");
      // разбор 1.3.12 (п5: «24:59» через 2,3 с): цифры перерисовываются раз в 500 мс от отметки сервера — снимок через фиксированные 2,3 с мог
      // попасть на прошлую перерисовку. Ждём состояние (идёт, ≤ 24:58); оракул — остаток самого сервера в тот же момент (±1,6 с: шаг 0,5 с + округление вверх)
      let x = null;
      await waitFor(async () => { x = await pv(page); return x.btn === "Пауза" && x.run && secOf(x.time) <= 1498; }, 4000).catch(() => { throw new Error("цифры не пошли за 4 с: " + JSON.stringify(x)); });
      const s = await pomo(); v = await pv(page);
      assert(s.running && v.btn === "Пауза" && v.run && secOf(v.time) >= 1494 && Math.abs(secOf(v.time) - s.remaining) <= 1.6, "идёт: " + JSON.stringify({ v, сервер: s.remaining }));
      await page.click("#pomoStart");
      await waitFor(async () => (await pomo()).running === false, 4000, "Пауза не дошла до сервера");
      await waitFor(async () => { const y = await pv(page); return !y.run && y.btn === "Старт"; }, 4000, "окно не показало паузу");  // ответ на «Пауза» дошёл до страницы
      const t1 = (await pv(page)).time; await sleep(1600);
      assert((await pv(page)).time === t1, "на паузе время идёт");
      await page.click("#pomoReset");
      await waitFor(async () => (await pv(page)).time === "25:00", 4000, "Сброс");
    } catch (e) {  // упавший шаг не оставляет таймер идущим: следующие шаги начинают с 25:00 (п5: каскад ещё на 3 шага)
      await api("POST", "api/pomodoro", { action: "reset" }).catch(() => {});
      await waitFor(async () => { const y = await pv(page); return y.time === "25:00" && !y.run; }, 5000).catch(() => {});
      throw e;
    }
    return v;
  });

  await step("22: отсчёт от отметки сервера: замороженная страница (свёрнутое окно) 6 с и перезагрузка окна не сбивают таймер", async () => {
    await page.click("#pomoStart");
    await waitFor(async () => (await pomo()).running, 4000, "не запустился");
    const cdp = await page.target().createCDPSession();
    await cdp.send("Page.setWebLifecycleState", { state: "frozen" });
    await sleep(6000);
    await cdp.send("Page.setWebLifecycleState", { state: "active" });
    await cdp.detach();
    await sleep(1200);
    const s1 = await pomo(), v1 = await pv(page);
    assert(Math.abs(secOf(v1.time) - s1.remaining) <= 1.6, `после заморозки страница ${v1.time}, сервер ${s1.remaining}`);
    await page.reload({ waitUntil: "networkidle2" }); await sleep(1500);
    const s2 = await pomo(), v2 = await pv(page);
    assert(v2.run && Math.abs(secOf(v2.time) - s2.remaining) <= 1.6 && s2.remaining < 1494, `после перезагрузки страница ${v2.time}, сервер ${s2.remaining}`);
    return { после_заморозки: v1.time, после_перезагрузки: v2.time, сервер: Math.round(s2.remaining) };
  });

  const wp = await browser.newPage();
  await wp.setViewport({ width: 2440, height: 1392 });
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
  const wgo = async hash => { await wp.evaluate(h => { location.hash = h; }, hash); await sleep(500); };

  await step("22: окно и обои показывают одно состояние (идёт, тот же остаток); Пауза на обоях — окно видит паузу", async () => {
    await sleep(2500);
    const near = async pg => { await pg.bringToFront(); await sleep(700); const v = await pv(pg), s = await pomo(); return { v, d: Math.abs(secOf(v.time) - s.remaining) }; };
    const a = await near(page), b = await near(wp);
    assert(a.v.run && b.v.run && a.d <= 1.6 && b.d <= 1.6, `окно ${JSON.stringify(a)}, обои ${JSON.stringify(b)}`);
    await wp.click("#pomoStart");
    await waitFor(async () => !(await pomo()).running, 4000, "Пауза с обоев не дошла до сервера");
    await page.bringToFront();
    await waitFor(async () => !(await pv(page)).run && (await pv(page)).btn === "Старт", 4500, "окно не увидело паузу с обоев");
    const c = await pv(page); await wp.bringToFront(); await sleep(300); const d = await pv(wp);
    assert(c.time === d.time, `на паузе окно ${c.time}, обои ${d.time}`);
    await page.bringToFront();
    await page.click("#pomoReset"); await sleep(400);
    return { окно: a.v.time, обои: b.v.time, пауза: c.time };
  });

  await step("22: привязка к задаче и длительности в настройках таймера; конец помидора — «Время вышло», кнопка «Перерыв» начинает перерыв; счётчики за день и по задаче", async () => {
    await page.bringToFront();
    await page.click("#pomoMore");
    await page.waitForSelector("#pomoPop:popover-open");
    await page.select("#pomoTask", "2");
    await waitFor(async () => (await pomo()).task === 2, 4000, "привязка не дошла до сервера");
    for (const [sel, v] of [["#pmWork", "1"], ["#pmShort", "1"], ["#pmLong", "2"], ["#pmEvery", "3"]]) { await page.click(sel, { clickCount: 3 }); await page.keyboard.type(v); }
    await page.click("#pomoSave");
    await waitFor(async () => JSON.stringify((await pomo()).durations) === JSON.stringify({ work: 1, short: 1, long: 2, every: 3 }), 4000, "длительности не сохранились");
    await page.click("#pmWork", { clickCount: 3 }); await page.keyboard.type("0"); await page.click("#pomoSave"); await sleep(500);
    const err = await page.$eval("#pomoErr", e => e.textContent);
    assert(/от 1 до 180/.test(err) && (await pomo()).durations.work === 1, "неверная длительность: «" + err + "»");
    await page.keyboard.press("Escape"); await sleep(300);
    assert((await pv(page)).time === "01:00", "новая длительность на экране: " + (await pv(page)).time);
    await page.click("#pomoStart");
    await waitFor(async () => (await pomo()).phase === "short", 70000, "помидор не закончился");
    // конец помидора — «Время вышло»: таймер стоит на перерыве и ждёт кнопку, перерыв сам не идёт; помидор засчитан
    let y = null;
    await waitFor(async () => { y = await pv(page); return y.phase === "Время вышло"; }, 5000).catch(() => { throw new Error("«Время вышло» не показано: " + JSON.stringify(y)); });
    let v = await pv(page), s = await pomo();
    assert(s.waiting && !s.running && s.phase === "short" && v.btn === "Перерыв" && !v.run && s.today === 1 && s.taskCount === 1,
      "после помидора: " + JSON.stringify({ v, сервер: { waiting: s.waiting, running: s.running, phase: s.phase, today: s.today, taskCount: s.taskCount } }));
    assert(v.info === "Помидоров сегодня: 1", "счётчик за день: " + v.info);
    await page.click("#pomoStart");  // кнопка «Перерыв» начинает перерыв
    await waitFor(async () => { const x = await pomo(); return x.running && x.phase === "short" && !x.waiting; }, 4000, "«Перерыв» не начал перерыв");
    await waitFor(async () => { y = await pv(page); return y.run && y.phase === "Перерыв"; }, 4000).catch(() => { throw new Error("окно не показало перерыв: " + JSON.stringify(y)); });
    v = await pv(page); s = await pomo();
    assert(s.today === 1 && s.taskCount === 1, "перерыв не считается помидором: " + JSON.stringify({ today: s.today, taskCount: s.taskCount }));
    await page.click("#pomoMore"); await page.waitForSelector("#pomoPop:popover-open");
    const pop = await page.$eval("#pomoPop", e => e.innerText);
    assert(/По этой задаче: 1 помидор/.test(pop), "счётчик по задаче: " + pop);
    await page.keyboard.press("Escape");
    await page.click("#pomoReset");
    await api("POST", "api/pomodoro", { action: "settings", durations: { work: 25, short: 5, long: 15, every: 4 } });
    await api("POST", "api/pomodoro", { action: "task", task: null });
    return { фаза: v.phase, инфо: v.info };
  });

  // ---------- шапка ----------
  const headerCheck = async (pg, label) => {
    await pg.evaluate(() => { const wx = document.getElementById("wx"); wx.hidden = false; // погода показана (в тесте адрес погоды недоступен)
      wx.innerHTML = '<span class="wxi"><svg viewBox="0 0 40 40"></svg></span><div><div class="wxt"><span>Переменная облачность,</span><b>24°</b></div><div class="wxd">Днём до 27°, ночью до 14°, вероятность осадков 10%</div></div>'; });
    await sleep(300);
    const r = await pg.evaluate(() => { const h = document.querySelector("header"), kids = [...h.children].filter(e => !e.hidden && e.getBoundingClientRect().width > 0);
      const rs = kids.map(e => { const b = e.getBoundingClientRect(); return { id: e.id || e.className, l: b.left, r: b.right, t: b.top, b: b.bottom }; });
      const over = []; for (let i = 0; i < rs.length; i++) for (let j = i + 1; j < rs.length; j++) if (rs[i].l < rs[j].r - 1 && rs[j].l < rs[i].r - 1 && rs[i].t < rs[j].b - 1 && rs[j].t < rs[i].b - 1) over.push(rs[i].id + "×" + rs[j].id);
      const inner = kids.concat([...h.querySelectorAll("#pomo *, #sum *, #wx *")]).filter(e => e.scrollWidth > Math.ceil(e.getBoundingClientRect().width) + 1).map(e => e.id || e.className); // текст за границей блока
      const dt = document.querySelector(".dt").getBoundingClientRect().height;
      return { over, sw: h.scrollWidth, cw: h.clientWidth, pomo: !!document.querySelector("#pomo") && document.querySelector("#pomo").getBoundingClientRect().width > 100, inner, dt: Math.round(dt) }; });
    assert(r.pomo && !r.over.length && !r.inner.length && r.sw <= r.cw + 1, `${label}: ${JSON.stringify(r)}`);
    return r;
  };
  await step("шапка 1600×900 и 1100×700 (наименьшее окно): часы, таймер, погода и счётчики не налезают друг на друга", async () => {
    await page.bringToFront();
    await go(""); await headerCheck(page, "1600");
    await page.setViewport({ width: 1100, height: 700 }); await sleep(600);
    const r = await headerCheck(page, "1100");
    await page.setViewport({ width: 1600, height: 900 }); await sleep(400);
    return r;
  });

  // ---------- обои: и список ----------
  await step("обои: шапка 2440×1392 не налезает; таймер виден", async () => { await wp.bringToFront(); return headerCheck(wp, "обои"); });

  await step("обои — щелчок по ☆, карточке проекта, пустому месту, счётчику, кнопкам таймера не просит клавиатуру; по полю — просит", async () => {
    await wgo("");
    await api("PATCH", "api/tasks/1", { star: true }); await sleep(4800);
    posts.length = 0;
    const clicks = [];
    const tap = async (sel, name) => { const before = posts.length; await wp.click(sel); await sleep(450); clicks.push({ name, запросов: posts.length - before }); };
    await tap("#prio .prow .star", "☆ в «Приоритетных»");
    await waitFor(() => !task(1).star, 5000, "☆ на обоях не сработала");
    await tap("#hot .task .t1", "задача в «Срочных»"); await wgo("");
    await tap("header #date", "пустое место шапки");
    await tap("#pomoStart", "Старт таймера"); await tap("#pomoStart", "Пауза таймера"); await tap("#pomoReset", "Сброс таймера");
    await tap("#sum .kpi", "счётчик"); await wgo("");
    await tap('.proj[data-pid="portal"] .t1', "карточка проекта");
    const noKbd = clicks.filter(c => c.запросов);
    assert(!posts.some(p => p.url === "/api/app/wallpaper-focus"), "FAIL-сценарий клавиатуру просили без поля: " + JSON.stringify(noKbd));
    await wgo("#/people/4");
    await wp.click('[data-fid="e-role"]'); await sleep(450);
    const f = posts.find(p => p.url === "/api/app/wallpaper-focus");
    assert(f && JSON.parse(f.body).fid === "e-role", "по полю запроса нет: " + JSON.stringify(posts));
    await wp.keyboard.press("Escape"); await sleep(500);
    return clicks;
  });

  await step("обои — «+ Новый проект» открывает поле, и клавиатура просится для него (поле, открытое щелчком)", async () => {
    await wgo("");
    posts.length = 0;
    await wp.click("#addProjBtn"); await sleep(600);
    const f = posts.filter(p => p.url === "/api/app/wallpaper-focus").map(p => JSON.parse(p.body).fid);
    assert(f.includes("newProjName"), "для поля нового проекта клавиатуру не попросили: " + JSON.stringify(posts));
    await wp.keyboard.press("Escape"); await sleep(400);
    return { запросы: f };
  });

  await step("обои — поле, получившее фокус из кода без щелчка (перерисовка, таймер), клавиатуру не просит", async () => {
    await wp.bringToFront();
    await wgo("#/people/4");
    await sleep(2000);
    posts.length = 0;
    await wp.evaluate(() => document.querySelector('[data-fid="e-role"]').focus());
    await sleep(700);
    assert(!posts.some(p => p.url === "/api/app/wallpaper-focus"), "фокус из кода без щелчка попросил клавиатуру: " + JSON.stringify(posts));
    await wp.evaluate(() => document.activeElement.blur()); await wgo("");
    return { запросов: posts.length };
  });

  await step("21 на обоях: счётчик → список, ☆ в списке работает", async () => {
    await wgo("");
    await kpiClick(wp, 0);
    assert(await wp.evaluate(() => location.hash === "#/list/late" && !document.getElementById("lsv").hidden), "список на обоях не открылся");
    await wp.click('#lsv .lrow[data-id="90"] .star');
    await waitFor(() => task(90).star === true, 5000, "☆ на обоях в списке");
    await wp.click('#lsv .lrow[data-id="90"] .star');
    await waitFor(() => !task(90).star, 5000, "снять ☆ на обоях");
    await wp.keyboard.press("Escape"); await sleep(500);
    return { ok: true };
  });

  await step("1.3.9 обои — ○ и «удалить» единственной задачи проекта: запросов клавиатуры 0, курсор не в «новая задача»", async () => {
    await wp.bringToFront();
    const out = {};
    for (const [pid, id, sel] of [["lumen", 92, ".ck"], ["panda", 91, ".del"]]) {
      await wgo("#/p/" + pid); await sleep(700);
      posts.length = 0;
      await wp.hover(`#openList .trow[data-id="${id}"]`);
      await wp.click(`#openList .trow[data-id="${id}"] ${sel}`);
      await waitFor(() => sel === ".ck" ? task(id).state === "done" : !task(id), 5000, `${sel} не сработал`);
      await sleep(900);
      out[pid] = { запросов: posts.filter(p => p.url === "/api/app/wallpaper-focus").length,
        фокус: await wp.evaluate(() => document.activeElement && (document.activeElement.id || document.activeElement.dataset.fid || document.activeElement.tagName)) };
    }
    await api("PATCH", "api/tasks/92", { state: "work" });
    await wgo("");
    assert(!out.lumen.запросов && !out.panda.запросов && out.lumen.фокус !== "atTitle" && out.panda.фокус !== "atTitle", "FAIL-сценарий " + JSON.stringify(out));
    return out;
  });

  await step("1.3.9 шапка не сдвигается при загрузке в 08:00, 14:00 и 20:00 — приветствие верное до первой отрисовки", async () => {
    const out = {};
    for (const H of [8, 14, 20]) {
      const p = await browser.newPage(); await p.setViewport({ width: 1600, height: 900 }); await p.emulateTimezone("Africa/Nairobi");
      const t = new Intl.DateTimeFormat("en-GB", { timeZone: "Africa/Nairobi", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date()).split(":");
      const off = ((H - +t[0]) * 60 - +t[1]) * 60000;  // тот же день, время на странице — H:00
      await p.evaluateOnNewDocument(off => { const R = Date; class Dt extends R { constructor(...a) { if (a.length) super(...a); else super(R.now() + off); } static now() { return R.now() + off; } } window.Date = Dt;
        window.__cls = 0; new PerformanceObserver(l => { for (const e of l.getEntries()) if (!e.hadRecentInput) window.__cls += e.value; }).observe({ type: "layout-shift", buffered: true }); }, off);
      await p.goto(BASE, { waitUntil: "networkidle2" }); await sleep(2500);
      out[H] = await p.evaluate(() => ({ cls: +window.__cls.toFixed(4), hello: document.getElementById("hello").textContent }));
      await p.close();
    }
    const want = { 8: "Доброе утро", 14: "Добрый день", 20: "Добрый вечер" };
    await page.bringToFront();
    assert(Object.entries(out).every(([h, v]) => v.cls < 0.02 && v.hello.startsWith(want[h])), "сдвиг шапки или приветствие: " + JSON.stringify(out));
    return out;
  });

  // ---------- ----------
  await step("сотрудник — Esc снимает подсказку «Не сохранено…» вместе с отметкой поля", async () => {
    await page.bringToFront();
    await go("#/people/4");
    // отметку ставим так же, как saveDraft при неудачной записи (перерисовка экрана сотрудника её стирает — поэтому прямо перед Esc)
    await page.click('[data-fid="e-role"]', { clickCount: 3 }); await page.keyboard.type("черновик");
    await page.$eval('[data-fid="e-role"]', e => { e.classList.add("unsaved"); e.title = "Не сохранено: сохранится, когда связь вернётся"; });
    await page.keyboard.press("Escape"); await sleep(400);
    const v = await page.$eval('[data-fid="e-role"]', e => ({ cls: e.classList.contains("unsaved"), title: e.title }));
    assert(!v.cls && !v.title, "после Esc: " + JSON.stringify(v));
    await sleep(1500);
    return v;
  });

  await browser.close();
  try { cp.execSync(`taskkill /PID ${srv.pid} /T /F`, { stdio: "ignore" }); } catch {}
  const ok = results.filter(r => r.ok).length;
  if (errs.length) console.log("ошибки страницы:", errs.slice(0, 5));
  fs.writeFileSync(path.join(__dirname, "e2e_138-результат.json"), JSON.stringify({ исходники: SRC, когда: new Date().toISOString(), results, errs }, null, 1));
  await sleep(500);
  try { cp.execFileSync((process.env.PY || path.join(DEV, ".venv", "Scripts", "python.exe")), [path.join(__dirname, "в_корзину.py"), T], { stdio: "ignore" }); } catch {}
  console.log(`ИТОГ: ${ok}/${results.length}`);
  process.exit(ok === results.length && !errs.length ? 0 : 1);
})().catch(e => { console.error("ОШИБКА", e); try { cp.execSync(`taskkill /PID ${srv.pid} /T /F`, { stdio: "ignore" }); } catch {} process.exit(2); });
