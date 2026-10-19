// flight-watch 중계기 (Cloudflare Worker)
//
// GitHub Actions의 예약 실행은 몇 시간씩 밀릴 수 있어요. 이 Worker가 1분마다
// 텔레그램에 새 메시지가 왔는지 보고, 왔으면 GitHub의 봇 워크플로를 바로 깨워요.
// 메시지 내용은 읽기만 하고 확인(offset) 처리는 하지 않아요. 확인은 봇(bot.py)이 해요.
// 그래서 이 Worker가 죽어도 메시지는 사라지지 않고, 예약 실행이 안전망으로 남아요.
//
// 필요한 설정 (README.md 참고):
//   Secrets/변수: TELEGRAM_BOT_TOKEN, GITHUB_TOKEN, GITHUB_REPO (예: jianpark0617/tools)
//   선택: TELEGRAM_CHAT_ID (내 채팅 메시지일 때만 깨우기), GITHUB_BRANCH (기본 main), CHECK_KEY
//   KV 바인딩: STATE
//   Cron Trigger: * * * * *

const WORKFLOW_FILE = "flight-bot.yml";
const REDISPATCH_AFTER_MS = 5 * 60 * 1000; // 깨웠는데 5분 넘게 처리가 안 되면 다시 깨워요

export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(check(env));
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    // 수동 점검: https://<worker>/check?key=<CHECK_KEY>
    if (url.pathname === "/check") {
      if (!env.CHECK_KEY || url.searchParams.get("key") !== env.CHECK_KEY) {
        return new Response("forbidden", { status: 403 });
      }
      return Response.json(await check(env));
    }
    return new Response("flight-watch relay: ok\n");
  },
};

async function check(env) {
  const missing = ["TELEGRAM_BOT_TOKEN", "GITHUB_TOKEN", "GITHUB_REPO"].filter((k) => !env[k]);
  if (missing.length) return { ok: false, error: `설정이 없어요: ${missing.join(", ")}` };
  if (!env.STATE) return { ok: false, error: "KV 바인딩(STATE)이 없어요" };

  // 확인하지 않은(아직 봇이 읽지 않은) 메시지만 돌려줘요. offset을 안 보내니 확인 처리도 안 돼요.
  const tg = await fetch(
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/getUpdates?timeout=0&allowed_updates=%5B%22message%22%5D`
  );
  const body = await tg.json();
  if (!body.ok) return { ok: false, error: body }; // 409 = 봇이 지금 읽는 중. 다음 분에 다시.

  const updates = body.result || [];
  if (!updates.length) return { ok: true, pending: 0 };

  const mine = env.TELEGRAM_CHAT_ID
    ? updates.filter((u) => String(u.message?.chat?.id) === String(env.TELEGRAM_CHAT_ID))
    : updates;
  const maxId = Math.max(...updates.map((u) => u.update_id));
  const lastId = Number((await env.STATE.get("last_dispatched_id")) || 0);
  const lastAt = Number((await env.STATE.get("last_dispatched_at")) || 0);
  const now = Date.now();

  const isNew = maxId > lastId;
  const stuck = now - lastAt > REDISPATCH_AFTER_MS; // 이미 깨웠는데 아직 안 읽힘
  if (!isNew && !stuck) return { ok: true, pending: updates.length, dispatched: false, reason: "already dispatched" };
  if (!mine.length && isNew) {
    // 남의 메시지뿐이면 깨우지 않아요. 봇이 다음 예약 실행 때 읽고 무시해요.
    await env.STATE.put("last_dispatched_id", String(maxId));
    return { ok: true, pending: updates.length, dispatched: false, reason: "not my chat" };
  }

  const gh = await fetch(
    `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "flight-watch-relay",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref: env.GITHUB_BRANCH || "main" }),
    }
  );
  if (gh.status !== 204) {
    return { ok: false, error: `GitHub ${gh.status}: ${(await gh.text()).slice(0, 200)}` };
  }
  await env.STATE.put("last_dispatched_id", String(maxId));
  await env.STATE.put("last_dispatched_at", String(now));
  return { ok: true, pending: updates.length, dispatched: true, maxId };
}
