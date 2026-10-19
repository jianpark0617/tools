# ⚡ 텔레그램 명령 바로 처리하기 (Cloudflare Worker 중계기)

봇 명령은 원래 GitHub Actions가 10분마다 확인하도록 되어 있지만, GitHub의 예약 실행은 붐비면 **몇 시간씩** 밀려요(실제로 2시간 간격으로 돈 적이 있어요). 이 중계기를 켜 두면 메시지가 온 뒤 **1~2분 안에** 처리돼요.

원리: Cloudflare Worker(무료)가 1분마다 텔레그램에 새 메시지가 있는지만 보고, 있으면 GitHub에 "봇 워크플로 지금 돌려"라고 신호를 보내요. 메시지를 읽고 설정을 바꾸는 건 여전히 GitHub Actions의 봇이 해요. Worker가 멈춰도 10분 예약 실행이 안전망으로 남아요.

선택 사항이에요. 안 켜도 봇은 느리게나마 돌아가요.

## 자동 설치 (추천, 5분)

Cloudflare 화면을 돌아다닐 필요 없이, GitHub Actions가 대신 설치해요. 토큰 두 개만 만들면 돼요.

### 1. Cloudflare API 토큰

1. https://dash.cloudflare.com/profile/api-tokens 를 열어요 (로그인 필요).
2. **Create Token** → 목록에서 **Edit Cloudflare Workers** 오른쪽의 **Use template**.
3. 아무것도 바꾸지 말고 아래 **Continue to summary** → **Create Token**.
4. 나오는 긴 문자열을 복사해 둬요. 이 화면을 벗어나면 다시 못 봐요.

### 2. GitHub 토큰

1. https://github.com/settings/personal-access-tokens/new 를 열어요.
2. **Token name**: `flight-watch-relay`. **Expiration**: 가능한 한 길게.
3. **Repository access**: *Only select repositories* → `tools` 선택.
4. **Permissions → Repository permissions**에서 **Actions**를 찾아 *Read and write*. 다른 건 그대로.
5. 맨 아래 **Generate token** → `github_pat_...` 복사.

### 3. 저장소 Secrets에 넣기

https://github.com/jianpark0617/tools/settings/secrets/actions (Fork면 자기 저장소) → **New repository secret** 두 번:

| Name | Secret |
|---|---|
| `CLOUDFLARE_API_TOKEN` | 1번 토큰 |
| `RELAY_GITHUB_TOKEN` | 2번 토큰 |

(`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`는 이미 있어요.)

### 4. 설치 실행

**Actions → "중계기 설치 (Cloudflare Worker)" → Run workflow**. 1~2분 뒤 초록색이면 끝이에요. 텔레그램 봇에게 `/list`를 보내면 1~2분 안에 답장이 와요.

빨간색이면 실패한 단계의 로그에 한글로 원인이 적혀 있어요. 토큰을 바꿔 넣었으면 다시 Run workflow 하면 돼요. 여러 번 돌려도 괜찮아요.

---

## 수동 설치 (Cloudflare 화면에서 직접)

자동 설치가 안 될 때만 보세요.

### 1. GitHub 토큰 만들기 (3분)

Worker가 GitHub에 "워크플로 돌려"라고 말하려면 열쇠가 필요해요.

1. https://github.com/settings/personal-access-tokens/new 를 열어요.
2. **Token name**: `flight-watch-relay`
3. **Expiration**: 가능한 한 길게. 만료되면 여기부터 다시 하고 Worker의 `GITHUB_TOKEN`만 바꾸면 돼요.
4. **Repository access**: *Only select repositories* → `tools` 선택
5. **Permissions → Repository permissions → Actions**: *Read and write*. 다른 권한은 건드리지 않아요.
6. **Generate token**을 누르고 나오는 `github_pat_...` 문자열을 복사해 둬요. 이 화면을 벗어나면 다시 못 봐요.

### 2. Worker 만들기 (5분)

1. https://dash.cloudflare.com 에 로그인해요(없으면 무료 가입).
2. **Workers & Pages → Create → Create Worker**. 이름은 `flight-watch-relay`. **Deploy**.
3. **Edit code**를 눌러 기존 코드를 전부 지우고, 이 폴더의 `worker.js` 내용을 붙여 넣은 뒤 **Deploy**.

### 3. 저장 공간(KV) 연결

"마지막으로 깨운 메시지 번호"를 기억하는 데 써요.

1. 왼쪽 메뉴 **Storage & Databases → KV → Create**. 이름 `flight-watch-state`.
2. Worker로 돌아와 **Settings → Bindings → Add → KV namespace**.
   - Variable name: `STATE`
   - KV namespace: `flight-watch-state`
3. **Deploy**(또는 Save).

### 4. 비밀값 넣기

Worker **Settings → Variables and Secrets → Add**에서 하나씩:

| Type | Name | Value |
|---|---|---|
| Secret | `TELEGRAM_BOT_TOKEN` | 봇 토큰 (GitHub Secrets에 넣은 것과 같은 값) |
| Secret | `GITHUB_TOKEN` | 1번에서 만든 `github_pat_...` |
| Text | `GITHUB_REPO` | `jianpark0617/tools` (Fork면 `내아이디/tools`) |
| Text | `TELEGRAM_CHAT_ID` | 내 채팅 ID (선택. 넣으면 내 메시지일 때만 깨워요) |
| Secret | `CHECK_KEY` | 아무 긴 문자열 (선택. 점검용 주소를 쓰려면) |

넣은 뒤 **Deploy**.

### 5. 1분마다 돌게 하기

Worker **Settings → Triggers → Cron Triggers → Add**: `* * * * *` 입력 후 저장.

### 6. 확인

텔레그램 봇에게 `/list`를 보내고 1~2분 기다려요. 답장이 오면 끝이에요. GitHub 저장소 **Actions** 탭에 "항공권 감시 봇" 실행이 새로 생긴 것도 볼 수 있어요.

`CHECK_KEY`를 넣었다면 브라우저에서 `https://flight-watch-relay.<내아이디>.workers.dev/check?key=<CHECK_KEY>`를 열어 상태를 볼 수 있어요. `{"ok":true,"pending":0}`이면 정상이고 `error`가 있으면 그 내용이 원인이에요.

## 문제 해결

- `GitHub 401/403`: 토큰이 틀렸거나 Actions 권한이 없어요. 1번을 다시 해요.
- `GitHub 404`: `GITHUB_REPO`가 틀렸거나 토큰이 그 저장소에 접근 권한이 없어요.
- `설정이 없어요`: 4번의 이름 철자를 확인해요.
- `KV 바인딩(STATE)이 없어요`: 3번에서 Variable name을 정확히 `STATE`로.
- 텔레그램 오류 409: 봇이 그 순간 메시지를 읽는 중이라 난 거예요. 다음 분에 자동으로 다시 해요.

## 비용

Cloudflare 무료 플랜 기준 하루 1,440번 실행, KV 읽기 2,880번, 쓰기는 메시지가 올 때만이라 전부 무료 한도 안이에요. GitHub Actions도 공개 저장소는 무료예요.
