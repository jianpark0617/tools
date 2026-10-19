// flight-watch 텔레그램 봇 (Cloudflare Worker, 웹훅 방식)
//
// 텔레그램이 메시지를 이 Worker로 바로 밀어 넣어요(웹훅). Worker가 명령을 해석해서
// GitHub 저장소의 flight-watch/config.json 을 직접 고치고 1~2초 안에 답장해요.
// 시간이 오래 걸리는 가격 조회(/summary, 매일 아침)는 GitHub Actions가 해요.
//
// 필요한 설정 (README.md 의 자동 설치가 다 해 줘요):
//   Secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_WEBHOOK_SECRET, GITHUB_TOKEN(Contents·Actions 쓰기)
//   변수:    GITHUB_REPO (예: jianpark0617/tools), GITHUB_BRANCH (기본 main)
//   KV:      STATE (되묻는 명령 기억용)

const CONFIG_PATH = "flight-watch/config.json";
const HISTORY_DIR = "flight-watch/history";
const WATCH_WORKFLOW = "flight-watch.yml";
const PENDING_TTL_MS = 60 * 60 * 1000;
const WEEKDAYS = { mon: 0, tue: 1, wed: 2, thu: 3, fri: 4, sat: 5, sun: 6 };
const WD_KO2EN = { 월: "mon", 화: "tue", 수: "wed", 목: "thu", 금: "fri", 토: "sat", 일: "sun" };
const WD_EN2KO = Object.fromEntries(Object.entries(WD_KO2EN).map(([k, v]) => [v, k]));

const AIRPORTS = {
  도쿄: ["NRT", "HND"], 오사카: ["KIX"], 후쿠오카: ["FUK"], 삿포로: ["CTS"], 오키나와: ["OKA"],
  나고야: ["NGO"], 교토: ["KIX"], 구마모토: ["KMJ"], 가고시마: ["KOJ"], 다카마쓰: ["TAK"],
  마쓰야마: ["MYJ"], 히로시마: ["HIJ"], 센다이: ["SDJ"], 오이타: ["OIT"], 기타큐슈: ["KKJ"],
  시즈오카: ["FSZ"], 타이베이: ["TPE"], 대만: ["TPE"], 가오슝: ["KHH"], 홍콩: ["HKG"], 마카오: ["MFM"],
  상하이: ["PVG"], 베이징: ["PEK", "PKX"], 칭다오: ["TAO"], 방콕: ["BKK", "DMK"], 치앙마이: ["CNX"],
  푸켓: ["HKT"], 다낭: ["DAD"], 나트랑: ["CXR"], 냐짱: ["CXR"], 호치민: ["SGN"], 하노이: ["HAN"],
  푸꾸옥: ["PQC"], 달랏: ["DLI"], 세부: ["CEB"], 마닐라: ["MNL"], 보라카이: ["MPH", "KLO"],
  클락: ["CRK"], 보홀: ["TAG"], 싱가포르: ["SIN"], 쿠알라룸푸르: ["KUL"], 코타키나발루: ["BKI"],
  발리: ["DPS"], 자카르타: ["CGK"], 괌: ["GUM"], 사이판: ["SPN"], 울란바토르: ["UBN"], 몽골: ["UBN"],
  블라디보스토크: ["VVO"], 델리: ["DEL"], 뭄바이: ["BOM"], 카트만두: ["KTM"], 콜롬보: ["CMB"],
  몰디브: ["MLE"], 두바이: ["DXB"], 이스탄불: ["IST"], 도하: ["DOH"], 파리: ["CDG"], 런던: ["LHR"],
  로마: ["FCO"], 바르셀로나: ["BCN"], 마드리드: ["MAD"], 프랑크푸르트: ["FRA"], 뮌헨: ["MUC"],
  암스테르담: ["AMS"], 프라하: ["PRG"], 비엔나: ["VIE"], 빈: ["VIE"], 취리히: ["ZRH"], 밀라노: ["MXP"],
  리스본: ["LIS"], 헬싱키: ["HEL"], 코펜하겐: ["CPH"], 뉴욕: ["JFK", "EWR"], 로스앤젤레스: ["LAX"],
  LA: ["LAX"], 샌프란시스코: ["SFO"], 시애틀: ["SEA"], 라스베이거스: ["LAS"], 하와이: ["HNL"],
  호놀룰루: ["HNL"], 밴쿠버: ["YVR"], 토론토: ["YYZ"], 시드니: ["SYD"], 멜버른: ["MEL"], 브리즈번: ["BNE"],
  오클랜드: ["AKL"], 케언스: ["CNS"], 인천: ["ICN"], 김포: ["GMP"], 부산: ["PUS"], 김해: ["PUS"],
  제주: ["CJU"], 대구: ["TAE"], 청주: ["CJJ"],
};

