// Самопроверка стража (FAIL-сценарий): страница идёт на «чужой» порт → HTTP-запрос заблокирован в браузере и учтён (Chrome может
// заранее открыть пустое TCP-соединение — данные по нему не передаются; считаем именно HTTP-запросы).
// Чужой порт здесь — свой временный 8697 со счётчиком соединений, а не порт другой сборки. Запуск: node страж_самопроверка.js
process.env.MOYDEN_GUARD_EXTRA = "8697";
const puppeteer = require(process.env.PUPPETEER_PATH || "puppeteer-core");
const CHROME = process.env.CHROME_PATH || "C:/Program Files/Google/Chrome/Application/chrome.exe";
const G = require("./страж_localhost.js");
const http = require("http");
let conns = 0, reqs = 0;
const srv = http.createServer((q, r) => { reqs++; r.end("ok"); }).on("connection", () => conns++).listen(8697);
(async () => {
  const browser = await puppeteer.launch({ executablePath: CHROME, headless: true, args: [] });
  await G.install(browser, __filename);
  const p = await browser.newPage();
  const err = await p.goto("http://127.0.0.1:8697/", { timeout: 8000 }).then(() => "", e => String(e.message || e));
  await browser.close(); srv.close();
  const ok = reqs === 0 && G.hits.length === 1 && /ERR_BLOCKED/.test(err);
  console.log(ok ? `PASS самопроверка стража: запрос заблокирован (HTTP-запросов 0, TCP-соединений ${conns}), учтён 1` : `FAIL самопроверка стража: HTTP-запросов ${reqs}, соединений ${conns}, учтено ${G.hits.length}, ошибка ${err}`);
  G.hits.length = 0;  // учтённое обращение — часть самопроверки, итог стража «PASS»
  process.exitCode = ok ? 0 : 1;
})();
