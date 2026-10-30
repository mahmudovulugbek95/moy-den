// фикстуры e2e написаны под пятницу 23.10.2026. Чтобы шаги не зависели от даты прогона, все даты фикстур сдвигаются
// на N дней: «23.10» становится сегодняшним днём (Найроби), «21.10» — позавчерашним и т. д. Порядок дней и «сегодня» сохраняются;
// день недели меняется, поэтому шаги «Неделя»/«Месяц» ищут день по дате (data-d, data-cell) и листают календарь к нему.
const BASE_DAY = "2026-10-23";
const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Africa/Nairobi" }).format(new Date());
const ms = s => Date.parse(s + "T00:00:00Z");
const days = (a, b) => Math.round((ms(a) - ms(b)) / 864e5);
const addDays = (s, n) => { const d = new Date(ms(s)); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
const dow = s => (new Date(ms(s)).getUTCDay() + 6) % 7; // понедельник — 0, как в календаре страницы
const N = days(today, BASE_DAY);
const D = s => addDays(s, N);                         // дата фикстуры → дата прогона
const ddmm = s => { const d = D(s); return d.slice(8, 10) + "." + d.slice(5, 7); };
const weeksFromToday = d => Math.round((ms(addDays(d, -dow(d))) - ms(addDays(today, -dow(today)))) / (7 * 864e5));
const monthsFromToday = d => (+d.slice(0, 4) * 12 + +d.slice(5, 7)) - (+today.slice(0, 4) * 12 + +today.slice(5, 7));
const BYDAY = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"];

// JSON фикстуры данных: каждая дата ГГГГ-ММ-ДД (в том числе внутри ГГГГ-ММ-ДДTчч:мм) сдвигается на N дней
function dataJson(txt) {
  return String(txt).replace(/(?<!\d)(20\d\d-\d\d-\d\d)(?!\d)/g, (m, d) => D(d));
}

// iCal: даты ГГГГММДД в DTSTART/DTEND/EXDATE/RECURRENCE-ID событий (часовой пояс VTIMEZONE 1970 не трогаем);
// BYDAY еженедельного повтора — день недели сдвинутого начала (иначе повтор уедет с дня, под который написан тест)
function ics(txt) {
  const sh = s => s.replace(/\b(20\d\d)(\d\d)(\d\d)(?=T|\b)/g, (m, y, mo, d) => D(`${y}-${mo}-${d}`).replaceAll("-", ""));
  return String(txt).split("BEGIN:VEVENT").map((blk, i) => {
    if (i === 0) return blk;
    let start = null;
    const out = blk.split(/\r?\n/).map(ln => {
      if (!/^(DTSTART|DTEND|EXDATE|RECURRENCE-ID)[;:]/.test(ln)) return ln;
      const v = sh(ln);
      const m = ln.startsWith("DTSTART") && v.match(/:(\d{4})(\d\d)(\d\d)/);
      if (m) start = `${m[1]}-${m[2]}-${m[3]}`;
      return v;
    });
    return out.map(ln => (/^RRULE:.*FREQ=WEEKLY/.test(ln) && start ? ln.replace(/BYDAY=[A-Z]{2}\b/, "BYDAY=" + BYDAY[dow(start)]) : ln)).join("\n");
  }).join("BEGIN:VEVENT");
}

module.exports = { BASE_DAY, today, N, D, ddmm, days, addDays, dow, weeksFromToday, monthsFromToday, dataJson, ics };

if (require.main === module) { // самопроверка: node tests\фикстуры_по_дате.js
  const assert = (c, m) => { if (!c) { console.log("FAIL", m); process.exitCode = 1; } else console.log("PASS", m); };
  assert(D(BASE_DAY) === today, "23.10 фикстуры = сегодня");
  assert(days(D("2026-10-21"), today) === -2 && days(D("2026-10-25"), today) === 2, "расстояния до сегодня сохраняются");
  const j = dataJson('{"due":"2026-10-21","at":"2026-10-23T10:00:00+03:00","n":"20261023"}');
  assert(j.includes(`"due":"${D("2026-10-21")}"`) && j.includes(`"at":"${today}T10:00:00+03:00"`) && j.includes('"n":"20261023"'), "JSON: " + j);
  const c = ics("BEGIN:VTIMEZONE\nDTSTART:19700101T000000\nEND:VTIMEZONE\nBEGIN:VEVENT\nDTSTART;TZID=Africa/Nairobi:20261005T100000\nRRULE:FREQ=WEEKLY;BYDAY=MO;COUNT=8\nEXDATE;TZID=Africa/Nairobi:20261019T100000\nEND:VEVENT\nBEGIN:VEVENT\nDTSTART;VALUE=DATE:20261024\nEND:VEVENT");
  const st = D("2026-10-05");
  assert(c.includes("DTSTART:19700101T000000") && c.includes(`DTSTART;TZID=Africa/Nairobi:${st.replaceAll("-", "")}T100000`)
    && c.includes("BYDAY=" + BYDAY[dow(st)]) && c.includes(`EXDATE;TZID=Africa/Nairobi:${D("2026-10-19").replaceAll("-", "")}T100000`)
    && c.includes(`DTSTART;VALUE=DATE:${D("2026-10-24").replaceAll("-", "")}`), "iCal: " + c);
  assert(dow(D("2026-10-19")) === dow(D("2026-10-05")), "исключение повтора попадает на день повтора");
  assert(weeksFromToday(today) === 0 && weeksFromToday(addDays(today, 7)) === 1 && monthsFromToday(today) === 0, "листание календаря");
  console.log("N =", N, "сегодня", today);
}
