// кнопки на обоях при настоящем (медленном) нажатии, когда перерисовка отложена или приходит во время нажатия.
// Причина дефекта фокус в поле → ответ сервера откладывает перерисовку → нажатие на ☆ уводит фокус из поля → список
// пересобирается между mousedown и mouseup → старой кнопки нет → click не приходит, PATCH не уходит.
// Отдельно: node "tests\обои-клик.js" http://127.0.0.1:ПОРТ/ ПУТЬ\data.json  (итог JSON в stdout, код 1 при FAIL)
// Внутри e2e.js — require("./обои-клик.js").run({ browser, BASE, readData }).
const PRESS_MS = 90; // как рукой: нажатие мыши ~80–120 мс (CDP-клик puppeteer — 1 мс, дефект не ловит)
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function run({ browser, BASE, readData }) {
  const api = (m, p, b) => fetch(BASE + p.replace(/^\//, ""), { method: m, headers: { "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined })
    .then(r => r.json());  // без Origin — как клиент API
  const task = id => readData().tasks.find(x => x.id === id);
  const waitFor = async (fn, ms, msg) => { const t = Date.now(); while (Date.now() - t < ms) { if (await fn()) return true; await sleep(100); } throw new Error(msg); };
  const res = [];
  const check = async (name, fn) => {
    try { const info = await fn(); res.push({ name, ok: true, info: info ?? null }); }
    catch (e) { res.push({ name, ok: false, error: String(e && e.message || e) }); }
  };
  const wp = await browser.newPage();
  try {
    await wp.emulateTimezone("Africa/Nairobi");
    await wp.setViewport({ width: 2440, height: 1392 }); // окно обоев пользователя: 2560 − полоса 120 × рабочая область 1392
    const pr = (await api("POST", "/api/projects", { name: "Звезда обоев" })).item;
    const t1 = (await api("POST", "/api/tasks", { project: pr.id, title: "Звезда обоев — задача", state: "work" })).item;
    const t2 = (await api("POST", "/api/tasks", { project: pr.id, title: "Звезда обоев — вторая", state: "work" })).item;
    const t3 = (await api("POST", "/api/tasks", { project: pr.id, title: "Звезда обоев — на удаление", state: "work" })).item;
    await wp.goto(BASE + "?wallpaper=1#/p/" + pr.id, { waitUntil: "networkidle0" });
    await wp.bringToFront();
    const sel = (id, act) => `#pv .trow[data-id="${id}"] [data-act=${act}]`;
    await wp.waitForSelector(sel(t1.id, "star")); await sleep(600);
    const slowClick = async (s, during) => {
      const b = await wp.$eval(s, e => { const r = e.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
      await wp.mouse.move(b.x, b.y); await sleep(150);
      await wp.mouse.down(); if (during) await during(); else await sleep(PRESS_MS); await wp.mouse.up(); await sleep(300);
    };
    // предусловие пользователя: фокус в поле задачи, данные изменились (опрос раз в 4 с) — перерисовка отложена до ухода из поля
    let nArm = 0;
    const arm = async () => {
      await wp.focus(`#pv .trow[data-id="${t2.id}"] textarea.ttl`);
      await api("PATCH", `/api/projects/${pr.id}`, { note: "опрос " + (++nArm) });
      await wp.waitForResponse(r => r.url().includes("/api/data") && r.status() === 200, { timeout: 9000 }); await sleep(200);
      const a = await wp.evaluate(() => document.activeElement && document.activeElement.className);
      if (!/ttl/.test(a)) throw new Error("предусловие: фокус не в поле, а в " + a);
    };
    // перерисовка приходит ВО ВРЕМЯ нажатия (опрос попал между нажатием и отпусканием) — для кнопок главного экрана
    const pollDuringPress = async () => {
      await api("PATCH", `/api/projects/${pr.id}`, { note: "опрос " + (++nArm) });
      await wp.waitForResponse(r => r.url().includes("/api/data") && r.status() === 200, { timeout: 9000 }); await sleep(250);
    };
    const summary = () => api("GET", "/api/summary");

    await check("☆ поставить: star:true в data.json, в /api/summary priority и в блоке «Приоритетные»", async () => {
      await arm(); await slowClick(sel(t1.id, "star"));
      await waitFor(() => task(t1.id).star === true, 5000, "star в data.json не true: " + JSON.stringify(task(t1.id)));
      const s = await summary(); if (!s.priority.includes(t1.id)) throw new Error("нет в priority: " + JSON.stringify(s.priority));
      await waitFor(() => wp.$(`#prio [data-unstar="${t1.id}"]`), 5000, "нет в «Приоритетных»");
      return { star: true, priority: s.priority };
    });
    await check("☆ снять: star не true, нет в priority и в «Приоритетных»", async () => {
      if (task(t1.id).star !== true) await api("PATCH", `/api/tasks/${t1.id}`, { star: true }); // снимаем настоящую звезду, а не пустую
      await waitFor(() => wp.$eval(sel(t1.id, "star"), e => e.getAttribute("aria-pressed") === "true"), 7000, "☆ не нажата до проверки");
      await arm(); await slowClick(sel(t1.id, "star"));
      await waitFor(() => task(t1.id).star !== true, 5000, "star осталась true");
      const s = await summary(); if (s.priority.includes(t1.id)) throw new Error("осталась в priority");
      await waitFor(async () => !(await wp.$(`#prio [data-unstar="${t1.id}"]`)), 5000, "осталась в «Приоритетных»");
      return { star: task(t1.id).star ?? null };
    });
    await check("⏸ пауза задачи ставится и снимается", async () => {
      await arm(); await slowClick(sel(t1.id, "pause"));
      await waitFor(() => !!task(t1.id).pause, 5000, "пауза не поставлена");
      await arm(); await slowClick(sel(t1.id, "pause"));
      await waitFor(() => !task(t1.id).pause, 5000, "пауза не снята");
      return { ok: true };
    });
    await check("💬 комментарии открываются", async () => {
      await arm(); await slowClick(sel(t1.id, "cm"));
      await waitFor(() => wp.$(`#pv .trow[data-id="${t1.id}"] textarea.cin`), 4000, "лента комментариев не открылась");
      await slowClick(sel(t1.id, "cm"));
      return { ok: true };
    });
    await check("○ выполнено: state done в data.json", async () => {
      await arm(); await slowClick(sel(t1.id, "toggle"));
      await waitFor(() => task(t1.id).state === "done", 5000, "не выполнена: " + task(t1.id).state);
      await sleep(1200); // обработчик «выполнено» сам переводит фокус после анимации — дождаться, чтобы не сбить предусловие
      await api("PATCH", `/api/tasks/${t1.id}`, { state: "work" }); await sleep(4500);
      return { ok: true };
    });
    await check("🗑 корзина: задача удалена, «Вернуть» возвращает", async () => {
      await wp.waitForSelector(sel(t3.id, "del")); await arm(); await slowClick(sel(t3.id, "del"));
      await waitFor(() => !task(t3.id), 5000, "задача не удалена");
      await wp.waitForSelector("#toast.show button", { timeout: 4000 }); await slowClick("#toast.show button");
      await waitFor(() => readData().tasks.some(x => x.title === "Звезда обоев — на удаление"), 5000, "«Вернуть» не вернул");
      return { ok: true };
    });
    await check("«Назад» с экрана проекта → главный экран", async () => {
      await arm(); await slowClick("#pv [data-back]");
      await waitFor(() => wp.$eval("#home", e => !e.hidden), 4000, "не вернулся на главный");
      return { hash: await wp.evaluate(() => location.hash) };
    });
    await check("«+ Новый проект» (опрос во время нажатия) → форма нового проекта", async () => {
      await slowClick("#addProjBtn", pollDuringPress);
      await waitFor(() => wp.$eval("#newProj", e => !e.hidden), 4000, "форма не открылась");
      await wp.keyboard.press("Escape"); await sleep(300);
      return { ok: true };
    });
    await check("календарь: клик по событию (опрос во время нажатия) → карточка события", async () => {
      const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Africa/Nairobi" }).format(new Date());
      // сроков задач на сетке больше нет (они плашкой в полосе «весь день») — блок на сетке дня даёт своё напоминание в 12:00
      const rm = await api("POST", "/api/reminders", { date: today, time: "12:00", text: "Обои: клик по блоку сетки" }), rid = (rm.item || rm).id;
      await wp.click('.seg button[data-v="day"]'); await sleep(300); // 1.3.4 вид «День» (прошлые шаги e2e оставляют «Неделю»)
      const blk = `#calBody .ev.rem[data-rem="${rid}"]`; // именно этот блок, а не события Google фикстуры
      try {
      await waitFor(() => wp.$(blk), 9000, "блок напоминания не появился на сетке дня");
      await wp.evaluate(() => document.activeElement && document.activeElement.blur());
      const s = await wp.evaluate(b => { const e = document.querySelector(b); if (!e) return null; e.scrollIntoView({ block: "center" }); const r = e.getBoundingClientRect();
        if (!(r.width > 20 && r.height > 12 && r.top > 0 && r.bottom < innerHeight)) return null; e.setAttribute("data-e2e-ev", "1"); return e.textContent.trim().slice(0, 40); }, blk);
      if (!s) throw new Error("на сетке дня нет события");
      await slowClick('#calBody [data-e2e-ev="1"]', async () => { await pollDuringPress(); });
      await waitFor(() => wp.$("#calPop:popover-open"), 4000, "карточка не открылась: " + s);
      await wp.keyboard.press("Escape"); await sleep(300);
      return { событие: s };
      } finally { await api("DELETE", `/api/reminders/${rid}`).catch(() => {}); } // напоминание теста убирается и при FAIL
    });
    await check("☆ на главном («Приоритетные», снять звезду) — опрос во время нажатия", async () => {
      for (const t of readData().tasks.filter(x => x.star === true && x.id !== t2.id)) await api("PATCH", `/api/tasks/${t.id}`, { star: false }); // на обоях лишние строки блока скрыты («и ещё N»)
      await api("PATCH", `/api/tasks/${t2.id}`, { star: true });
      await waitFor(() => wp.$(`#prio [data-unstar="${t2.id}"]`), 7000, "t2 не появилась в «Приоритетных»");
      await slowClick(`#prio [data-unstar="${t2.id}"]`, pollDuringPress);
      await waitFor(() => task(t2.id).star !== true, 5000, "звезда в «Приоритетных» не снялась");
      return { ok: true };
    });
  } finally {
    // свои тестовые записи — убрать
    try { const d = readData(); for (const t of d.tasks.filter(x => String(x.title).startsWith("Звезда обоев"))) await api("DELETE", `/api/tasks/${t.id}`); } catch {}
    // проект API не удаляет (его нет в API) — остаётся в копии данных теста; у пользователя тест удаляет его скриптом под замком
    await wp.close().catch(() => {});
  }
  return res;
}
module.exports = { run };

if (require.main === module) {
  const PUPPETEER = process.env.PUPPETEER_PATH || "puppeteer-core";
  const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
  const fs = require("fs");
  const [BASE, DATA] = process.argv.slice(2);
  const readData = () => JSON.parse(fs.readFileSync(DATA, "utf8").replace(/^\uFEFF/, ""));
  (async () => {
    const browser = await require(PUPPETEER).launch({ executablePath: CHROME, headless: true, args: ["--lang=ru-RU"] });
    let res;
    try { res = await run({ browser, BASE, readData }); } finally { await browser.close(); }
    const fail = res.filter(r => !r.ok).length;
    console.log(JSON.stringify({ всего: res.length, прошло: res.length - fail, упало: fail, проверки: res }, null, 1));
    process.exit(fail ? 1 : 0);
  })().catch(e => { console.error(e); process.exit(2); });
}
