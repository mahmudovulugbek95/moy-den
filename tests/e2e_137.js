// сквозные проверки в безголовом Chrome — экран сотрудника (набор не стирается новыми данными, Enter/Esc, отчество),
// форма «Добавить задачу» (пустая при открытии и после добавления), обои (запросы клавиатуры программе, данные догоняют).
// Запуск: node tests\e2e_137.js [папка исходников]
// Работает на копии фикстуры, свой порт 8656; настоящие данные не трогает. Итог: tests\e2e_137-результат.json
const PUPPETEER = process.env.PUPPETEER_PATH || "puppeteer-core";
process.env.MOYDEN_TZ = process.env.MOYDEN_TZ || "Africa/Nairobi"; // сервер считает дни в том же поясе, что и страница (emulateTimezone)
const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const puppeteer = require(PUPPETEER);
const fs = require("fs"), path = require("path"), cp = require("child_process"), os = require("os");
const DEV = path.resolve(__dirname, "..");
const SRC = path.resolve(process.argv[2] || DEV);
const T = fs.mkdtempSync(path.join(os.tmpdir(), "moyden-e2e137-"));
const PORT = 8656, BASE = `http://127.0.0.1:${PORT}/`;
fs.copyFileSync(path.join(__dirname, "fixture-data.json"), path.join(T, "data.json"));
fs.copyFileSync(path.join(__dirname, "fixture-settings.json"), path.join(T, "settings.json"));
fs.writeFileSync(path.join(T, "cal.json"), '{"calendars": []}');
const sleep = ms => new Promise(r => setTimeout(r, ms));
const readData = () => JSON.parse(fs.readFileSync(path.join(T, "data.json"), "utf8").replace(/^\uFEFF/, ""));
const emp = id => readData().employees.find(e => e.id === id);
const api = (method, p, body) => fetch(BASE + p.replace(/^\//, ""), { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
let srv;
const results = [];
async function step(name, fn) {
  try { const info = await fn(); results.push({ step: name, ok: true, info: info ?? null }); console.log("PASS", name, info ? JSON.stringify(info).slice(0, 300) : ""); }
  catch (e) { results.push({ step: name, ok: false, error: String(e && e.message || e) }); console.log("FAIL", name, String(e && e.message || e)); }
}
function assert(c, msg) { if (!c) throw new Error(msg); }
async function waitFor(fn, ms = 8000, msg = "не дождался") { const t = Date.now(); while (Date.now() - t < ms) { if (await fn()) return true; await sleep(100); } throw new Error(msg); }

(async () => {
  srv = cp.spawn((process.env.PY || path.join(DEV, ".venv", "Scripts", "python.exe")), [path.join(SRC, "server.py"), "--port", String(PORT), "--data", path.join(T, "data.json"),
    "--calendars", path.join(T, "cal.json"), "--cache", path.join(T, "cache"), "--settings", path.join(T, "settings.json"), "--log", path.join(T, "server.log"),
    "--weather-url", "http://127.0.0.1:9/none", "--weather-cache", path.join(T, "wx.json"), "--google-sync", path.join(T, "нет.json")], { stdio: "ignore" });
  for (let i = 0; i < 60; i++) { try { if ((await fetch(BASE + "api/ping")).ok) break; } catch {} await sleep(200); }
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: ["--lang=ru-RU"] });
  await require("./страж_localhost.js").install(browser, __filename);
  const page = await browser.newPage();
  await page.setViewport({ width: 1600, height: 900 });
  await page.emulateTimezone("Africa/Nairobi");
  const errs = [];
  page.on("pageerror", e => errs.push(String(e)));
  const go = async hash => { await page.evaluate(h => { location.hash = h; }, hash); await sleep(500); };
  await page.goto(BASE, { waitUntil: "networkidle2" });
  await waitFor(() => page.evaluate(() => document.querySelectorAll(".proj").length > 0));
  const val = sel => page.$eval(sel, e => e.value);
  const act = () => page.evaluate(() => document.activeElement && (document.activeElement.dataset.fid || document.activeElement.id) || null);

  await step("сотрудник: набор в «Должность» не портится новыми данными (правка другого сотрудника клиентом API) — фокус, текст и курсор на месте, Enter сохраняет", async () => {
    await go("#/people/4");
    await page.click('[data-fid="e-role"]', { clickCount: 3 });
    await page.keyboard.type("арт-директор, дизайнер");
    await (await api("PATCH", "/api/employees/3", { role: "руководитель (клиент API)" })).json();
    await sleep(6000);  // опрос данных — раз в 4 с
    await page.keyboard.type(" и партнёр");  // набор продолжается с того же места (курсор не прыгнул в начало)
    const a = await act(), v = await val('[data-fid="e-role"]');
    assert(a === "e-role" && v === "арт-директор, дизайнер и партнёр", `перерисовка испортила правку: фокус ${a}, в поле «${v}»`);
    await page.keyboard.press("Enter");
    await waitFor(() => emp(4).role === "арт-директор, дизайнер и партнёр", 4000, "Enter не сохранил: " + emp(4).role);
    return { role: emp(4).role, фокус_после_Enter: await act() };
  });

  await step("сотрудник: поле «Отчество» есть, Enter сохраняет в data.json (middleName)", async () => {
    await go("#/people/4");
    assert(await page.$('[data-fid="e-middleName"]'), "поля «Отчество» нет на экране сотрудника");
    await page.click('[data-fid="e-middleName"]');
    await page.keyboard.type("Игоревич");
    await page.keyboard.press("Enter");
    await waitFor(() => emp(4).middleName === "Игоревич", 4000, "отчество не сохранилось: " + JSON.stringify(emp(4)));
    await go("#/people"); await go("#/people/4");
    assert(await val('[data-fid="e-middleName"]') === "Игоревич", "после повторного открытия отчества нет");
    return { middleName: emp(4).middleName };
  });

  await step("сотрудник: Esc в поле возвращает прежнее значение, ничего не сохраняет и не уводит с экрана", async () => {
    await go("#/people/4");
    const before = emp(4).firstName;
    await page.click('[data-fid="e-firstName"]', { clickCount: 3 });
    await page.keyboard.type("Ошибка");
    await page.keyboard.press("Escape");
    await sleep(1500);
    const h = await page.evaluate(() => location.hash), v = await val('[data-fid="e-firstName"]');
    assert(emp(4).firstName === before, "Esc сохранил набранное: " + emp(4).firstName);
    assert(h === "#/people/4" && v === before, `экран ${h}, в поле «${v}»`);
    return { экран: h };
  });

  await step("«Добавить задачу»: при открытии проекта исполнитель пустой (не ответственный проекта)", async () => {
    await go("#/p/portal");
    const w = await val("#atWho");
    assert(w === "", `в поле исполнителя «${w}»`);
  });

  await step("«Добавить задачу»: после добавления с исполнителем форма пустая; вторая задача без выбора — без исполнителя; повторное открытие — пусто", async () => {
    await go("#/p/portal");
    await page.click("#atTitle"); await page.keyboard.type("Задача с исполнителем");
    await page.click("#atWho"); await page.keyboard.type("Влад");
    await page.$eval("#atDue", e => { e.value = "2026-11-02"; });
    await page.focus("#atTitle"); await page.keyboard.press("Enter");
    await waitFor(() => readData().tasks.some(t => t.title === "Задача с исполнителем"));
    const f = { title: await val("#atTitle"), who: await val("#atWho"), due: await val("#atDue") };
    assert(f.title === "" && f.who === "" && f.due === "", "форма после добавления: " + JSON.stringify(f));
    await page.keyboard.type("Задача без выбора"); await page.keyboard.press("Enter");
    await waitFor(() => readData().tasks.some(t => t.title === "Задача без выбора"));
    const t2 = readData().tasks.find(t => t.title === "Задача без выбора");
    assert(!t2.whoId && !t2.who, "вторая задача получила исполнителя: " + JSON.stringify(t2));
    await page.click("#atTitle"); await page.keyboard.type("Недописанная задача");
    await page.click("#atWho"); await page.keyboard.type("Нина");
    await go(""); await go("#/p/portal");
    const w = await val("#atWho"), tt = await val("#atTitle");
    assert(w === "", `после повторного открытия в поле исполнителя «${w}»`);
    assert(tt === "Недописанная задача", `повторное открытие того же проекта стёрло набранный заголовок: «${tt}»`);
    await page.$eval("#atTitle", e => { e.value = ""; });
    return { первая: readData().tasks.find(t => t.title === "Задача с исполнителем").who, вторая: t2.who };
  });

  // ---------- обои ----------
  const wp = await browser.newPage();
  await wp.setViewport({ width: 2440, height: 1392 });
  await wp.emulateTimezone("Africa/Nairobi");
  wp.on("pageerror", e => errs.push("обои: " + String(e)));
  const posts = [];
  let fakeFocus = null;  // ответ «программы» на запрос клавиатуры (в безголовом прогоне программы нет — сервер отвечает 409)
  await wp.setRequestInterception(true);
  wp.on("request", r => {
    if (r.method() === "POST" && /\/api\/app\//.test(r.url())) posts.push({ url: r.url().replace(BASE, "/"), body: r.postData(), t: Date.now() });
    if (fakeFocus && /\/api\/app\/wallpaper-focus$/.test(r.url())) return r.respond({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, focus: fakeFocus }) });
    r.continue();
  });
  await wp.goto(BASE + "?wallpaper=1&left=0&bottom=0", { waitUntil: "networkidle2" });
  await waitFor(() => wp.evaluate(() => document.querySelectorAll(".proj").length > 0));
  const wgo = async hash => { await wp.evaluate(h => { location.hash = h; }, hash); await sleep(500); };

  await step("обои: нажатие мыши в поле — запрос программе отдать клавиатуру обоям (поле и экран в запросе); Enter — запрос вернуть клавиатуру", async () => {
    await wgo("#/people/4");
    posts.length = 0;
    await wp.click('[data-fid="e-role"]');
    await sleep(400);
    const f = posts.find(p => p.url === "/api/app/wallpaper-focus");
    assert(f, "запроса фокуса нет: " + JSON.stringify(posts));
    const b = JSON.parse(f.body);
    assert(b.fid === "e-role" && b.hash === "#/people/4", "тело запроса: " + f.body);
    await wp.keyboard.type(" Б"); await wp.keyboard.press("Enter");
    await sleep(800);
    const r = posts.find(p => p.url === "/api/app/wallpaper-release");
    assert(r && r.t - posts[0].t > 300, "запроса возврата нет после Enter: " + JSON.stringify(posts.map(p => p.url)));
    return { запросы: posts.map(p => p.url) };
  });

  await step("обои: Enter в «Добавить задачу» (поле остаётся в фокусе для следующей задачи) — клавиатуру не возвращает", async () => {
    await wgo("#/p/zeta");
    posts.length = 0;
    await wp.click("#atTitle"); await wp.keyboard.type("Задача на обоях"); await wp.keyboard.press("Enter");
    await waitFor(() => readData().tasks.some(t => t.title === "Задача на обоях"));
    await sleep(700);
    assert(posts.some(p => p.url === "/api/app/wallpaper-focus"), "нет запроса фокуса");
    assert(!posts.some(p => p.url === "/api/app/wallpaper-release"), "вернул клавиатуру, хотя поле ждёт следующую задачу");
  });

  await step("обои: программа ответила «клавиатуру отдать не удалось», hasFocus()=true, клавиш нет — новые данные показываются ≤ 6 с, набранное на месте", async () => {
    await wgo("#/p/portal");
    const id = await wp.$eval("#openList .trow", li => li.dataset.id);
    await wp.evaluate(() => { Object.defineProperty(document, "hasFocus", { value: () => true, configurable: true }); });
    fakeFocus = "window";
    await wp.click(`#openList .trow[data-id="${id}"] .ttl`);
    await sleep(300); fakeFocus = null;
    await wp.evaluate(i => { const e = document.querySelector(`#openList .trow[data-id="${i}"] .ttl`); e.value = e.value + " (черновик)"; e.dispatchEvent(new Event("input", { bubbles: true })); }, id);
    await sleep(3200);  // клавиш больше 3 с не было
    await (await api("PATCH", "/api/projects/portal", { note: "Заметка от клиента API 137" })).json();
    const t0 = Date.now();
    await waitFor(() => wp.$eval("#pvNote", e => e.value === "Заметка от клиента API 137"), 6500, "обои не показали новые данные за 6,5 с");
    const st = await wp.evaluate(i => ({ fid: document.activeElement.closest(".trow") && document.activeElement.closest(".trow").dataset.id, v: document.activeElement.value }), id);
    assert(st.fid === id && /\(черновик\)$/.test(st.v), "набранное потеряно: " + JSON.stringify(st));
    return { показано_через_мс: Date.now() - t0 };
  });

  await browser.close();
  try { cp.execSync(`taskkill /PID ${srv.pid} /T /F`, { stdio: "ignore" }); } catch {}
  const ok = results.filter(r => r.ok).length;
  if (errs.length) console.log("ошибки страницы:", errs.slice(0, 5));
  fs.writeFileSync(path.join(__dirname, "e2e_137-результат.json"), JSON.stringify({ исходники: SRC, когда: new Date().toISOString(), results, errs }, null, 1));
  await sleep(400);
  try { cp.execFileSync((process.env.PY || path.join(DEV, ".venv", "Scripts", "python.exe")), [path.join(__dirname, "в_корзину.py"), T], { stdio: "ignore" }); } catch {} // тестовые данные — в Корзину
  console.log(`ИТОГ: ${ok}/${results.length}`);
  process.exit(ok === results.length && !errs.length ? 0 : 1);
})().catch(e => { console.error("ОШИБКА", e); try { cp.execSync(`taskkill /PID ${srv.pid} /T /F`, { stdio: "ignore" }); } catch {} process.exit(2); });
