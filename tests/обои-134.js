// поведение отложенной перерисовки — проверки ПОВЕДЕНИЯ страницы (безголовый Chrome, настоящие события мыши CDP).
//  1) нажатие без отпускания + новые данные → экран обновлён не позже 6 с (таймер на остаток 5 с);
//  2) выбор в выпадающем списке во время «нажато» (состояние задачи на обоях, статус и ответственный в окне) → экран сразу;
//  3) ○ одной задачи, и пока идёт анимация — медленное нажатие ☆ другой: прямая перерисовка не съедает клик;
//  4) обои без фокуса: фокус в поле, окно ушло из фокуса → новые данные показаны, набранный текст и курсор на месте;
//     окно в фокусе → набор не сбивается (перерисовка ждёт);
//  5) CLS: пустая шапка до первого tick() той же высоты, что заполненная.
// Отдельно: node "tests\обои-134.js" http://127.0.0.1:ПОРТ/ ПУТЬ\data.json  (итог JSON в stdout, код 1 при FAIL)
// Внутри e2e.js — require("./обои-134.js").run({ browser, BASE, readData }).
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function run({ browser, BASE, readData }) {
  const api = (m, p, b) => fetch(BASE + p.replace(/^\//, ""), { method: m, headers: { "Content-Type": "application/json" }, body: b ? JSON.stringify(b) : undefined })
    .then(r => r.json());
  const task = id => readData().tasks.find(x => x.id === id);
  const waitFor = async (fn, ms, msg) => { const t = Date.now(); while (Date.now() - t < ms) { if (await fn()) return Date.now() - t; await sleep(50); } throw new Error(msg); };
  const res = [];
  const check = async (name, fn) => {
    try { const info = await fn(); res.push({ name, ok: true, info: info ?? null }); }
    catch (e) { res.push({ name, ok: false, error: String(e && e.message || e) }); }
  };
  const center = (pg, s) => pg.$eval(s, e => { e.scrollIntoView({ block: "nearest" }); const r = e.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2 }; });
  const mark = pg => pg.evaluate(() => { const o = document.createElement("option"); o.id = "e2eMark"; document.getElementById("dlEmp").appendChild(o); }); // renderAll пересобирает #dlEmp
  const rendered = pg => pg.evaluate(() => !document.getElementById("e2eMark"));
  const nextData = pg => pg.waitForResponse(r => r.url().includes("/api/data") && r.status() === 200, { timeout: 9000 });
  const row = id => `#pv .trow[data-id="${id}"]`;
  const sel = (id, act) => `${row(id)} [data-act=${act}]`;
  const mk = async (pid, title) => (await api("POST", "/api/tasks", { project: pid, title, state: "work" })).item;

  const pr = (await api("POST", "/api/projects", { name: "Нажатие 1.3.4" })).item;
  const tB = await mk(pr.id, "Нажатие — верхняя"); // создаётся первой: строка выше и не сдвигается, когда нижняя уходит в выполненные
  const tA = await mk(pr.id, "Нажатие — нижняя");
  const wp = await browser.newPage();
  const win = await browser.newPage();
  try {
    await wp.emulateTimezone("Africa/Nairobi"); await win.emulateTimezone("Africa/Nairobi");
    await wp.setViewport({ width: 2440, height: 1392 }); await win.setViewport({ width: 1600, height: 900 });
    await win.goto(BASE + "#/p/" + pr.id, { waitUntil: "networkidle0" });
    await wp.goto(BASE + "?wallpaper=1#/p/" + pr.id, { waitUntil: "networkidle0" });
    await wp.bringToFront();
    await wp.waitForSelector(sel(tA.id, "star")); await sleep(600);

    await check("нажатие без отпускания (обои): новые данные на экране не позже 6 с", async () => {
      const p = await center(wp, sel(tA.id, "star"));
      await wp.mouse.move(p.x, p.y); await sleep(100); await mark(wp);
      await wp.mouse.down(); const t0 = Date.now();
      try {
        const tC = await mk(pr.id, "Нажатие — новая во время нажатия");
        await nextData(wp); const resp = Date.now() - t0;
        const early = await rendered(wp);
        await waitFor(() => wp.$(row(tC.id)), 9000, `за 9 с после ответа опроса (${resp} мс от нажатия) при нажатии без отпускания новая задача не показана`);
        const shown = Date.now() - t0;
        if (shown > 6000) throw new Error(`показано через ${shown} мс > 6000`);
        return { ответ_опроса_мс: resp, показано_через_мс: shown, перерисовка_до_отпускания_сразу: early };
      } finally { await wp.mouse.move(4, 4); await wp.mouse.up(); await sleep(300); } // отпустить в стороне: ☆ не переключать
    });

    const pickOther = (pg, s) => pg.evaluate(s => { const e = document.querySelector(s); const o = [...e.options].find(x => x.value !== e.value);
      e.value = o.value; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true })); return o.value; }, s);
    // выбор пункта в нативном списке: нажатие на список, отпускание до страницы не доходит (его забирает всплывающий список), затем change
    const selectWhilePressed = async (pg, s, saved) => {
      const p = await center(pg, s);
      await pg.mouse.move(p.x, p.y); await sleep(100); await mark(pg);
      await pg.mouse.down(); await sleep(150);
      const v = await pickOther(pg, s); const t0 = Date.now();
      try {
        await waitFor(saved, 5000, "значение не сохранено в data.json");
        const ms = await waitFor(() => rendered(pg), 7000, "экран после выбора не перерисован за 7 с");
        if (Date.now() - t0 > 1500) throw new Error(`экран обновлён через ${Date.now() - t0} мс после выбора — ждал отпускания мыши`);
        return { значение: v, перерисовка_мс: Date.now() - t0, ждал_мс: ms };
      } finally { await pg.mouse.move(4, 4); await pg.mouse.up(); await pg.evaluate(() => document.activeElement && document.activeElement.blur()); await sleep(300); }
    };
    await check("обои: состояние задачи из списка во время «нажато» — экран сразу (≤ 1,5 с)", async () => {
      const s = `${row(tA.id)} select[data-f=state]`;
      return selectWhilePressed(wp, s, () => task(tA.id).state === "wait");
    });
    await check("окно: статус проекта из списка — экран сразу", async () => {
      await win.bringToFront(); await win.waitForSelector("#pvStatus"); await sleep(300);
      const before = readData().projects.find(x => x.id === pr.id).status || "ok";
      return selectWhilePressed(win, "#pvStatus", () => (readData().projects.find(x => x.id === pr.id).status || "ok") !== before);
    });
    await check("окно: ответственный проекта из списка — экран сразу", async () => {
      const before = readData().projects.find(x => x.id === pr.id).owner || "";
      return selectWhilePressed(win, "#pvOwner", () => (readData().projects.find(x => x.id === pr.id).owner || "") !== before);
    });

    const onProject = async () => { await wp.bringToFront(); await wp.evaluate(h => { if (location.hash !== h) location.hash = h; }, "#/p/" + pr.id);
      await wp.waitForSelector(sel(tB.id, "star"), { visible: true }); await sleep(400); };
    await check("обои: ○ нижней задачи, во время анимации — медленное нажатие ☆ верхней (700 мс): звезда поставлена", async () => {
      await onProject();
      if (task(tA.id).state !== "work") { await api("PATCH", `/api/tasks/${tA.id}`, { state: "work" }); await nextData(wp); await sleep(300); }
      const pA = await center(wp, sel(tA.id, "toggle")), pB = await center(wp, sel(tB.id, "star"));
      if (!(pB.y < pA.y)) throw new Error("предусловие: верхняя задача не выше нижней " + JSON.stringify({ pA, pB }));
      await wp.mouse.click(pA.x, pA.y); await sleep(250);
      await wp.mouse.move(pB.x, pB.y); await wp.mouse.down(); await sleep(700); await wp.mouse.up();
      await waitFor(() => task(tB.id).star === true, 4000, "☆ верхней не поставлена: клик съела перерисовка после «выполнено»");
      await waitFor(() => task(tA.id).state === "done", 3000, "нижняя не выполнена");
      await api("PATCH", `/api/tasks/${tB.id}`, { star: false });
      return { star: true, нижняя: "done" };
    });

    await check("обои без фокуса: фокус в поле комментария — новые данные показаны, набранный текст и курсор на месте", async () => {
      await onProject();
      if (!(await wp.$(`${row(tB.id)} textarea.cin`))) { await wp.click(sel(tB.id, "cm")); await wp.waitForSelector(`${row(tB.id)} textarea.cin`); }
      const f = `[data-fid="t${tB.id}-cnew"]`;
      await wp.focus(f); await wp.keyboard.type("черновик"); await wp.evaluate(f => document.querySelector(f).setSelectionRange(3, 3), f);
      // окно в фокусе: перерисовка ждёт, набор не сбивается
      const tD = await mk(pr.id, "Нажатие — пока печатают"); await nextData(wp); await sleep(700);
      const whileTyping = !!(await wp.$(row(tD.id)));
      if (whileTyping) throw new Error("окно в фокусе, курсор в поле: перерисовка не дождалась выхода из поля");
      // окно ушло из фокуса (пользователь работает в другой программе)
      const other = await browser.newPage(); await other.bringToFront(); await sleep(400);
      const hf = await wp.evaluate(() => document.hasFocus());
      if (hf) throw new Error("предусловие: окно обоев всё ещё в фокусе");
      await waitFor(() => wp.$(row(tD.id)), 3000, "после ухода окна из фокуса отложенная перерисовка не показала задачу");
      const tE = await mk(pr.id, "Нажатие — пока окно не в фокусе"); const t0 = Date.now();
      await waitFor(() => wp.$(row(tE.id)), 7000, "окно без фокуса, курсор в поле: новая задача не показана за 7 с");
      const ms = Date.now() - t0;
      const st = await wp.evaluate(f => { const e = document.querySelector(f), a = document.activeElement; return { v: e && e.value, fid: a && a.dataset && a.dataset.fid, s: e && e.selectionStart }; }, f);
      await other.close();
      await wp.bringToFront(); await sleep(300); await wp.keyboard.type("Ж");
      const v2 = await wp.$eval(f, e => e.value);
      await wp.$eval(f, e => { e.value = ""; e.dispatchEvent(new Event("input", { bubbles: true })); e.blur(); }); // черновик не отправлять
      if (st.v !== "черновик" || st.fid !== `t${tB.id}-cnew` || st.s !== 3) throw new Error("поле после перерисовки: " + JSON.stringify(st));
      if (v2 !== "черЖновик") throw new Error("ввод после возврата фокуса попал не на место курсора: " + v2);
      return { показано_мс: ms, поле: st, после_возврата: v2 };
    });

    // 1.3.5 правка названия без Enter, затем медленное нажатие по кнопке другой задачи — щелчок не теряется
    const editTitle = async () => { await onProject(); const f = `[data-fid="t${tB.id}-ttl"]`; await wp.focus(f); await wp.keyboard.press("End"); await wp.keyboard.type(" правка"); };
    const pressSlow = async s => { const p = await center(wp, s); await wp.mouse.move(p.x, p.y); await sleep(100); await wp.mouse.down(); await sleep(90); await wp.mouse.up(); };
    const restoreB = () => api("PATCH", `/api/tasks/${tB.id}`, { title: "Нажатие — верхняя" });
    await check("1.3.5 правка названия без Enter → нажатие 90 мс по ☆ другой задачи → star:true, название сохранено", async () => {
      if (task(tA.id).state !== "work") await api("PATCH", `/api/tasks/${tA.id}`, { state: "work", star: false });
      await sleep(4500); await editTitle(); await pressSlow(sel(tA.id, "star"));
      await waitFor(() => task(tA.id).star === true, 5000, "☆ не поставлена: щелчок потерян после правки названия");
      await waitFor(() => /правка$/.test(task(tB.id).title), 4000, "название не сохранено: " + task(tB.id).title);
      await api("PATCH", `/api/tasks/${tA.id}`, { star: false }); await restoreB(); return { star: true };
    });
    await check("1.3.5 правка названия без Enter → нажатие 90 мс по ○ другой задачи → выполнена", async () => {
      await sleep(4500); await editTitle(); await pressSlow(sel(tA.id, "toggle"));
      await waitFor(() => task(tA.id).state === "done", 5000, "○ не сработал: щелчок потерян после правки названия");
      await api("PATCH", `/api/tasks/${tA.id}`, { state: "work" }); await restoreB(); return { state: "done" };
    });
    await check("1.3.5 правка названия без Enter → нажатие на список состояния другой задачи — список не пересоздан под нажатием", async () => {
      await sleep(4500); await editTitle();
      const s = `${row(tA.id)} select[data-f=state]`;
      await wp.$eval(s, e => { e.dataset.e2eSel = "1"; });
      const p = await center(wp, s); await wp.mouse.move(p.x, p.y); await sleep(100); await wp.mouse.down(); await sleep(400);
      const same = await wp.evaluate(() => !!document.querySelector("[data-e2e-sel]"));
      await wp.mouse.move(4, 4); await wp.mouse.up(); await wp.evaluate(() => document.activeElement && document.activeElement.blur()); await restoreB(); await sleep(300);
      if (!same) throw new Error("список пересоздан между нажатием и отпусканием — выбор пункта пропал бы");
      return { тот_же_список: true };
    });
    await check("1.3.5 обои, hasFocus()=false во время набора — поле не пересоздаётся, пока печатают; после паузы данные видны, правка сохранена", async () => {
      await onProject(); await wp.evaluate(() => { Document.prototype.__hf = Document.prototype.hasFocus; Document.prototype.hasFocus = () => false; });
      try {
        const f = `[data-fid="t${tB.id}-ttl"]`;
        await wp.focus(f); await wp.keyboard.press("End"); let typed = " набор"; await wp.keyboard.type(typed);
        await wp.$eval(f, e => { e.dataset.e2eSame = "1"; });
        const tF = await mk(pr.id, "Нажатие — во время набора");
        // печатает, пока не придёт ответ опроса (клавиша раз в 0,7 с < 3 с), — ответ всегда приходит во время набора (раньше 1 раз из 4 — после)
        let came = false; const got = nextData(wp).then(() => { came = true; }, e => { came = true; throw e; });
        const t0 = Date.now(); while (!came && Date.now() - t0 < 15000) { await wp.keyboard.type("."); typed += "."; await sleep(700); }
        await got; await wp.keyboard.type(" Х"); typed += " Х"; await sleep(300);
        const same = await wp.evaluate(() => !!document.querySelector("[data-e2e-same]"));
        if (!same) throw new Error("поле пересоздано во время набора (hasFocus false) — набор сбит");
        await waitFor(() => wp.$(row(tF.id)), 7000, "после паузы в наборе новая задача не показана");
        const v = await wp.$eval(f, e => e.value);
        if (!v.endsWith(typed)) throw new Error("набранный текст потерян: " + v + " (ждали окончание «" + typed + "»)");
        await wp.$eval(f, e => e.blur());
        await waitFor(() => task(tB.id).title.endsWith(typed), 4000, "правка в пересобранном поле не сохранена при уходе: " + task(tB.id).title);
        return { сохранено: task(tB.id).title };
      } finally { await wp.evaluate(() => { Document.prototype.hasFocus = Document.prototype.__hf; }); await restoreB();
        await waitFor(() => wp.$eval(`[data-fid="t${tB.id}-ttl"]`, e => e.value === "Нажатие — верхняя").catch(() => false), 9000, "").catch(() => {}); } // следующий шаг — без перерисовки в полёте
    });
    await check("1.3.5 недописанный комментарий после ухода из поля и перерисовки не пропадает", async () => {
      await onProject(); await sleep(300);
      if (!(await wp.$(`${row(tB.id)} textarea.cin`))) { await wp.click(sel(tB.id, "cm")); await wp.waitForSelector(`${row(tB.id)} textarea.cin`); }
      const f = `[data-fid="t${tB.id}-cnew"]`;
      await wp.focus(f); await wp.keyboard.type("недописанный"); await wp.$eval(f, e => e.blur());
      const tG = await mk(pr.id, "Нажатие — после комментария");
      await waitFor(() => wp.$(row(tG.id)), 7000, "перерисовка не пришла");
      const v = await wp.$eval(f, e => e.value);
      await wp.$eval(f, e => { e.value = ""; e.dispatchEvent(new Event("input", { bubbles: true })); e.focus(); e.blur(); });
      if (v !== "недописанный") throw new Error("текст комментария пропал: «" + v + "»");
      return { v };
    });

    await check("CLS: шапка до первого tick() той же высоты, что заполненная", async () => {
      await win.bringToFront(); await win.goto(BASE, { waitUntil: "networkidle0" }); await sleep(500);
      const m = await win.evaluate(() => {
        const H = () => { const h = document.querySelector("header").getBoundingClientRect(), s = document.getElementById("sum").getBoundingClientRect(), m = document.getElementById("home").getBoundingClientRect(); return [+h.height.toFixed(2), +s.top.toFixed(2), +m.top.toFixed(2)]; };
        const wx = document.getElementById("wx"), wxh = wx.hidden; wx.hidden = true; // как при первой отрисовке: погода ещё не пришла
        const ids = ["wday", "date", "clock"], keep = ids.map(i => document.getElementById(i).textContent), full = H();
        ids.forEach(i => { document.getElementById(i).textContent = ""; }); const empty = H();
        ids.forEach((i, k) => { document.getElementById(i).textContent = keep[k]; }); wx.hidden = wxh;
        return { заполненная: full, пустая: empty };
      });
      if (m.заполненная.some((v, i) => Math.abs(v - m.пустая[i]) > 0.5)) throw new Error("шапка меняет высоту при заполнении (сдвиг экрана): " + JSON.stringify(m));
      return m;
    });
  } finally {
    try { const d = readData(); for (const t of d.tasks.filter(x => String(x.title).startsWith("Нажатие"))) await api("DELETE", `/api/tasks/${t.id}`); } catch {}
    await wp.close().catch(() => {}); await win.close().catch(() => {});
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
