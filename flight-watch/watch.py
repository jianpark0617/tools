#!/usr/bin/env python3
"""항공권 최저가 감시기.

config.json에 적힌 여행지마다 Google Flights를 조회해서 최저가를 찾고,
기준가 아래로 내려가거나 역대 최저가가 갱신되면 텔레그램으로 알려 줘요.
조회 결과는 history/ 폴더에 쌓여서 다음 실행 때 비교 기준이 돼요.

사용법:
    python watch.py                 # 조회 → 조건 맞으면 알림
    python watch.py --summary       # 조건과 상관없이 현재 최저가 요약을 보냄
    python watch.py --no-send       # 텔레그램으로 보내지 않고 화면에만 출력
    python watch.py --only 도쿄     # 특정 여행지만 조회

환경 변수:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  (둘 중 하나라도 없으면 화면 출력만 해요)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "config.json"
HISTORY_DIR = HERE / "history"
KST = ZoneInfo("Asia/Seoul")

WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
WEEKDAY_KO = "월화수목금토일"
MAX_RUNS_KEPT = 400

# 다시 알리는 기준: 마지막으로 알린 가격보다 이만큼(비율) 더 싸졌을 때,
# 또는 마지막 알림 뒤로 이만큼(일) 지났는데 여전히 기준가 아래일 때.
REALERT_DROP_RATIO = 0.03
REALERT_AFTER_DAYS = 7

# Google이 가끔 빈 오류 응답을 줘요. 이만큼 다시 시도해요.
FETCH_RETRIES = 3
RETRY_BACKOFF_SECONDS = 4


# ---------------------------------------------------------------------------
# 데이터 구조
# ---------------------------------------------------------------------------


@dataclass
class Combo:
    """조회할 날짜 조합 하나."""

    depart: date
    ret: date
    airport: str

    @property
    def nights(self) -> int:
        return (self.ret - self.depart).days

    @property
    def key(self) -> str:
        return f"{self.depart.isoformat()}|{self.ret.isoformat()}|{self.airport}"


@dataclass
class Offer:
    """조회 결과 중 가장 싼 항공편 하나."""

    price: int
    depart: str
    ret: str
    airport: str
    airlines: list[str]
    stops: int
    depart_time: str
    url: str
    seen_at: str
    passengers: int = 1  # 가격(price)은 이 인원의 총액이에요

    @property
    def nights(self) -> int:
        return (date.fromisoformat(self.ret) - date.fromisoformat(self.depart)).days

    @property
    def per_person(self) -> int:
        return round(self.price / max(self.passengers, 1))

    @property
    def key_(self) -> str:
        return f"{self.depart}|{self.ret}|{self.airport}"


# ---------------------------------------------------------------------------
# 설정과 날짜 조합
# ---------------------------------------------------------------------------


def load_config(path: Path = CONFIG_PATH) -> dict:
    with path.open(encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("destinations"):
        raise SystemExit("config.json에 destinations가 비어 있어요.")
    for d in cfg["destinations"]:
        for key in ("name", "airports"):
            if not d.get(key):
                raise SystemExit(f"여행지 설정에 '{key}'가 없어요: {d}")
        if not d.get("dates") and not (d.get("depart_from") and d.get("depart_to")):
            raise SystemExit(
                f"'{d['name']}'에는 dates 또는 depart_from/depart_to가 필요해요."
            )
    return cfg


def build_combos(dest: dict, today: date) -> list[Combo]:
    """여행지 설정 하나를 조회할 (출발일, 귀국일, 공항) 목록으로 펼쳐요."""
    airports = [a.upper() for a in dest["airports"]]
    pairs: list[tuple[date, date]] = []

    if dest.get("dates"):
        # 날짜를 직접 지정한 경우: [["2026-12-18", "2026-12-22"], ...]
        for dep, ret in dest["dates"]:
            pairs.append((date.fromisoformat(dep), date.fromisoformat(ret)))
    else:
        start = date.fromisoformat(dest["depart_from"])
        end = date.fromisoformat(dest["depart_to"])
        nights = [int(n) for n in dest.get("nights", [3])]
        wd_names = dest.get("depart_weekdays")
        weekdays = (
            {WEEKDAYS[w.lower()[:3]] for w in wd_names} if wd_names else set(range(7))
        )
        d = start
        while d <= end:
            if d.weekday() in weekdays:
                for n in nights:
                    pairs.append((d, d + timedelta(days=n)))
            d += timedelta(days=1)

    combos = [
        Combo(depart=dep, ret=ret, airport=ap)
        for dep, ret in pairs
        if dep > today  # 이미 지난 날짜는 건너뛰어요
        for ap in airports
    ]
    combos.sort(key=lambda c: (c.depart, c.ret, c.airport))
    return combos


# 빠른 조회: 기록상 가장 쌌던 날짜 몇 개와 아직 안 본 날짜 몇 개만 봐요. 특가가 잠깐 떴다 사라지는 걸
# 잡으려고 몇 시간마다 돌리는 용도예요. 전체 조회는 하루 한 번 그대로.
QUICK_KNOWN = 15
QUICK_NEW = 5


def quick_combos(combos: list[Combo], hist: dict) -> list[Combo]:
    """빠른 조회에 쓸 조합: 기록상 1인 가격이 싼 순 QUICK_KNOWN개 + 기록에 없는 조합 QUICK_NEW개."""
    by_key = {c.key: c for c in combos}
    known = [(per_person_of(v), k) for k, v in hist.get("combos", {}).items() if k in by_key]
    known.sort()
    picked = [by_key[k] for _, k in known[:QUICK_KNOWN]]
    seen = {c.key for c in picked}
    # 기록이 적을수록(처음 며칠) 새 날짜를 더 많이 봐서 총 QUICK_KNOWN + QUICK_NEW 개를 채워요.
    fresh = [c for c in combos if c.key not in hist.get("combos", {}) and c.key not in seen]
    picked += fresh[: QUICK_KNOWN + QUICK_NEW - len(picked)]
    if not picked:
        picked = combos[: QUICK_KNOWN + QUICK_NEW]
    return sorted(picked, key=lambda c: (c.depart, c.ret, c.airport))


def rotate_combos(combos: list[Combo], cap: int, today: date) -> list[Combo]:
    """조합이 너무 많으면 하루에 cap개씩 돌아가며 조회해요."""
    if cap <= 0 or len(combos) <= cap:
        return combos
    offset = (today.toordinal() * cap) % len(combos)
    doubled = combos + combos
    return doubled[offset : offset + cap]


# ---------------------------------------------------------------------------
# Google Flights 조회
# ---------------------------------------------------------------------------


def airline_filter(cfg: dict, dest: dict) -> tuple[set[str], set[str]]:
    """(포함할 항공사 코드, 제외할 항공사 코드). 여행지 설정이 있으면 그걸, 없으면 전체 설정을 써요.

    Google은 한국·일본 저가항공사의 수하물 요금을 모르기 때문에 "수하물 포함 가격"을 직접 줄 수
    없어요. 대신 저가항공사를 빼면 남는 대형항공사 요금은 보통 위탁수하물이 포함이라, 그 용도로 써요.
    """
    def pick(key: str) -> set[str]:
        v = dest.get(key) if key in dest else cfg.get(key)
        return {str(c).upper() for c in (v or [])}
    return pick("airlines"), pick("exclude_airlines")


def airline_code(leg: dict) -> str:
    """구간의 항공사 코드 (편명 '7C1393' → '7C')."""
    return (leg.get("flight") or "")[:2].upper()


def passes_airline_filter(offer: dict, include: set[str], exclude: set[str]) -> bool:
    codes = {airline_code(l) for l in offer.get("legs", []) if l.get("flight")}
    if include and not codes <= include:
        return False
    if exclude and codes & exclude:
        return False
    return True


def filter_label(cfg: dict, dest: dict) -> str:
    include, exclude = airline_filter(cfg, dest)
    if include:
        return ",".join(sorted(include)) + "만"
    if exclude:
        return "저가항공 제외" if len(exclude) >= 8 else ",".join(sorted(exclude)) + " 제외"
    return ""


def dest_passengers(cfg: dict, dest: dict) -> int:
    """여행지별 인원이 있으면 그걸, 없으면 전체 설정의 인원을 써요 (성인 기준)."""
    n = int(dest.get("passengers") or cfg.get("passengers") or 1)
    return min(max(n, 1), 9)


def make_query(cfg: dict, origin: str, combo: Combo, passengers: int = 1):
    from fast_flights import FlightQuery, Passengers, create_query

    max_stops = cfg.get("max_stops")
    return create_query(
        flights=[
            FlightQuery(date=combo.depart.isoformat(), from_airport=origin,
                        to_airport=combo.airport, max_stops=max_stops),
            FlightQuery(date=combo.ret.isoformat(), from_airport=combo.airport,
                        to_airport=origin, max_stops=max_stops),
        ],
        trip="round-trip",
        seat=cfg.get("seat", "economy"),
        passengers=Passengers(adults=passengers),
        language=cfg.get("language", "ko"),
        currency=cfg.get("currency", "KRW"),
    )


class GoogleTransientError(Exception):
    """Google이 일시적인 오류 응답을 보냈어요. 잠시 뒤 다시 시도하면 보통 돼요."""


def parse_offers(html: str) -> list[dict]:
    """Google Flights 결과 HTML에서 항공편 목록을 뽑아요.

    fast-flights의 내장 파서는 "다른 항공편"(payload[3])만 읽고, 가격이 없는 항목을
    만나면 IndexError로 죽어요. 그래서 "추천 항공편"(payload[2])까지 함께 읽고
    가격 없는 항목은 건너뛰는 파서를 직접 둬요. (구조 발견: fast-flights/@kftang)
    """
    from selectolax.lexbor import LexborHTMLParser

    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise GoogleTransientError("결과 데이터(script.ds:1)가 없어요. 차단되었거나 페이지 구조가 바뀐 듯해요.")
    js = script.text()
    data = js.split("data:", 1)[1].rsplit(",", 1)[0]
    if data.rstrip().endswith("errorHasStatus: true"):
        raise GoogleTransientError("Google ErrorResponse")
    payload = json.loads(data)

    offers: list[dict] = []
    for idx in (2, 3):  # 2 = 추천 항공편, 3 = 다른 항공편
        section = payload[idx] if len(payload) > idx else None
        if not section or not section[0]:
            continue
        for item in section[0]:
            try:
                price = item[1][0][1]
            except (IndexError, TypeError):
                continue  # 가격 미정 항목
            if not isinstance(price, int) or price <= 0:
                continue
            flight = item[0]
            legs = []
            for leg in flight[2]:
                dep = leg[8] or []
                dep_h, dep_m = (dep + [0, 0])[:2]
                num = leg[22] if len(leg) > 22 and leg[22] else None
                legs.append({
                    "from": leg[3], "to": leg[6],
                    "dep_time": f"{dep_h or 0:02d}:{dep_m or 0:02d}",
                    "flight": f"{num[0]}{num[1]}" if num else "",
                })
            offers.append({"price": price, "airlines": list(flight[1] or []), "legs": legs})
    return offers


def fetch_cheapest(cfg: dict, origin: str, combo: Combo, now: datetime,
                   passengers: int = 1, airlines: tuple[set[str], set[str]] | None = None) -> Offer | None:
    """조합 하나를 조회해서 가장 싼 항공편을 돌려줘요. 결과가 없으면 None.

    Google은 인원수만큼 곱한 총액을 돌려줘요. (확인: 1명 285,900 → 2명 571,800)
    """
    from fast_flights import fetch_flights_html

    q = make_query(cfg, origin, combo, passengers)
    last_exc: Exception | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            html = fetch_flights_html(q)
            offers = parse_offers(html)
            break
        except (GoogleTransientError, OSError, ValueError) as e:
            last_exc = e
            time.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
    else:
        raise RuntimeError(f"{FETCH_RETRIES}번 시도 후 포기: {last_exc}")

    if not offers:
        return None
    if airlines:
        offers = [o for o in offers if passes_airline_filter(o, *airlines)]
        if not offers:
            return None
    best = min(offers, key=lambda o: o["price"])

    # 왕복 결과는 가는 편 구간 목록에 왕복 총액이 붙어 있어요.
    legs = best["legs"]
    return Offer(
        price=best["price"],
        depart=combo.depart.isoformat(),
        ret=combo.ret.isoformat(),
        airport=combo.airport,
        airlines=list(dict.fromkeys(best["airlines"])),
        stops=max(len(legs) - 1, 0),
        depart_time=legs[0]["dep_time"] if legs else "",
        url=q.url(),
        seen_at=now.isoformat(timespec="minutes"),
        passengers=passengers,
    )


# ---------------------------------------------------------------------------
# 기록(history)
# ---------------------------------------------------------------------------


def history_path(origin: str, dest_name: str) -> Path:
    safe = "".join(ch for ch in dest_name if ch.isalnum() or ch in "-_")
    return HISTORY_DIR / f"{origin}-{safe}.json"


def load_history(path: Path) -> dict:
    if path.exists():
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    return {"best": None, "last_alert": None, "runs": [], "combos": {}}


def save_history(path: Path, hist: dict) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    hist["runs"] = hist["runs"][-MAX_RUNS_KEPT:]
    with path.open("w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=1)
        f.write("\n")


# ---------------------------------------------------------------------------
# 알림 판단과 메시지
# ---------------------------------------------------------------------------


def decide_alert(hist: dict, offer: Offer, alert_below: int | None, now: datetime) -> str | None:
    """알릴 이유를 돌려줘요. 알릴 필요가 없으면 None."""
    best = hist.get("best")
    last = hist.get("last_alert")

    # 인원이 바뀌어도 비교가 되도록 전부 1인 가격으로 따져요. 기준가도 1인 기준이에요.
    new_low = best is None or offer.per_person < per_person_of(best)
    under = alert_below is not None and offer.per_person <= alert_below

    if not new_low and not under:
        return None

    if last:
        cheaper_than_last = offer.per_person < per_person_of(last) * (1 - REALERT_DROP_RATIO)
        last_at = datetime.fromisoformat(last["at"])
        stale = (now - last_at) >= timedelta(days=REALERT_AFTER_DAYS)
        if not cheaper_than_last and not (under and stale):
            return None  # 비슷한 가격으로는 다시 알리지 않아요

    if new_low and best is not None:
        return "역대 최저가 갱신"
    if under:
        return "기준가 이하"
    return "첫 조회 결과"


def per_person_of(rec: dict) -> float:
    """기록(best, last_alert, combos 항목)의 1인 가격."""
    return rec["price"] / max(int(rec.get("passengers", 1)), 1)


def won(n: int, currency: str) -> str:
    return f"{n:,}원" if currency == "KRW" else f"{n:,} {currency}"


def fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.month}/{d.day}({WEEKDAY_KO[d.weekday()]})"


def naver_url(origin: str, o: Offer, adults: int) -> str:
    dep = o.depart.replace("-", "")
    ret = o.ret.replace("-", "")
    return (
        "https://flight.naver.com/flights/international/"
        f"{origin}-{o.airport}-{dep}/{o.airport}-{origin}-{ret}"
        f"?adult={adults}&fareType=Y"
    )


def skyscanner_url(origin: str, o: Offer, adults: int) -> str:
    dep = o.depart[2:].replace("-", "")
    ret = o.ret[2:].replace("-", "")
    return (
        f"https://www.skyscanner.co.kr/transport/flights/{origin.lower()}/"
        f"{o.airport.lower()}/{dep}/{ret}/?adultsv2={adults}&cabinclass=economy"
    )


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def offer_line(o: Offer, currency: str) -> str:
    stops = "직항" if o.stops == 0 else f"경유 {o.stops}회"
    air = ", ".join(o.airlines) if o.airlines else "항공사 미상"
    t = f" {o.depart_time}" if o.depart_time else ""
    if o.passengers > 1:
        price = f"<b>{won(o.price, currency)}</b> (성인 {o.passengers}명 총액, 1인 {won(o.per_person, currency)})"
    else:
        price = f"<b>{won(o.price, currency)}</b>"
    return (
        f"{fmt_date(o.depart)}{t} → {fmt_date(o.ret)} · {o.nights}박 · {o.airport}\n"
        f"💰 {price} · {esc(air)} · {stops}"
    )


def price_short(o: Offer, currency: str) -> str:
    """목록용 짧은 가격: 인원이 여럿이면 총액과 1인 가격을 같이."""
    if o.passengers > 1:
        return f"{won(o.price, currency)} (1인 {won(o.per_person, currency)})"
    return won(o.price, currency)


def links_line(origin: str, o: Offer, adults: int) -> str:
    return (
        f'🔗 <a href="{esc(o.url)}">Google Flights</a> · '
        f'<a href="{esc(naver_url(origin, o, adults))}">네이버 항공권</a> · '
        f'<a href="{esc(skyscanner_url(origin, o, adults))}">스카이스캐너</a>'
    )


def alert_message(cfg: dict, dest: dict, reason: str, offer: Offer,
                  prev_best: dict | None, top: list[Offer]) -> str:
    origin = cfg["origin"]
    cur = cfg.get("currency", "KRW")
    adults = offer.passengers
    fl = filter_label(cfg, dest)
    lines = [f"✈️ <b>{esc(dest['name'])}</b> {reason}!",
             f"{origin} 출발 왕복 · 성인 {adults}명" + (f" · {esc(fl)}" if fl else ""), ""]
    lines.append(offer_line(offer, cur))
    if prev_best:
        prev_pp = per_person_of(prev_best)
        if round(prev_pp) != offer.per_person:
            pct = (offer.per_person - prev_pp) / prev_pp * 100
            label = "1인 " if adults > 1 or int(prev_best.get("passengers", 1)) > 1 else ""
            lines.append(f"(이전 최저 {label}{won(round(prev_pp), cur)}, {pct:+.0f}%)")
    if dest.get("alert_below"):
        label = "1인 " if adults > 1 else ""
        lines.append(f"기준가 {label}{won(int(dest['alert_below']), cur)}")
    lines += ["", links_line(origin, offer, adults)]

    others = [o for o in top if o.key_ != offer.key_][:3]
    if others:
        lines += ["", "다른 날짜도 보세요:"]
        for o in others:
            lines.append(
                f'• {fmt_date(o.depart)}→{fmt_date(o.ret)} {o.airport} '
                f'<a href="{esc(o.url)}">{price_short(o, cur)}</a>'
            )
    return "\n".join(lines)


def summary_message(cfg: dict, per_dest: list[tuple[dict, list[Offer], dict]]) -> str:
    origin = cfg["origin"]
    cur = cfg.get("currency", "KRW")
    today = datetime.now(KST).strftime("%m/%d")
    lines = [f"📋 <b>항공권 최저가 요약</b> ({today}) · {origin} 출발 왕복", ""]
    for dest, offers, hist in per_dest:
        pax = dest_passengers(cfg, dest)
        fl = filter_label(cfg, dest)
        lines.append(f"<b>{esc(dest['name'])}</b>" + (f" · 성인 {pax}명" if pax > 1 else "") + (f" · {esc(fl)}" if fl else ""))
        if not offers:
            lines.append("  조회 결과 없음")
        else:
            for o in offers[:3]:
                lines.append(
                    f'  • {fmt_date(o.depart)}→{fmt_date(o.ret)} {o.airport} '
                    f'<a href="{esc(o.url)}">{price_short(o, cur)}</a>'
                )
            best = hist.get("best")
            if best:
                bp = int(best.get("passengers", 1))
                shown = won(best["price"], cur) + (f" (1인 {won(round(per_person_of(best)), cur)})" if bp > 1 else "")
                lines.append(f"  역대 최저 {shown} ({best.get('depart', '?')}~{best.get('ret', '?')})")
        lines.append("")
    return "\n".join(lines).rstrip()


# ---------------------------------------------------------------------------
# 텔레그램
# ---------------------------------------------------------------------------


def send_telegram(text: str, *, no_send: bool = False) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if no_send or not token or not chat_id:
        why = "--no-send" if no_send else "텔레그램 설정 없음"
        print(f"\n===== 보낼 메시지 ({why}) =====\n{text}\n===== 끝 =====\n")
        return False

    # 텔레그램 메시지 길이 제한(4096자)
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.loads(resp.read().decode())
    if not body.get("ok"):
        raise RuntimeError(f"텔레그램 전송 실패: {body}")
    return True


# ---------------------------------------------------------------------------
# 메인
# ---------------------------------------------------------------------------


def watch_destination(cfg: dict, dest: dict, now: datetime, fetch=fetch_cheapest,
                      quick: bool = False) -> tuple[list[Offer], dict, Path, int]:
    """여행지 하나를 조회하고 기록을 갱신해요. (가격순 결과, 기록, 기록 경로, 실패 수)"""
    origin = cfg["origin"].upper()
    today = now.date()
    interval = float(cfg.get("request_interval_seconds", 2))
    pax = dest_passengers(cfg, dest)
    airlines = airline_filter(cfg, dest)

    path = history_path(origin, dest["name"])
    hist = load_history(path)

    all_combos = build_combos(dest, today)
    if quick:
        combos = quick_combos(all_combos, hist)
    else:
        combos = rotate_combos(all_combos, int(cfg.get("max_queries_per_destination", 60)), today)

    offers: list[Offer] = []
    failures = 0
    print(f"[{dest['name']}] {len(combos)}개 조합 조회 시작 (성인 {pax}명{', 빠른 조회' if quick else ''})")
    for i, combo in enumerate(combos):
        try:
            offer = fetch(cfg, origin, combo, now, pax, airlines)
        except Exception as e:  # 조회 하나 실패해도 나머지는 계속
            failures += 1
            print(f"  ! {combo.key} 실패: {type(e).__name__}: {str(e)[:120]}")
            offer = None
        if offer:
            offers.append(offer)
            hist["combos"][combo.key] = {"price": offer.price, "passengers": pax, "at": offer.seen_at,
                                         "airlines": offer.airlines, "url": offer.url}
            print(f"  {combo.key}  {offer.price:,}  {', '.join(offer.airlines)}")
        else:
            print(f"  {combo.key}  (결과 없음)")
        if i < len(combos) - 1 and interval > 0:
            time.sleep(interval)

    # 지난 날짜 기록은 정리
    hist["combos"] = {k: v for k, v in hist["combos"].items()
                      if date.fromisoformat(k.split("|")[0]) > today}

    offers.sort(key=lambda o: o.price)
    if offers:
        hist["runs"].append({"at": now.isoformat(timespec="minutes"),
                             "min_price": offers[0].price, "passengers": pax,
                             "depart": offers[0].depart, "ret": offers[0].ret,
                             "airport": offers[0].airport, "queried": len(combos),
                             "quick": quick})
    return offers, hist, path, failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="항공권 최저가 감시기")
    ap.add_argument("--summary", action="store_true", help="조건과 상관없이 요약을 보내요")
    ap.add_argument("--quick", action="store_true", help="기록상 싼 날짜 위주로 빠르게 조회해요")
    ap.add_argument("--no-send", action="store_true", help="텔레그램으로 보내지 않아요")
    ap.add_argument("--only", help="이 이름의 여행지만 조회해요")
    ap.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    now = datetime.now(KST)
    dests = cfg["destinations"]
    if args.only:
        dests = [d for d in dests if d["name"] == args.only]
        if not dests:
            raise SystemExit(f"'{args.only}' 여행지가 config.json에 없어요.")

    per_dest: list[tuple[dict, list[Offer], dict]] = []
    total_failures = 0
    sent = 0

    for dest in dests:
        offers, hist, path, failures = watch_destination(cfg, dest, now, quick=args.quick)
        total_failures += failures
        per_dest.append((dest, offers, hist))

        if offers and not args.summary:
            top = offers[0]
            alert_below = dest.get("alert_below")
            reason = decide_alert(hist, top, int(alert_below) if alert_below else None, now)
            prev_best = hist.get("best")
            if reason:
                msg = alert_message(cfg, dest, reason, top, prev_best, offers)
                if send_telegram(msg, no_send=args.no_send):
                    sent += 1
                hist["last_alert"] = {"price": top.price, "passengers": top.passengers,
                                      "at": now.isoformat(timespec="minutes")}
            else:
                print(f"[{dest['name']}] 최저 {top.price:,} — 알림 조건 아님")

        if offers and (hist.get("best") is None or offers[0].per_person < per_person_of(hist["best"])):
            hist["best"] = asdict(offers[0])
        save_history(path, hist)

    if args.summary:
        if send_telegram(summary_message(cfg, per_dest), no_send=args.no_send):
            sent += 1

    # 설정에서 빠진 여행지나 바뀐 출발지의 기록 파일은 정리해요.
    if not args.only and HISTORY_DIR.exists():
        keep = {history_path(cfg["origin"].upper(), d["name"]) for d in cfg["destinations"]}
        for f in HISTORY_DIR.glob("*.json"):
            if f not in keep:
                f.unlink()
                print(f"오래된 기록 삭제: {f.name}")

    any_result = any(offers for _, offers, _ in per_dest)
    print(f"\n완료: 여행지 {len(dests)}곳, 알림 {sent}건, 조회 실패 {total_failures}건")
    if not any_result:
        # 전부 실패했다는 건 Google 쪽이 바뀌었거나 차단된 것. 눈에 띄게 실패 처리.
        send_telegram(
            "⚠️ 항공권 감시기가 오늘 아무 결과도 받지 못했어요. "
            "Google Flights 조회가 막혔거나 fast-flights 라이브러리를 업데이트해야 할 수 있어요. "
            "GitHub Actions 로그를 확인해 주세요.",
            no_send=args.no_send,
        )
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