const HELP =
  "<b>항공권 감시 봇 명령어</b>\n" +
  "/목록 — 감시 중인 여행지와 기준가\n" +
  "/기준가 도쿄 350000 — 기준가 바꾸기 (35만 도 됨)\n" +
  "/추가 방콕 BKK 2026-12-01 2027-02-28 450000 — 여행지 추가\n" +
  "   공항을 모르면 ? : /추가 후쿠오카 ? 2026-12-01 2027-02-28\n" +
  "   뒤에 박수=5,6 요일=목,금 인원=2 를 붙일 수 있어요\n" +
  "/삭제 방콕 — 여행지 빼기\n" +
  "/기간 도쿄 2027-01-01 2027-03-31 — 출발 가능 기간\n" +
  "/박수 도쿄 2,3 — 박수\n" +
  "/요일 도쿄 목,금,토 — 출발 요일 (전부 = 매일)\n" +
  "/출발지 GMP — 출발 공항\n" +
  "/인원 2 — 인원 (성인). 여행지만 다르게: /인원 도쿄 4\n" +
  "/요약 — 지금 최저가 요약 (조회에 3~5분)\n" +
  "/초기화 — 가격 기록 지우기 (처음부터 다시 비교)\n" +
  "\n영문도 돼요: /list /price /add /del /pax /period /nights /days /origin /summary /reset /help";

const COMMANDS = {
  목록: "list", list: "list", ls: "list",
  기준가: "price", price: "price",
  추가: "add", add: "add",
  삭제: "del", del: "del", remove: "del", rm: "del",
  기간: "period", period: "period",
  박수: "nights", nights: "nights",
  요일: "days", weekdays: "days", days: "days",
  출발지: "origin", origin: "origin",
  인원: "pax", pax: "pax", passengers: "pax", adults: "pax",
  요약: "summary", summary: "summary",
  초기화: "reset", reset: "reset",
  도움: "help", help: "help", start: "help",
};
const NEEDS_ARGS = new Set(["price", "add", "del", "period", "nights", "days", "origin", "pax"]);

class CommandError extends Error {}

// ---------------------------------------------------------------------------
// 진입점
// ---------------------------------------------------------------------------

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/telegram") {
      const secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token") || "";
      if (!env.TELEGRAM_WEBHOOK_SECRET || secret !== env.TELEGRAM_WEBHOOK_SECRET) {
        return new Response("forbidden", { status: 403 });
      }
      let update;
      try {
        update = await request.json();
      } catch {
        return new Response("bad request", { status: 400 });
      }
      // 텔레그램에는 바로 200을 주고, 처리는 뒤에서 이어가요.
      ctx.waitUntil(handleUpdate(update, env).catch((e) => console.error("handleUpdate 실패:", e)));
      return new Response("ok");
    }
    if (url.pathname === "/check") {
      if (!env.CHECK_KEY || url.searchParams.get("key") !== env.CHECK_KEY) {
        return new Response("forbidden", { status: 403 });
      }
      return Response.json(await status(env));
    }
    return new Response("flight-watch relay: ok\n");
  },
};

