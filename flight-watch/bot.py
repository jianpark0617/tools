#!/usr/bin/env python3
"""텔레그램 명령으로 감시 설정을 바꾸는 봇.

GitHub Actions가 몇 분마다 이 스크립트를 돌려요. 그동안 봇에게 온 메시지를 읽고,
명령이면 config.json을 고친 뒤 답장해요. 실시간은 아니고 보통 5~15분 안에 반영돼요.

명령어 (한글/영문 둘 다 돼요):
    /목록                                    감시 중인 여행지와 기준가
    /기준가 도쿄 350000                       기준가 바꾸기 (35만, 350,000 도 됨)
    /추가 방콕 BKK 2026-12-01 2027-02-28 450000
                                             여행지 추가 (이름 공항 출발기간 [기준가])
                                             뒤에 박수=5,6 요일=목,금 을 붙일 수 있어요
    /추가 후쿠오카 ? 2026-12-01 2027-02-28     공항 코드를 모르면 ? 로 찾아 줘요
    /삭제 방콕                                여행지 빼기
    /기간 도쿄 2027-01-01 2027-03-31           출발 가능 기간 바꾸기
    /박수 도쿄 2,3                             박수 바꾸기
    /요일 도쿄 목,금,토                         출발 요일 바꾸기 (전부 = 매일)
    /출발지 GMP                               출발 공항 바꾸기
    /요약                                     지금 최저가 요약 보내기
    /도움                                     이 안내

환경 변수: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (이 채팅의 메시지만 처리해요)
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from watch import (  # noqa: E402
    CONFIG_PATH,
    KST,
    WEEKDAYS,
    esc,
    history_path,
    load_history,
    send_telegram,
    won,
)

STATE_PATH = Path(__file__).resolve().parent / "bot_state.json"

WEEKDAY_KO_TO_EN = {"월": "mon", "화": "tue", "수": "wed", "목": "thu", "금": "fri", "토": "sat", "일": "sun"}
WEEKDAY_EN_TO_KO = {v: k for k, v in WEEKDAY_KO_TO_EN.items()}

# 자주 가는 곳의 공항 코드. "/추가 이름 ?" 으로 찾을 때 써요.
AIRPORTS: dict[str, list[str]] = {
    "도쿄": ["NRT", "HND"], "오사카": ["KIX"], "후쿠오카": ["FUK"], "삿포로": ["CTS"],
    "오키나와": ["OKA"], "나고야": ["NGO"], "교토": ["KIX"], "구마모토": ["KMJ"],
    "가고시마": ["KOJ"], "다카마쓰": ["TAK"], "마쓰야마": ["MYJ"], "히로시마": ["HIJ"],
    "센다이": ["SDJ"], "오이타": ["OIT"], "기타큐슈": ["KKJ"], "시즈오카": ["FSZ"],
    "타이베이": ["TPE"], "대만": ["TPE"], "가오슝": ["KHH"], "홍콩": ["HKG"], "마카오": ["MFM"],
    "상하이": ["PVG"], "베이징": ["PEK", "PKX"], "칭다오": ["TAO"],
    "방콕": ["BKK", "DMK"], "치앙마이": ["CNX"], "푸켓": ["HKT"],
    "다낭": ["DAD"], "나트랑": ["CXR"], "냐짱": ["CXR"], "호치민": ["SGN"], "하노이": ["HAN"],
    "푸꾸옥": ["PQC"], "달랏": ["DLI"],
    "세부": ["CEB"], "마닐라": ["MNL"], "보라카이": ["MPH", "KLO"], "클락": ["CRK"], "보홀": ["TAG"],
    "싱가포르": ["SIN"], "쿠알라룸푸르": ["KUL"], "코타키나발루": ["BKI"],
    "발리": ["DPS"], "자카르타": ["CGK"], "괌": ["GUM"], "사이판": ["SPN"],
    "울란바토르": ["UBN"], "몽골": ["UBN"], "블라디보스토크": ["VVO"],
    "델리": ["DEL"], "뭄바이": ["BOM"], "카트만두": ["KTM"], "콜롬보": ["CMB"], "몰디브": ["MLE"],
    "두바이": ["DXB"], "이스탄불": ["IST"], "도하": ["DOH"],
    "파리": ["CDG"], "런던": ["LHR"], "로마": ["FCO"], "바르셀로나": ["BCN"], "마드리드": ["MAD"],
    "프랑크푸르트": ["FRA"], "뮌헨": ["MUC"], "암스테르담": ["AMS"], "프라하": ["PRG"],
    "비엔나": ["VIE"], "빈": ["VIE"], "취리히": ["ZRH"], "밀라노": ["MXP"], "리스본": ["LIS"],
    "헬싱키": ["HEL"], "코펜하겐": ["CPH"],
    "뉴욕": ["JFK", "EWR"], "로스앤젤레스": ["LAX"], "LA": ["LAX"], "샌프란시스코": ["SFO"],
    "시애틀": ["SEA"], "라스베이거스": ["LAS"], "하와이": ["HNL"], "호놀룰루": ["HNL"],
    "밴쿠버": ["YVR"], "토론토": ["YYZ"], "시드니": ["SYD"], "멜버른": ["MEL"],
    "브리즈번": ["BNE"], "오클랜드": ["AKL"], "케언스": ["CNS"],
    "인천": ["ICN"], "김포": ["GMP"], "부산": ["PUS"], "김해": ["PUS"], "제주": ["CJU"],
    "대구": ["TAE"], "청주": ["CJJ"],
}

HELP = (
    "<b>항공권 감시 봇 명령어</b>\n"
    "/목록 — 감시 중인 여행지와 기준가\n"
    "/기준가 도쿄 350000 — 기준가 바꾸기 (35만 도 됨)\n"
    "/추가 방콕 BKK 2026-12-01 2027-02-28 450000 — 여행지 추가\n"
    "   공항을 모르면 ? : /추가 후쿠오카 ? 2026-12-01 2027-02-28\n"
    "   뒤에 박수=5,6 요일=목,금 을 붙일 수 있어요\n"
    "/삭제 방콕 — 여행지 빼기\n"
    "/기간 도쿄 2027-01-01 2027-03-31 — 출발 가능 기간\n"
    "/박수 도쿄 2,3 — 박수\n"
    "/요일 도쿄 목,금,토 — 출발 요일 (전부 = 매일)\n"
    "/출발지 GMP — 출발 공항\n"
    "/인원 2 — 인원 (성인). 여행지만 다르게: /인원 도쿄 4\n"
    "/요약 — 지금 최저가 요약\n"
    "/초기화 — 가격 기록 지우기 (처음부터 다시 비교)\n"
    "\n명령은 몇 분 뒤에 처리돼요. 처리되면 답장이 와요."
)

COMMANDS = {
    "목록": "list", "list": "list", "ls": "list",
    "기준가": "price", "price": "price",
    "추가": "add", "add": "add",
    "삭제": "del", "del": "del", "remove": "del", "rm": "del",
    "기간": "period", "period": "period",
    "박수": "nights", "nights": "nights",
    "요일": "weekdays", "weekdays": "weekdays", "days": "days",
    "출발지": "origin", "origin": "origin",
    "인원": "pax", "pax": "pax", "passengers": "pax", "adults": "pax",
    "요약": "summary", "summary": "summary",
    "초기화": "reset", "reset": "reset",
    "도움": "help", "help": "help", "start": "help",
}


class CommandError(Exception):
    """사용자에게 그대로 보여 줄 오류 메시지."""


# ---------------------------------------------------------------------------
# 파싱 도우미
# ---------------------------------------------------------------------------


def parse_price(s: str) -> int:
    """'350000', '350,000', '35만', '35만원', '35만5천' → 정수."""
    t = s.replace(",", "").replace("원", "").strip()
    if t.isdigit():
        return int(t)
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)만)?(?:(\d+)천)?", t)
    if m and (m.group(1) or m.group(2)):
        return int(float(m.group(1) or 0) * 10000 + int(m.group(2) or 0) * 1000)
    raise CommandError(f"가격을 못 읽겠어요: {s}\n예: 350000, 350,000, 35만")


def parse_date(s: str, today: date) -> date:
    """2026-12-01 / 2026.12.01 / 20261201 / 12-01 / 12/01 (연도 없으면 다가오는 날짜)."""
    t = s.strip().replace(".", "-").replace("/", "-")
    if re.fullmatch(r"\d{8}", t):
        t = f"{t[:4]}-{t[4:6]}-{t[6:]}"
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", t)
        if not m:
            raise CommandError(f"날짜를 못 읽겠어요: {s}\n예: 2026-12-01")
        mo, d = map(int, m.groups())
        y = today.year
        try:
            if date(y, mo, d) < today:
                y += 1
        except ValueError:
            pass
    try:
        return date(y, mo, d)
    except ValueError:
        raise CommandError(f"없는 날짜예요: {s}")


def parse_airports(s: str, name: str) -> list[str]:
    if s == "?":
        codes = AIRPORTS.get(name)
        if not codes:
            raise CommandError(
                f"'{name}'의 공항 코드를 몰라요. 3글자 코드를 직접 적어 주세요.\n"
                f"예: /추가 {name} XXX 2026-12-01 2027-02-28"
            )
        return codes
    codes = [c.strip().upper() for c in re.split(r"[,/]", s) if c.strip()]
    bad = [c for c in codes if not re.fullmatch(r"[A-Z]{3}", c)]
    if bad or not codes:
        raise CommandError(f"공항 코드는 영문 3글자예요: {s}\n모르면 ? 라고 적으면 찾아 봐요.")
    return codes


def parse_nights(s: str) -> list[int]:
    try:
        nights = sorted({int(x) for x in re.split(r"[,/]", s.replace("박", "")) if x.strip()})
    except ValueError:
        raise CommandError(f"박수를 못 읽겠어요: {s}\n예: 3,4")
    if not nights or any(n < 1 or n > 60 for n in nights):
        raise CommandError("박수는 1~60 사이 숫자예요. 예: 3,4")
    return nights


def parse_weekdays(s: str) -> list[str] | None:
    if s in ("전부", "매일", "all", "any"):
        return None
    out = []
    for part in re.split(r"[,/]", s):
        p = part.strip().replace("요일", "")
        if not p:
            continue
        if p in WEEKDAY_KO_TO_EN:
            out.append(WEEKDAY_KO_TO_EN[p])
        elif p.lower()[:3] in WEEKDAYS:
            out.append(p.lower()[:3])
        else:
            raise CommandError(f"요일을 못 읽겠어요: {part}\n예: 금,토 또는 전부")
    if not out:
        raise CommandError("요일을 적어 주세요. 예: 금,토 또는 전부")
    return list(dict.fromkeys(out))


def find_dest(cfg: dict, name: str) -> dict:
    for d in cfg["destinations"]:
        if d["name"] == name:
            return d
    names = ", ".join(d["name"] for d in cfg["destinations"]) or "(없음)"
    raise CommandError(f"'{name}' 여행지가 없어요. 지금 있는 것: {names}")


def fmt_weekdays(wd: list[str] | None) -> str:
    if not wd:
        return "매일"
    return ",".join(WEEKDAY_EN_TO_KO.get(w, w) for w in wd)


def describe(cfg: dict, d: dict) -> str:
    cur = cfg.get("currency", "KRW")
    if d.get("dates"):
        when = ", ".join(f"{a}~{b}" for a, b in d["dates"])
    else:
        when = (f"{d.get('depart_from')}~{d.get('depart_to')} 출발 · "
                f"{fmt_weekdays(d.get('depart_weekdays'))} · {','.join(map(str, d.get('nights', [3])))}박")
    pax = int(d.get("passengers") or cfg.get("passengers") or 1)
    below = won(int(d["alert_below"]), cur) + (" (1인)" if pax > 1 else "") if d.get("alert_below") else "없음"
    who = f" · 성인 {pax}명" if d.get("passengers") else ""
    return f"<b>{esc(d['name'])}</b> ({', '.join(d['airports'])}){who}\n  {when}\n  기준가 {below}"


# ---------------------------------------------------------------------------
# 명령 처리. 각 함수는 (답장, 설정이 바뀌었는지)를 돌려줘요.
# ---------------------------------------------------------------------------


def cmd_list(cfg: dict, args: list[str], today: date) -> tuple[str, bool]:
    origin = cfg["origin"]
    cur = cfg.get("currency", "KRW")
    if not cfg["destinations"]:
        return f"감시 중인 여행지가 없어요. /추가 로 넣어 보세요.", False
    lines = [f"<b>감시 중</b> · {origin} 출발 · 기본 성인 {cfg.get('passengers', 1)}명", ""]
    for d in cfg["destinations"]:
        lines.append(describe(cfg, d))
        hist = load_history(history_path(origin, d["name"]))
        best = hist.get("best")
        if best:
            bp = int(best.get("passengers", 1))
            shown = won(best["price"], cur) + (f" (성인 {bp}명, 1인 {won(round(best['price'] / bp), cur)})" if bp > 1 else "")
            lines.append(
                f"  역대 최저 <a href=\"{esc(best['url'])}\">{shown}</a> "
                f"({best['depart']}~{best['ret']} {best['airport']})"
            )
        lines.append("")
    return "\n".join(lines).rstrip(), False


def cmd_price(cfg: dict, args: list[str], today: date) -> tuple[str, bool]:
    if len(args) < 2:
        raise CommandError("이렇게 보내 주세요: /기준가 도쿄 350000")
    d = find_dest(cfg, args[0])
    if args[1] in ("없음", "삭제", "none", "off"):
        d.pop("alert_below", None)
        return f"{d['name']} 기준가를 없앴어요. 역대 최저가 갱신 때만 알려요.", True
    price = parse_price(" ".join(args[1:]))
    d["alert_below"] = price
    return f"{d['name']} 기준가를 {won(price, cfg.get('currency', 'KRW'))}으로 바꿨어요.", True


def cmd_add(cfg: dict, args: list[str], today: date) -> tuple[str, bool]:
    # /추가 이름 공항 시작 끝 [기준가] [박수=3,4] [요일=금,토]
    opts = {}
    plain = []
    for a in args:
        m = re.fullmatch(r"(박수|요일|인원|nights|weekdays|days|pax|adults)=(.+)", a)
        if m:
            opts[m.group(1)] = m.group(2)
        else:
            plain.append(a)
    if len(plain) < 4:
        raise CommandError(
            "이렇게 보내 주세요:\n/추가 방콕 BKK 2026-12-01 2027-02-28 450000\n"
            "공항을 모르면 ? : /추가 후쿠오카 ? 2026-12-01 2027-02-28"
        )
    name = plain[0]
    if any(d["name"] == name for d in cfg["destinations"]):
        raise CommandError(f"'{name}'은 이미 있어요. 바꾸려면 /기준가, /기간, /박수, /요일 을 쓰세요.")
    airports = parse_airports(plain[1], name)
    start, end = parse_date(plain[2], today), parse_date(plain[3], today)
    if end < start:
        raise CommandError("끝나는 날짜가 시작보다 빨라요.")
    if end < today:
        raise CommandError("이미 지난 기간이에요.")
    dest = {
        "name": name,
        "airports": airports,
        "depart_from": start.isoformat(),
        "depart_to": end.isoformat(),
        "depart_weekdays": ["fri", "sat"],
        "nights": [3, 4],
    }
    if len(plain) >= 5:
        dest["alert_below"] = parse_price(" ".join(plain[4:]))
    if "박수" in opts or "nights" in opts:
        dest["nights"] = parse_nights(opts.get("박수") or opts["nights"])
    pax_raw = opts.get("인원") or opts.get("pax") or opts.get("adults")
    if pax_raw:
        dest["passengers"] = parse_pax(pax_raw)
    wd_raw = opts.get("요일") or opts.get("weekdays") or opts.get("days")
    if wd_raw:
        wd = parse_weekdays(wd_raw)
        if wd is None:
            dest.pop("depart_weekdays")
        else:
            dest["depart_weekdays"] = wd
    cfg["destinations"].append(dest)
    return "추가했어요. 다음 조회부터 감시해요.\n\n" + describe(cfg, dest), True


def cmd_del(cfg: dict, args: list[str], today: date) -> tuple[str, bool]:
    if not args:
        raise CommandError("이렇게 보내 주세요: /삭제 방콕")
    d = find_dest(cfg, args[0])
    cfg["destinations"].remove(d)
    return f"{d['name']}을(를) 감시 목록에서 뺐어요.", True


def cmd_period(cfg: dict, args: list[str], today: date) -> tuple[str, bool]:
    if len(args) < 3:
        raise CommandError("이렇게 보내 주세요: /기간 도쿄 2027-01-01 2027-03-31")
    d = find_dest(cfg, args[0])
    start, end = parse_date(args[1], today), parse_date(args[2], today)
    if end < start:
        raise CommandError("끝나는 날짜가 시작보다 빨라요.")
    d.pop("dates", None)
    d["depart_from"], d["depart_to"] = start.isoformat(), end.isoformat()
    return f"{d['name']} 출발 기간을 {start}~{end}로 바꿨어요.", True


def cmd_nights(cfg: dict, args: list[str], today: date) -> tuple[str, bool]:
    if len(args) < 2:
        raise CommandError("이렇게 보내 주세요: /박수 도쿄 2,3")
    d = find_dest(cfg, args[0])
    d["nights"] = parse_nights(args[1])
    return f"{d['name']} 박수를 {','.join(map(str, d['nights']))}박으로 바꿨어요.", True


def cmd_weekdays(cfg: dict, args: list[str], today: date) -> tuple[str, bool]:
    if len(args) < 2:
        raise CommandError("이렇게 보내 주세요: /요일 도쿄 목,금,토  (매일이면 전부)")
    d = find_dest(cfg, args[0])
    wd = parse_weekdays(args[1])
    if wd is None:
        d.pop("depart_weekdays", None)
    else:
        d["depart_weekdays"] = wd
    return f"{d['name']} 출발 요일을 {fmt_weekdays(wd)}로 바꿨어요.", True


def parse_pax(s: str) -> int:
    t = s.replace("명", "").strip()
    if not t.isdigit() or not 1 <= int(t) <= 9:
        raise CommandError("인원은 1~9 사이 숫자예요. 예: /인원 2")
    return int(t)


def cmd_pax(cfg: dict, args: list[str], today: date) -> tuple[str, bool]:
    """/인원 2 → 전체 기본 인원. /인원 도쿄 4 → 도쿄만. /인원 도쿄 기본 → 도쿄 따로 설정 해제."""
    if not args:
        raise CommandError("이렇게 보내 주세요: /인원 2  (여행지만 다르게: /인원 도쿄 4)")
    if len(args) == 1:
        n = parse_pax(args[0])
        cfg["passengers"] = n
        overrides = [d["name"] for d in cfg["destinations"] if d.get("passengers")]
        note = f"\n({', '.join(overrides)}은(는) 따로 정한 인원을 그대로 써요)" if overrides else ""
        return f"기본 인원을 성인 {n}명으로 바꿨어요. 기준가는 1인 기준으로 비교해요.{note}", True
    d = find_dest(cfg, args[0])
    if args[1] in ("기본", "default", "삭제", "없음"):
        d.pop("passengers", None)
        return f"{d['name']}은(는) 기본 인원(성인 {cfg.get('passengers', 1)}명)을 따라요.", True
    d["passengers"] = parse_pax(args[1])
    return f"{d['name']} 인원을 성인 {d['passengers']}명으로 바꿨어요. 알림에는 총액과 1인 가격이 같이 나와요.", True


def cmd_origin(cfg: dict, args: list[str], today: date) -> tuple[str, bool]:
    if not args:
        raise CommandError("이렇게 보내 주세요: /출발지 GMP")
    code = args[0].upper()
    if not re.fullmatch(r"[A-Z]{3}", code):
        found = AIRPORTS.get(args[0])
        if not found:
            raise CommandError("출발 공항은 영문 3글자 코드예요. 인천 ICN, 김포 GMP, 부산 PUS")
        code = found[0]
    cfg["origin"] = code
    return f"출발 공항을 {code}로 바꿨어요. 역대 최저가 기록은 공항별로 따로 쌓여요.", True


def cmd_reset(cfg: dict, args: list[str], today: date) -> tuple[str, bool]:
    """가격 기록을 지워요. Fork로 받은 남의 기록을 비우거나, 처음부터 다시 비교하고 싶을 때."""
    from watch import HISTORY_DIR

    n = 0
    for f in HISTORY_DIR.glob("*.json"):
        f.unlink()
        n += 1
    return f"가격 기록 {n}개를 지웠어요. 다음 조회 때 '첫 조회 결과'부터 다시 시작해요.", False


def cmd_help(cfg: dict, args: list[str], today: date) -> tuple[str, bool]:
    return HELP, False


HANDLERS = {
    "list": cmd_list, "price": cmd_price, "add": cmd_add, "del": cmd_del,
    "period": cmd_period, "nights": cmd_nights, "weekdays": cmd_weekdays, "days": cmd_weekdays,
    "origin": cmd_origin, "help": cmd_help, "reset": cmd_reset, "pax": cmd_pax,
}


# 텔레그램 메뉴에서 명령을 고르면 인자 없이 바로 전송돼요. 그러면 뭘 적을지 물어보고,
# 다음에 오는 메시지를 그 답으로 받아요. (state["pending"]에 기다리는 명령을 저장)
PENDING_TTL_MINUTES = 60


def ask_for_args(cfg: dict, kind: str) -> str:
    names = ", ".join(d["name"] for d in cfg["destinations"]) or "(아직 없음)"
    first = cfg["destinations"][0]["name"] if cfg["destinations"] else "도쿄"
    prompts = {
        "price": f"어느 여행지 기준가를 얼마로 바꿀까요?\n예: {first} 35만\n여행지: {names}",
        "add": "추가할 여행지를 이렇게 적어 주세요:\n이름 공항 출발시작 출발끝 [기준가]\n예: 방콕 BKK 12-01 2-28 45만\n공항을 모르면 ? : 후쿠오카 ? 12-01 2-28",
        "del": f"어느 여행지를 뺄까요?\n여행지: {names}",
        "period": f"어느 여행지의 출발 기간을 언제로 바꿀까요?\n예: {first} 2027-01-01 2027-03-31",
        "nights": f"어느 여행지를 몇 박으로 바꿀까요?\n예: {first} 2,3",
        "weekdays": f"어느 여행지의 출발 요일을 바꿀까요?\n예: {first} 목,금,토  (매일이면 전부)",
        "days": f"어느 여행지의 출발 요일을 바꿀까요?\n예: {first} 목,금,토  (매일이면 전부)",
        "origin": "출발 공항 코드를 적어 주세요.\n예: GMP (인천 ICN, 김포 GMP, 부산 PUS)",
        "pax": f"몇 명인가요? 숫자만 적으면 기본 인원이 바뀌어요.\n예: 2\n여행지만 다르게: {first} 4",
    }
    return prompts[kind] + "\n\n다음에 보내는 메시지를 그 답으로 받아요. 취소하려면 아무 명령이나 새로 보내세요."


def handle_text(cfg: dict, text: str, today: date, pending: str | None = None,
                skip_prompt: bool = False) -> tuple[str, bool, bool, str | None]:
    """메시지 하나를 처리해요. (답장, 설정 변경 여부, 요약 요청 여부, 다음에 기다릴 명령)

    pending: 직전에 인자 없이 온 명령. 이번 메시지가 명령이 아니면 그 인자로 써요.
    skip_prompt: 바로 뒤에 답이 이어져 오는 게 보이면 물어보는 답장을 생략해요.
    """
    text = text.strip()
    is_command = text.startswith("/")
    if pending and not is_command and text:
        text = f"/{pending} {text}"
        is_command = True
    parts = text.lstrip("/").split()
    if not parts:
        return HELP, False, False, None
    word = parts[0].split("@")[0].lower()
    kind = COMMANDS.get(word)
    if kind is None:
        if not is_command:
            return "명령어는 /로 시작해요. /도움 을 보내 보세요.", False, False, None
        return f"모르는 명령어예요: /{esc(parts[0])}\n\n{HELP}", False, False, None
    if kind == "summary":
        return "지금 최저가를 조회해서 요약을 보낼게요. 3~5분 걸려요.", False, True, None
    if not parts[1:] and kind in ("price", "add", "del", "period", "nights", "weekdays", "days", "origin", "pax"):
        return ("" if skip_prompt else ask_for_args(cfg, kind)), False, False, kind
    try:
        reply, changed = HANDLERS[kind](cfg, parts[1:], today)
    except CommandError as e:
        return f"⚠️ {esc(str(e))}", False, False, None
    return reply, changed, False, None


# ---------------------------------------------------------------------------
# 텔레그램 입출력과 상태
# ---------------------------------------------------------------------------


# 텔레그램에서 "/"를 누르면 나오는 메뉴. 명령 이름은 영문 소문자만 허용돼서(텔레그램 규칙)
# 영문 별칭을 올리고 설명을 한글로 달아요. 한글 명령(/기준가 등)도 계속 돼요.
MENU_VERSION = 1
COMMAND_MENU = [
    ("list", "감시 중인 여행지와 기준가, 역대 최저가"),
    ("price", "기준가 바꾸기: /price 도쿄 35만"),
    ("add", "여행지 추가: /add 방콕 BKK 12-01 2-28 45만"),
    ("del", "여행지 빼기: /del 방콕"),
    ("pax", "인원: /pax 2 (여행지만: /pax 도쿄 4)"),
    ("period", "출발 기간: /period 도쿄 2027-01-01 2027-03-31"),
    ("nights", "박수: /nights 도쿄 2,3"),
    ("days", "출발 요일: /days 도쿄 목,금,토 (매일은 전부)"),
    ("origin", "출발 공항: /origin GMP"),
    ("summary", "지금 최저가 요약 받기 (3~5분)"),
    ("reset", "가격 기록 지우기"),
    ("help", "명령어 안내"),
]


def tg_set_commands(token: str) -> None:
    data = urllib.parse.urlencode({
        "commands": json.dumps([{"command": c, "description": d[:256]} for c, d in COMMAND_MENU]),
    }).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/setMyCommands", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    if not body.get("ok"):
        raise RuntimeError(f"setMyCommands 실패: {body}")


def tg_get_updates(token: str, offset: int | None) -> list[dict]:
    params = {"timeout": "0", "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        params["offset"] = str(offset)
    url = f"https://api.telegram.org/bot{token}/getUpdates?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    if not body.get("ok"):
        raise RuntimeError(f"getUpdates 실패: {body}")
    return body["result"]


def load_state() -> dict:
    if STATE_PATH.exists():
        with STATE_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    return {"offset": None}


def save_state(state: dict) -> None:
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
        f.write("\n")


def save_config(cfg: dict, path: Path | None = None) -> None:
    with (path or CONFIG_PATH).open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")


def set_output(name: str, value: str) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"{name}={value}\n")


def main() -> int:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID가 없어서 할 일이 없어요.")
        set_output("summary", "false")
        return 0

    with CONFIG_PATH.open(encoding="utf-8") as f:
        cfg = json.load(f)
    state = load_state()
    today = datetime.now(KST).date()

    # 토큰의 앞부분은 봇 번호예요. Fork로 받은 저장소에는 원래 주인 봇의 읽은 위치가
    # 들어 있을 수 있어서, 봇이 다르면 처음부터 다시 읽어요.
    bot_id = token.split(":")[0]
    if "bot_id" not in state:
        state["bot_id"] = bot_id  # 예전 형식의 상태 파일: 번호만 적어 둬요
    elif state["bot_id"] != bot_id:
        print("봇이 바뀌어서 읽은 위치를 초기화해요.")
        state = {"offset": None, "bot_id": bot_id}

    # "/" 메뉴 등록 (봇마다 한 번, 메뉴가 바뀌면 다시)
    if state.get("menu_version") != MENU_VERSION:
        try:
            tg_set_commands(token)
            state["menu_version"] = MENU_VERSION
            print("텔레그램 명령 메뉴 등록")
        except Exception as e:
            print(f"명령 메뉴 등록 실패 (다음에 다시 시도): {e}")

    updates = tg_get_updates(token, state.get("offset"))
    print(f"새 업데이트 {len(updates)}건")
    changed = False
    want_summary = False

    # 내 채팅의 글 메시지만 순서대로
    texts: list[str] = []
    for u in updates:
        state["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        text = msg.get("text")
        if not text:
            continue
        if str(msg.get("chat", {}).get("id")) != chat_id:
            print(f"  다른 채팅({msg.get('chat', {}).get('id')})의 메시지는 무시")
            continue
        texts.append(text)

    # 지난번에 인자 없이 온 명령이 있으면 이어받아요 (너무 오래됐으면 버려요)
    pending = None
    pend = state.get("pending")
    if pend:
        try:
            age = datetime.now(KST) - datetime.fromisoformat(pend["at"])
        except (KeyError, ValueError):
            age = timedelta(days=1)
        if age <= timedelta(minutes=PENDING_TTL_MINUTES):
            pending = pend["kind"]

    for i, text in enumerate(texts):
        print(f"  > {text}")
        nxt = texts[i + 1] if i + 1 < len(texts) else None
        skip_prompt = bool(nxt) and not nxt.strip().startswith("/")
        reply, did_change, summary, pending = handle_text(cfg, text, today, pending, skip_prompt)
        changed = changed or did_change
        want_summary = want_summary or summary
        if not reply:
            continue
        print(f"  < {reply.splitlines()[0]}")
        try:
            send_telegram(reply)
        except Exception as e:  # 답장 실패해도 다음 메시지는 처리
            print(f"  ! 답장 실패: {e}")

    state["pending"] = {"kind": pending, "at": datetime.now(KST).isoformat(timespec="minutes")} if pending else None

    if changed:
        save_config(cfg)
        print("config.json 저장")
    if state != load_state():
        save_state(state)
    set_output("summary", "true" if want_summary else "false")
    set_output("changed", "true" if changed else "false")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
