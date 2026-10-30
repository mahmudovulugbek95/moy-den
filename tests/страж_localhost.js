// Страж сквозных проверок: страницы проверок не должны обращаться к программам «Мой день», которые работают на этом компьютере.
// BLOCKED_PORTS — порт другой сборки «Мой день»: обращения к нему блокируются на уровне сети браузера (соединение не открывается) и учитываются.
// В конце прогона печатается строка PASS/FAIL «страж»; при обращениях код выхода процесса — 1.
// Подключение (сразу после запуска браузера): await require("./страж_localhost.js").install(browser, __filename);
const BLOCKED_PORTS = [8642, ...String(process.env.MOYDEN_GUARD_EXTRA || "").split(",").filter(Boolean).map(Number)]; // + порты самопроверки
const hits = [];
let name = "e2e";

function protect(page) {
  if (!page) return Promise.resolve(page);
  if (!page.__moydenGuard) {  // одно обещание на страницу: новая страница не уходит в работу, пока блокировка не включена
    page.__moydenGuard = (async () => {
      // Перехват на своей сессии DevTools (домен Fetch): запрос к запрещённому порту останавливается до отправки и отклоняется —
      // соединение с программой на этом порту не открывается (в том числе для переходов страницы, которые Network.setBlockedURLs не ловит).
      const cdp = await page.target().createCDPSession();
      cdp.on("Fetch.requestPaused", e => { hits.push(e.request.url); cdp.send("Fetch.failRequest", { requestId: e.requestId, errorReason: "BlockedByClient" }).catch(() => {}); });
      await cdp.send("Fetch.enable", { patterns: BLOCKED_PORTS.flatMap(p => [{ urlPattern: `*://127.0.0.1:${p}/*` }, { urlPattern: `*://localhost:${p}/*` }]) });
    })();
  }
  return page.__moydenGuard.then(() => page);
}

async function install(browser, file) {
  name = require("path").basename(file || "e2e");
  const newPage = browser.newPage.bind(browser);
  browser.newPage = async (...a) => protect(await newPage(...a));
  browser.on("targetcreated", t => { t.page().then(protect).catch(() => {}); });
  for (const p of await browser.pages()) await protect(p);
  return browser;
}

process.on("exit", () => {
  if (hits.length) {
    console.log(`FAIL страж (${name}): обращения к другой программе на этом компьютере (заблокированы): ${[...new Set(hits)].join(", ")}`);
    process.exitCode = 1;
  } else {
    console.log(`PASS страж (${name}): ни одного обращения к 127.0.0.1:${BLOCKED_PORTS.join(", ")}`);
  }
});

module.exports = { install, protect, hits };
