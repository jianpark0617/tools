# 사진 분석 서버 켜기

친구들이 쓰는 GitHub Pages 링크에서 "사진으로 추가"가 AI 분석까지 되게 하려면, 이 폴더의 `worker.js`를 Cloudflare Workers에 올려야 해요. 서버 없이도 앱은 돌아가지만, 그때는 사진에서 색만 읽고 종류와 모양은 직접 골라야 해요.

비용은 사진 한 장에 20~30원 정도예요(Claude Opus 5.5 기준). 친구들이 올리는 사진 값도 내 API 키에서 나가니까, 아래 1번에서 한도를 꼭 걸어 두세요.

## 1. Anthropic API 키 만들기 (5분)
1. https://console.anthropic.com 에 가입하고 결제 수단을 등록해요.
2. **Settings → Limits**에서 월 사용 한도를 정해요. 예: $5. 한도를 넘으면 분석만 멈추고 앱은 계속 돼요.
3. **API Keys → Create Key**로 키를 만들고 복사해 둬요. 이 키는 다른 곳에 붙여 넣지 마세요.

## 2. Cloudflare Worker 올리기 (5분)
1. https://dash.cloudflare.com 에 가입해요. 무료 플랜이면 돼요(하루 10만 요청).
2. **Workers & Pages → Create → Create Worker**를 누르고 이름을 정해요. 예: `fitting-room-ai`. **Deploy**를 눌러요.
3. **Edit code**를 눌러서 기존 코드를 모두 지우고 `worker.js` 내용을 붙여 넣은 뒤 **Deploy**를 눌러요.
4. Worker의 **Settings → Variables and Secrets → Add**에서
   - Type: **Secret**
   - Name: `ANTHROPIC_API_KEY`
   - Value: 1번에서 복사한 키
   를 넣고 저장해요.
5. Worker 화면 위쪽에 있는 주소를 복사해요. `https://fitting-room-ai.<내아이디>.workers.dev` 모양이에요.

## 3. 앱에 주소 넣기
`fitting-room/index.html`에서 이 줄을 찾아요.

```js
const ANALYZE_URL = '';
```

따옴표 안에 복사한 주소를 넣고 `main`에 올려요.

```js
const ANALYZE_URL = 'https://fitting-room-ai.<내아이디>.workers.dev';
```

1~2분 뒤 친구들 링크에서도 사진을 올리면 AI가 옷을 읽어요.

## 확인과 문제 해결
- 사진을 올렸는데 "AI 분석이 잠시 안 돼요"가 뜨면 Worker 화면의 **Logs**를 열어 보세요. `anthropic error 401`이면 키가 틀린 것이고, `403`이면 `worker.js`의 `ALLOWED_ORIGINS`가 앱 주소와 다른 거예요.
- 저장소 이름이나 GitHub 아이디를 바꾸면 `ALLOWED_ORIGINS`도 바꿔야 해요.
- 키를 바꾸고 싶으면 Cloudflare의 Secret 값만 바꾸면 돼요. 앱은 손댈 게 없어요.