async function status(env) {
  const missing = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_WEBHOOK_SECRET", "GITHUB_TOKEN", "GITHUB_REPO"]
    .filter((k) => !env[k]);
  const out = { ok: missing.length === 0, missing, kv: !!env.STATE };
  try {
    const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/getWebhookInfo`);
    out.webhook = await r.json();
  } catch (e) {
    out.webhook = String(e);
  }
  try {
    await ghGetFile(env, CONFIG_PATH);
    out.github = "config.json 읽기 OK";
  } catch (e) {
    out.github = String(e.message || e);
  }
  return out;
}

export async function handleUpdate(update, env) {
  const msg = update.message;
  const text = msg && typeof msg.text === "string" ? msg.text : null;
  if (!text) return { ignored: "텍스트 아님" };
  const chatId = String(msg.chat && msg.chat.id);
  if (!env.TELEGRAM_CHAT_ID || chatId !== String(env.TELEGRAM_CHAT_ID)) {
    return { ignored: `다른 채팅 ${chatId}` };
  }

  const pending = await loadPending(env);
  const today = kstToday();

  let result;
  try {
    result = await withConfig(env, async (cfg) => {
      // /목록 은 역대 최저가도 보여 주려고 기록 파일을 읽어요.
      if (/^\/?(목록|list|ls)(@\S+)?$/i.test(text.trim())) await withHistory(env, cfg);
      return handleText(cfg, text, today, pending);
    });
  } catch (e) {
    console.error("처리 실패:", e);
    await sendTelegram(env, `⚠️ 처리 중 문제가 생겼어요: ${esc(String(e.message || e)).slice(0, 300)}`);
    return { error: String(e) };
  }

  await savePending(env, result.pending);
  const extras = [];
  if (result.summary) {
    try {
      await ghDispatch(env, WATCH_WORKFLOW, { mode: "summary" });
    } catch (e) {
      extras.push(`⚠️ 조회 실행을 시작하지 못했어요: ${esc(String(e.message || e)).slice(0, 200)}`);
    }
  }
  if (result.reset) {
    try {
      const n = await ghDeleteHistory(env);
      result.reply = `가격 기록 ${n}개를 지웠어요. 다음 조회 때 '첫 조회 결과'부터 다시 시작해요.`;
    } catch (e) {
      result.reply = `⚠️ 기록을 지우지 못했어요: ${esc(String(e.message || e)).slice(0, 200)}`;
    }
  }
  const reply = [result.reply, ...extras].filter(Boolean).join("\n\n");
  if (reply) await sendTelegram(env, reply);
  return result;
}

/** config.json 을 읽고 fn 을 적용한 뒤, 바뀌었으면 커밋해요. 그 사이 남이 고쳤으면 다시 시도해요. */
async function withConfig(env, fn) {
  let lastErr;
  for (let attempt = 0; attempt < 3; attempt++) {
    const { json: cfg, sha } = await ghGetFile(env, CONFIG_PATH);
    const result = await fn(cfg);
    if (!result.changed) return result;
    try {
      await ghPutFile(env, CONFIG_PATH, JSON.stringify(cfg, null, 2) + "\n", sha,
        `flight-watch: 텔레그램 명령 반영 (${result.commit || "설정 변경"})`);
      return result;
    } catch (e) {
      lastErr = e;
      if (!/409|422/.test(String(e.message))) throw e; // 충돌이 아니면 바로 실패
    }
  }
  throw lastErr || new Error("설정 저장에 계속 실패했어요");
}

// ---------------------------------------------------------------------------
// 명령 해석
// ---------------------------------------------------------------------------

export function handleText(cfg, rawText, today, pending) {
  let text = rawText.trim();
  let isCommand = text.startsWith("/");
  if (pending && !isCommand && text) {
    text = `/${pending} ${text}`;
    isCommand = true;
  }
  const parts = text.replace(/^\/+/, "").split(/\s+/).filter(Boolean);
  const none = { reply: HELP, changed: false, summary: false, reset: false, pending: null };
  if (!parts.length) return none;
  const word = parts[0].split("@")[0].toLowerCase();
  const kind = COMMANDS[word] || COMMANDS[parts[0].split("@")[0]];
  if (!kind) {
    if (!isCommand) return { ...none, reply: "명령어는 /로 시작해요. /도움 을 보내 보세요." };
    return { ...none, reply: `모르는 명령어예요: /${esc(parts[0])}\n\n${HELP}` };
  }
  const args = parts.slice(1);
  if (kind === "summary") return { ...none, reply: "지금 최저가를 조회해서 요약을 보낼게요. 3~5분 걸려요.", summary: true };
  if (kind === "reset") return { ...none, reply: "", reset: true };
  if (kind === "help") return none;
  if (!args.length && NEEDS_ARGS.has(kind)) return { ...none, reply: askForArgs(cfg, kind), pending: kind };
  try {
    const r = HANDLERS[kind](cfg, args, today);
    return { ...none, reply: r.reply, changed: !!r.changed, commit: r.commit };
  } catch (e) {
    if (e instanceof CommandError) return { ...none, reply: `⚠️ ${esc(e.message)}` };
    throw e;
  }
}

function askForArgs(cfg, kind) {
  const names = cfg.destinations.map((d) => d.name).join(", ") || "(아직 없음)";
  const first = cfg.destinations[0]?.name || "도쿄";
  const p = {
    price: `어느 여행지 기준가를 얼마로 바꿀까요?\n예: ${first} 35만\n여행지: ${names}`,
    add: "추가할 여행지를 이렇게 적어 주세요:\n이름 공항 출발시작 출발끝 [기준가]\n예: 방콕 BKK 12-01 2-28 45만\n공항을 모르면 ? : 후쿠오카 ? 12-01 2-28",
    del: `어느 여행지를 뺄까요?\n여행지: ${names}`,
    period: `어느 여행지의 출발 기간을 언제로 바꿀까요?\n예: ${first} 2027-01-01 2027-03-31`,
    nights: `어느 여행지를 몇 박으로 바꿀까요?\n예: ${first} 2,3`,
    days: `어느 여행지의 출발 요일을 바꿀까요?\n예: ${first} 목,금,토  (매일이면 전부)`,
    origin: "출발 공항 코드를 적어 주세요.\n예: GMP (인천 ICN, 김포 GMP, 부산 PUS)",
    pax: `몇 명인가요? 숫자만 적으면 기본 인원이 바뀌어요.\n예: 2\n여행지만 다르게: ${first} 4`,
  }[kind];
  return p + "\n\n다음에 보내는 메시지를 그 답으로 받아요. 취소하려면 아무 명령이나 새로 보내세요.";
}

const HANDLERS = {
  list(cfg) {
    const origin = cfg.origin;
    if (!cfg.destinations.length) return { reply: "감시 중인 여행지가 없어요. /추가 로 넣어 보세요." };
    const lines = [`<b>감시 중</b> · ${origin} 출발 · 기본 성인 ${cfg.passengers || 1}명`, ""];
    for (const d of cfg.destinations) {
      lines.push(describe(cfg, d));
      const best = d.__best; // withHistory 에서 채워 줘요
      if (best) {
        const bp = Number(best.passengers || 1);
        const shown = won(best.price, cfg.currency) + (bp > 1 ? ` (성인 ${bp}명, 1인 ${won(Math.round(best.price / bp), cfg.currency)})` : "");
        lines.push(`  역대 최저 <a href="${esc(best.url)}">${shown}</a> (${best.depart}~${best.ret} ${best.airport})`);
      }
      lines.push("");
    }
    return { reply: lines.join("\n").trimEnd() };
  },
  price(cfg, args) {
    if (args.length < 2) throw new CommandError("이렇게 보내 주세요: /기준가 도쿄 350000");
    const d = findDest(cfg, args[0]);
    if (["없음", "삭제", "none", "off"].includes(args[1])) {
      delete d.alert_below;
      return { reply: `${d.name} 기준가를 없앴어요. 역대 최저가 갱신 때만 알려요.`, changed: true, commit: `${d.name} 기준가 해제` };
    }
    const price = parsePrice(args.slice(1).join(" "));
    d.alert_below = price;
    return { reply: `${d.name} 기준가를 ${won(price, cfg.currency)}으로 바꿨어요.`, changed: true, commit: `${d.name} 기준가 ${price}` };
  },
  add(cfg, args, today) {
    const opts = {};
    const plain = [];
    for (const a of args) {
      const m = a.match(/^(박수|요일|인원|nights|weekdays|days|pax|adults)=(.+)$/);
      if (m) opts[m[1]] = m[2];
      else plain.push(a);
    }
    if (plain.length < 4) {
      throw new CommandError("이렇게 보내 주세요:\n/추가 방콕 BKK 2026-12-01 2027-02-28 450000\n공항을 모르면 ? : /추가 후쿠오카 ? 2026-12-01 2027-02-28");
    }
    const name = plain[0];
    if (cfg.destinations.some((d) => d.name === name)) {
      throw new CommandError(`'${name}'은 이미 있어요. 바꾸려면 /기준가, /기간, /박수, /요일 을 쓰세요.`);
    }
    const airports = parseAirports(plain[1], name);
    const start = parseDate(plain[2], today);
    const end = parseDate(plain[3], today);
    if (end < start) throw new CommandError("끝나는 날짜가 시작보다 빨라요.");
    if (end < today) throw new CommandError("이미 지난 기간이에요.");
    const dest = { name, airports, depart_from: iso(start), depart_to: iso(end), depart_weekdays: ["fri", "sat"], nights: [3, 4] };
    if (plain.length >= 5) dest.alert_below = parsePrice(plain.slice(4).join(" "));
    const nightsRaw = opts["박수"] || opts.nights;
    if (nightsRaw) dest.nights = parseNights(nightsRaw);
    const wdRaw = opts["요일"] || opts.weekdays || opts.days;
    if (wdRaw) {
      const wd = parseWeekdays(wdRaw);
      if (wd === null) delete dest.depart_weekdays;
      else dest.depart_weekdays = wd;
    }
    const paxRaw = opts["인원"] || opts.pax || opts.adults;
    if (paxRaw) dest.passengers = parsePax(paxRaw);
    cfg.destinations.push(dest);
    return { reply: "추가했어요. 다음 조회부터 감시해요.\n\n" + describe(cfg, dest), changed: true, commit: `${name} 추가` };
  },
  del(cfg, args) {
    const d = findDest(cfg, args[0]);
    cfg.destinations = cfg.destinations.filter((x) => x !== d);
    return { reply: `${d.name}을(를) 감시 목록에서 뺐어요.`, changed: true, commit: `${d.name} 삭제` };
  },
  period(cfg, args, today) {
    if (args.length < 3) throw new CommandError("이렇게 보내 주세요: /기간 도쿄 2027-01-01 2027-03-31");
    const d = findDest(cfg, args[0]);
    const start = parseDate(args[1], today);
    const end = parseDate(args[2], today);
    if (end < start) throw new CommandError("끝나는 날짜가 시작보다 빨라요.");
    delete d.dates;
    d.depart_from = iso(start);
    d.depart_to = iso(end);
    return { reply: `${d.name} 출발 기간을 ${iso(start)}~${iso(end)}로 바꿨어요.`, changed: true, commit: `${d.name} 기간` };
  },
  nights(cfg, args) {
    if (args.length < 2) throw new CommandError("이렇게 보내 주세요: /박수 도쿄 2,3");
    const d = findDest(cfg, args[0]);
    d.nights = parseNights(args[1]);
    return { reply: `${d.name} 박수를 ${d.nights.join(",")}박으로 바꿨어요.`, changed: true, commit: `${d.name} 박수` };
  },
  days(cfg, args) {
    if (args.length < 2) throw new CommandError("이렇게 보내 주세요: /요일 도쿄 목,금,토  (매일이면 전부)");
    const d = findDest(cfg, args[0]);
    const wd = parseWeekdays(args[1]);
    if (wd === null) delete d.depart_weekdays;
    else d.depart_weekdays = wd;
    return { reply: `${d.name} 출발 요일을 ${fmtWeekdays(wd)}로 바꿨어요.`, changed: true, commit: `${d.name} 요일` };
  },
  origin(cfg, args) {
    let code = args[0].toUpperCase();
    if (!/^[A-Z]{3}$/.test(code)) {
      const found = AIRPORTS[args[0]];
      if (!found) throw new CommandError("출발 공항은 영문 3글자 코드예요. 인천 ICN, 김포 GMP, 부산 PUS");
      code = found[0];
    }
    cfg.origin = code;
    return { reply: `출발 공항을 ${code}로 바꿨어요. 역대 최저가 기록은 공항별로 따로 쌓여요.`, changed: true, commit: `출발지 ${code}` };
  },
  pax(cfg, args) {
    if (args.length === 1) {
      const n = parsePax(args[0]);
      cfg.passengers = n;
      const overrides = cfg.destinations.filter((d) => d.passengers).map((d) => d.name);
      const note = overrides.length ? `\n(${overrides.join(", ")}은(는) 따로 정한 인원을 그대로 써요)` : "";
      return { reply: `기본 인원을 성인 ${n}명으로 바꿨어요. 기준가는 1인 기준으로 비교해요.${note}`, changed: true, commit: `인원 ${n}` };
    }
    const d = findDest(cfg, args[0]);
    if (["기본", "default", "삭제", "없음"].includes(args[1])) {
      delete d.passengers;
      return { reply: `${d.name}은(는) 기본 인원(성인 ${cfg.passengers || 1}명)을 따라요.`, changed: true, commit: `${d.name} 인원 기본` };
    }
    d.passengers = parsePax(args[1]);
    return { reply: `${d.name} 인원을 성인 ${d.passengers}명으로 바꿨어요. 알림에는 총액과 1인 가격이 같이 나와요.`, changed: true, commit: `${d.name} 인원 ${d.passengers}` };
  },
};

// ---------------------------------------------------------------------------
// 파싱·표시 도우미
// ---------------------------------------------------------------------------

function parsePrice(s) {
  const t = s.replace(/,/g, "").replace(/원/g, "").trim();
  if (/^\d+$/.test(t)) return parseInt(t, 10);
  const m = t.match(/^(?:(\d+(?:\.\d+)?)만)?(?:(\d+)천)?$/);
  if (m && (m[1] || m[2])) return Math.round(parseFloat(m[1] || 0) * 10000 + parseInt(m[2] || 0, 10) * 1000);
  throw new CommandError(`가격을 못 읽겠어요: ${s}\n예: 350000, 350,000, 35만`);
}

function parseDate(s, today) {
  let t = s.trim().replace(/[./]/g, "-");
  if (/^\d{8}$/.test(t)) t = `${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6)}`;
  let y, mo, d;
  let m = t.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (m) {
    [y, mo, d] = [+m[1], +m[2], +m[3]];
  } else {
    m = t.match(/^(\d{1,2})-(\d{1,2})$/);
    if (!m) throw new CommandError(`날짜를 못 읽겠어요: ${s}\n예: 2026-12-01`);
    [mo, d] = [+m[1], +m[2]];
    y = today.getUTCFullYear();
    const cand = mkDate(y, mo, d);
    if (cand && cand < today) y += 1;
  }
  const out = mkDate(y, mo, d);
  if (!out) throw new CommandError(`없는 날짜예요: ${s}`);
  return out;
}

function mkDate(y, mo, d) {
  const dt = new Date(Date.UTC(y, mo - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== mo - 1 || dt.getUTCDate() !== d) return null;
  return dt;
}
const iso = (dt) => dt.toISOString().slice(0, 10);
function kstToday() {
  const now = new Date(Date.now() + 9 * 3600 * 1000);
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
}

function parseAirports(s, name) {
  if (s === "?") {
    const codes = AIRPORTS[name];
    if (!codes) throw new CommandError(`'${name}'의 공항 코드를 몰라요. 3글자 코드를 직접 적어 주세요.\n예: /추가 ${name} XXX 2026-12-01 2027-02-28`);
    return [...codes];
  }
  const codes = s.split(/[,/]/).map((c) => c.trim().toUpperCase()).filter(Boolean);
  if (!codes.length || codes.some((c) => !/^[A-Z]{3}$/.test(c))) {
    throw new CommandError(`공항 코드는 영문 3글자예요: ${s}\n모르면 ? 라고 적으면 찾아 봐요.`);
  }
  return codes;
}

function parseNights(s) {
  const parts = s.replace(/박/g, "").split(/[,/]/).map((x) => x.trim()).filter(Boolean);
  if (!parts.length || parts.some((x) => !/^\d+$/.test(x))) throw new CommandError(`박수를 못 읽겠어요: ${s}\n예: 3,4`);
  const nights = [...new Set(parts.map((x) => parseInt(x, 10)))].sort((a, b) => a - b);
  if (nights.some((n) => n < 1 || n > 60)) throw new CommandError("박수는 1~60 사이 숫자예요. 예: 3,4");
  return nights;
}

function parseWeekdays(s) {
  if (["전부", "매일", "all", "any"].includes(s)) return null;
  const out = [];
  for (const part of s.split(/[,/]/)) {
    const p = part.trim().replace(/요일/g, "");
    if (!p) continue;
    if (WD_KO2EN[p]) out.push(WD_KO2EN[p]);
    else if (WEEKDAYS[p.toLowerCase().slice(0, 3)] !== undefined) out.push(p.toLowerCase().slice(0, 3));
    else throw new CommandError(`요일을 못 읽겠어요: ${part}\n예: 금,토 또는 전부`);
  }
  if (!out.length) throw new CommandError("요일을 적어 주세요. 예: 금,토 또는 전부");
  return [...new Set(out)];
}

function parsePax(s) {
  const t = s.replace(/명/g, "").trim();
  if (!/^\d+$/.test(t) || +t < 1 || +t > 9) throw new CommandError("인원은 1~9 사이 숫자예요. 예: /인원 2");
  return parseInt(t, 10);
}

function findDest(cfg, name) {
  const d = cfg.destinations.find((x) => x.name === name);
  if (d) return d;
  const names = cfg.destinations.map((x) => x.name).join(", ") || "(없음)";
  throw new CommandError(`'${name}' 여행지가 없어요. 지금 있는 것: ${names}`);
}

const fmtWeekdays = (wd) => (!wd || !wd.length ? "매일" : wd.map((w) => WD_EN2KO[w] || w).join(","));
const won = (n, cur = "KRW") => (cur === "KRW" ? `${Number(n).toLocaleString("en-US")}원` : `${Number(n).toLocaleString("en-US")} ${cur}`);
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function describe(cfg, d) {
  const when = d.dates
    ? d.dates.map(([a, b]) => `${a}~${b}`).join(", ")
    : `${d.depart_from}~${d.depart_to} 출발 · ${fmtWeekdays(d.depart_weekdays)} · ${(d.nights || [3]).join(",")}박`;
  const pax = Number(d.passengers || cfg.passengers || 1);
  const below = d.alert_below ? won(d.alert_below, cfg.currency) + (pax > 1 ? " (1인)" : "") : "없음";
  const who = d.passengers ? ` · 성인 ${pax}명` : "";
  return `<b>${esc(d.name)}</b> (${d.airports.join(", ")})${who}\n  ${when}\n  기준가 ${below}`;
}

// ---------------------------------------------------------------------------
// GitHub / 텔레그램 / KV
// ---------------------------------------------------------------------------

function ghHeaders(env) {
  return {
    Authorization: `Bearer ${env.GITHUB_TOKEN}`,
    Accept: "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "flight-watch-relay",
    "Content-Type": "application/json",
  };
}
const ghBase = (env) => `https://api.github.com/repos/${env.GITHUB_REPO}`;
const branch = (env) => env.GITHUB_BRANCH || "main";
const encPath = (p) => p.split("/").map(encodeURIComponent).join("/");

function b64decodeUtf8(b64) {
  const bin = atob(b64.replace(/\n/g, ""));
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}
function b64encodeUtf8(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

export async function ghGetFile(env, path) {
  const r = await fetch(`${ghBase(env)}/contents/${encPath(path)}?ref=${branch(env)}`, { headers: ghHeaders(env) });
  if (r.status === 404) throw new Error(`GitHub 404: ${path} 이 없어요`);
  if (!r.ok) throw new Error(`GitHub ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const j = await r.json();
  const text = b64decodeUtf8(j.content);
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {}
  return { text, json, sha: j.sha };
}

async function ghPutFile(env, path, content, sha, message) {
  const r = await fetch(`${ghBase(env)}/contents/${encPath(path)}`, {
    method: "PUT",
    headers: ghHeaders(env),
    body: JSON.stringify({ message, content: b64encodeUtf8(content), sha, branch: branch(env),
      committer: { name: "flight-watch-relay", email: "flight-watch-relay@users.noreply.github.com" } }),
  });
  if (!r.ok) throw new Error(`GitHub ${r.status}: ${(await r.text()).slice(0, 200)}`);
  return r.json();
}

async function ghDispatch(env, workflowFile, inputs) {
  const r = await fetch(`${ghBase(env)}/actions/workflows/${workflowFile}/dispatches`, {
    method: "POST",
    headers: ghHeaders(env),
    body: JSON.stringify({ ref: branch(env), inputs: inputs || {} }),
  });
  if (r.status !== 204) throw new Error(`GitHub ${r.status}: ${(await r.text()).slice(0, 200)}`);
}

async function ghDeleteHistory(env) {
  const r = await fetch(`${ghBase(env)}/contents/${encPath(HISTORY_DIR)}?ref=${branch(env)}`, { headers: ghHeaders(env) });
  if (r.status === 404) return 0;
  if (!r.ok) throw new Error(`GitHub ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const files = (await r.json()).filter((f) => f.type === "file" && f.name.endsWith(".json"));
  let n = 0;
  for (const f of files) {
    const d = await fetch(`${ghBase(env)}/contents/${encPath(f.path)}`, {
      method: "DELETE",
      headers: ghHeaders(env),
      body: JSON.stringify({ message: `flight-watch: 가격 기록 초기화 (${f.name})`, sha: f.sha, branch: branch(env) }),
    });
    if (d.ok) n += 1;
    else throw new Error(`GitHub ${d.status}: ${(await d.text()).slice(0, 200)}`);
  }
  return n;
}

/** /목록 에 역대 최저가를 붙이기 위해 history 파일을 읽어요. 실패해도 목록은 보여요. */
async function withHistory(env, cfg) {
  const safe = (name) => [...name].filter((ch) => /[\p{L}\p{N}_-]/u.test(ch)).join("");
  await Promise.all(cfg.destinations.map(async (d) => {
    try {
      const { json } = await ghGetFile(env, `${HISTORY_DIR}/${cfg.origin.toUpperCase()}-${safe(d.name)}.json`);
      if (json && json.best) d.__best = json.best;
    } catch {}
  }));
}

async function sendTelegram(env, text) {
  if (text.length > 4000) text = text.slice(0, 3990) + "\n…";
  const r = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: env.TELEGRAM_CHAT_ID, text, parse_mode: "HTML", disable_web_page_preview: true }),
  });
  if (!r.ok) console.error("sendMessage 실패:", r.status, await r.text());
}

async function loadPending(env) {
  if (!env.STATE) return null;
  try {
    const raw = await env.STATE.get("pending");
    if (!raw) return null;
    const p = JSON.parse(raw);
    if (Date.now() - Number(p.at || 0) > PENDING_TTL_MS) return null;
    return p.kind || null;
  } catch {
    return null;
  }
}
async function savePending(env, kind) {
  if (!env.STATE) return;
  if (kind) await env.STATE.put("pending", JSON.stringify({ kind, at: Date.now() }));
  else await env.STATE.delete("pending");
}

