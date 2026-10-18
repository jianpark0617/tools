# ✈️ 항공권 최저가 감시기 (`flight-watch/`)

여행지를 적어 두면 **매일 아침 자동으로** Google Flights 최저가를 찾아보고, 기준가 아래로 내려가거나 역대 최저가가 갱신되면 **텔레그램**으로 알려 줘요. 메시지에는 Google Flights · 네이버 항공권 · 스카이스캐너 링크가 같이 붙어서 바로 예매 화면으로 갈 수 있어요.

서버나 컴퓨터를 켜 둘 필요가 없어요. GitHub Actions가 매일 대신 돌려 줘요(무료).

## 처음 한 번만 하는 설정 (5분)

### 1. 텔레그램 봇 만들기

1. 텔레그램에서 **@BotFather**를 찾아 대화 시작 → `/newbot` 입력.
2. 봇 이름(아무거나)과 아이디(`…bot`으로 끝나야 함)를 정하면 **토큰**을 줘요. `123456789:AAH...` 같은 긴 문자열이에요. 복사해 두세요.
3. 방금 만든 봇을 찾아서 **아무 메시지나 하나 보내세요** (봇은 먼저 말을 걸 수 없어서 이게 꼭 필요해요).
4. 브라우저 주소창에 아래를 붙여넣어 열어요 (`<토큰>` 자리에 토큰):
   ```
   https://api.telegram.org/bot<토큰>/getUpdates
   ```
   나오는 글 안에서 `"chat":{"id":123456789` 부분의 숫자가 **채팅 ID**예요.

### 2. GitHub 저장소에 저장

저장소 페이지 → **Settings → Secrets and variables → Actions → New repository secret**에서 두 개 추가:

| 이름 | 값 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 1번에서 받은 토큰 |
| `TELEGRAM_CHAT_ID` | 1번에서 찾은 채팅 ID |

### 3. 한 번 돌려 보기

저장소 → **Actions → "항공권 최저가 감시" → Run workflow**. `mode`를 `summary`로 고르면 조건과 상관없이 지금 최저가 요약이 텔레그램으로 와요. 이게 오면 설정 끝.

> Secrets를 아직 안 넣었으면 텔레그램 대신 Actions 로그에 메시지가 찍혀요. 깨지지는 않아요.

## 여행지 바꾸기

`flight-watch/config.json` 한 파일만 고치면 돼요. 저장하고 `main`에 올리면 다음 실행부터 반영.

```json
{
  "origin": "ICN",            ← 출발 공항 (김포 GMP, 부산 PUS …)
  "currency": "KRW",
  "passengers": 1,            ← 성인 인원
  "max_stops": 0,             ← 0=직항만, 1=경유 1회까지, null=상관없음
  "max_queries_per_destination": 60,
  "destinations": [
    {
      "name": "도쿄",                     ← 알림에 표시될 이름
      "airports": ["NRT", "HND"],         ← 도착 공항. 여러 개면 각각 조회
      "depart_from": "2026-12-01",        ← 출발 가능한 기간
      "depart_to": "2027-02-28",
      "depart_weekdays": ["fri", "sat"],  ← 출발 요일 (빼면 매일)
      "nights": [3, 4],                   ← 몇 박
      "alert_below": 300000               ← 이 가격 이하면 알림 (빼도 됨)
    }
  ]
}
```

날짜를 딱 정해 둔 경우에는 기간 대신 `dates`를 쓰면 돼요:

```json
{ "name": "방콕", "airports": ["BKK"], "dates": [["2026-12-24", "2026-12-30"]], "alert_below": 450000 }
```

여행지를 지우면 감시도 멈춰요. 공항 코드는 [IATA 코드](https://www.google.com/search?q=IATA+공항+코드) 3글자를 써요 (오사카 KIX, 후쿠오카 FUK, 다낭 DAD, 타이베이 TPE, 홍콩 HKG, 싱가포르 SIN, 파리 CDG …).

## 언제 알림이 오나요

- **역대 최저가 갱신**: 지금까지 본 가격보다 싸졌을 때.
- **기준가 이하**: `alert_below`보다 싸거나 같을 때.
- 같은 수준의 가격으로는 다시 알리지 않아요. 마지막 알림보다 **3% 넘게** 더 싸지거나, 기준가 아래인 채로 **7일**이 지나면 한 번 더 알려요.
- 처음 돌릴 때는 비교 기준이 없어서 "첫 조회 결과"로 한 번 와요.
- 하루 종일 아무 결과도 못 받으면 ⚠️ 경고가 와요 (Google 쪽이 바뀌었을 가능성).

## 조회 범위와 횟수

조합 수 = 출발 가능한 날 × 박수 × 도착 공항 수. 예시 설정의 도쿄는 약 100개인데, `max_queries_per_destination`(기본 60)을 넘으면 **매일 다른 60개를 돌아가며** 조회해요. 이틀이면 전체를 한 바퀴 돌아요. 조회 사이에 2초씩 쉬어서 여행지 하나에 2~3분쯤 걸려요.

## 기록

조회 결과는 `flight-watch/history/<출발>-<여행지>.json`에 자동으로 커밋돼요. 날짜 조합별 마지막 가격, 날마다의 최저가, 역대 최저가가 들어 있어요. 나중에 그래프를 그리거나 추세를 보는 데 쓸 수 있어요.

## 내 컴퓨터에서 돌리기

```bash
pip install -r flight-watch/requirements.txt
python flight-watch/watch.py --no-send          # 보내지 않고 화면에만
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... python flight-watch/watch.py --summary
```

## 알아 둘 점

- 가격은 **Google Flights**에서 가져와요. 공식 API가 아니라 [`fast-flights`](https://github.com/AWeirdDev/flights) 라이브러리로 읽는 거라, Google이 화면 구조를 바꾸면 조회가 안 될 수 있어요. 그러면 ⚠️ 경고가 오고 Actions가 빨간색이 돼요. 보통 `flight-watch/requirements.txt`의 버전을 올리면 해결돼요.
- 표시 가격은 Google 기준이라 실제 예매 사이트와 몇천 원 차이 날 수 있어요. 링크를 눌러 최종 가격을 확인하세요.
- 카카오톡 알림도 기술적으로는 가능하지만, 카카오 개발자 등록 후 토큰을 2개월마다 직접 갱신해야 해서 자동화가 자주 끊겨요. 그래서 텔레그램을 골랐어요.
