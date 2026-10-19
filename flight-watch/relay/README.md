# ⚡ 텔레그램 명령 바로바로 처리하기 (Cloudflare Worker)

이걸 켜면 봇이 **1~2초 안에** 답장해요. 텔레그램이 메시지를 Cloudflare Worker(무료)로 바로 밀어 넣고(웹훅), Worker가 명령을 해석해 저장소의 `config.json`을 직접 고치고 답장해요. 4분 걸리는 가격 조회(`/summary`, 매일 아침)만 GitHub Actions가 해요.

안 켜면 GitHub Actions가 10분마다(실제로는 몇 시간씩 밀릴 수 있음) 메시지를 확인하는 느린 방식으로 돌아가요.

## 자동 설치 (5분)

Cloudflare 화면을 돌아다닐 필요 없이 GitHub Actions가 대신 설치해요. 토큰 두 개만 만들면 돼요.

### 1. Cloudflare API 토큰

1. https://dash.cloudflare.com/profile/api-tokens → **Create Token**
2. **Edit Cloudflare Workers** 줄의 **Use template**
3. **Account Resources**: 두 번째 칸에서 내 계정 선택. **Zone Resources**: 가운데를 **All zones**로.
4. **Continue to summary** → **Create Token** → 회색 상자 옆 **Copy** (아래 `curl` 예시는 복사하지 마세요)

### 2. GitHub 토큰

1. https://github.com/settings/personal-access-tokens/new
2. **Token name**: `flight-watch-relay`. **Expiration**: 가능한 한 길게.
3. **Repository access**: *Only select repositories* → `tools`
4. **Repository permissions**에서 두 가지:
   - **Actions**: *Read and write* (가격 조회 워크플로를 깨우려고)
   - **Contents**: *Read and write* (설정 파일을 고치려고)
5. **Generate token** → `github_pat_...` 복사

### 3. 저장소 Secrets

https://github.com/jianpark0617/tools/settings/secrets/actions (Fork면 자기 저장소) → **New repository secret**:

| Name | Secret |
|---|---|
| `CLOUDFLARE_API_TOKEN` | 1번 토큰 |
| `RELAY_GITHUB_TOKEN` | 2번 토큰 |

(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`는 이미 있어야 해요.)

### 4. 설치 실행

**Actions → "중계기 설치 (Cloudflare Worker)" → Run workflow** (mode: `install`). 초록색이면 끝. 텔레그램 봇에게 `/list`를 보내면 1~2초 안에 답장이 와요.

빨간색이면 실패한 단계의 로그에 한글로 원인이 적혀 있어요. 고치고 다시 Run workflow 하면 돼요. 여러 번 돌려도 안전해요. 왜 안 되는지 모르겠으면 mode를 `diagnose`로 돌려 보세요.

## 어떻게 돌아가나

- Worker 주소 `https://flight-watch-relay.<내subdomain>.workers.dev/telegram`이 텔레그램 웹훅으로 등록돼요. 설치 때 만든 비밀 문자열이 맞는 요청만 받아요.
- Worker가 GitHub API로 `flight-watch/config.json`을 읽고, 바꾸고, 커밋해요. 커밋 작성자는 `flight-watch-relay`예요.
- `/summary`는 GitHub Actions의 "항공권 최저가 감시"를 `summary` 모드로 깨워요.
- `/reset`은 `flight-watch/history/` 파일을 지워요.
- 되묻기(인자 없는 명령) 상태는 Cloudflare KV에 1시간 보관돼요.
- 웹훅이 켜져 있으면 GitHub Actions의 봇(`bot.py`)은 "웹훅 모드"라고만 찍고 아무것도 안 해요. 웹훅을 끄면(텔레그램 `deleteWebhook`) 다시 폴링 모드로 돌아가요.

## 토큰이 만료되면

GitHub 토큰을 새로 만들어 `RELAY_GITHUB_TOKEN`을 바꾸고 설치 워크플로를 다시 돌리면 돼요.

## 문제 해결

- `RELAY_GITHUB_TOKEN에 Contents 권한이 없어요`: 2번에서 Contents를 빠뜨린 거예요. https://github.com/settings/personal-access-tokens 에서 토큰을 열어 권한을 추가하고 다시 실행.
- `GitHub 401/403/404`: 토큰이 틀렸거나 저장소 선택이 빠졌어요.
- `workers.dev 주소를 만들지 못했어요`: Cloudflare 대시보드 → Workers & Pages 를 한 번 열면 주소를 정하라고 나와요. 정한 뒤 다시 실행.
- 답장이 안 오면 `diagnose`로 돌려서 "웹훅 상태"의 `last_error`를 보세요. Cloudflare 대시보드 → Workers → flight-watch-relay → **Logs**에도 실행 기록이 남아요.
- 봇이 "⚠️ 처리 중 문제가 생겼어요: GitHub 409"라고 답하면 같은 순간에 다른 커밋이 있었던 거예요. 다시 보내면 돼요.

## 비용

Cloudflare 무료 플랜(하루 10만 요청), KV 무료 한도, 공개 저장소의 GitHub Actions 모두 무료예요.
