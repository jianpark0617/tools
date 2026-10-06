// 아침 피팅룸 사진 분석 서버 (Cloudflare Worker)
//
// 앱(GitHub Pages)이 옷 사진을 보내면 Claude에게 보여 주고, 옷의 종류·모양·색을 JSON으로 돌려줘요.
// API 키는 이 서버에만 있어서 앱 코드에 드러나지 않아요. 배포 방법은 README.md에 있어요.
//
// 이 파일은 Cloudflare 대시보드에 그대로 붙여 넣는 한 파일짜리 서버라서 SDK 없이 HTTP로 직접 호출해요.

const MODEL = 'claude-opus-5-5';
// 이 주소에서 열린 앱만 받아 줘요. 저장소 이름을 바꾸면 여기도 바꿔야 해요.
const ALLOWED_ORIGINS = ['https://jianpark0617.github.io'];
const MAX_IMAGE_CHARS = 4_000_000; // base64 기준 약 3MB

// index.html의 SHAPE_INFO와 같은 목록이어야 해요.
const SHAPES = {
  tee: '반팔 티셔츠', long: '긴팔 티셔츠', shirt: '셔츠·블라우스', knit: '니트·스웨터', polo: '카라 니트·폴로', hoodie: '후드티·맨투맨', dress: '원피스',
  jeans: '청바지', slacks: '긴바지·슬랙스', shorts: '반바지', skirt: '치마',
  coat: '코트', jacket: '자켓', blazer: '블레이저', cardigan: '가디건', puffer: '패딩', bomber: '바시티·블루종',
  sneaker: '운동화', loafer: '로퍼·구두', boots: '부츠', sandal: '샌들·슬리퍼',
};

const PROMPT = `사진에 찍힌 옷(또는 신발) 한 가지를 보고 JSON 객체 하나로만 답하세요.
- category: "top"(상의, 원피스 포함) | "bottom"(하의) | "outer"(아우터) | "shoes"(신발). 옷이 아니면 "none".
- shape: 가장 가까운 것 하나. ${Object.entries(SHAPES).map(([k, v]) => `${k}=${v}`).join(', ')}
- name: 한국어로 짧은 이름, 색 포함 (예: "회색 후드티", "연청 와이드 데님")
- color: 옷감의 주된 색을 #rrggbb로. 조명 때문에 밝거나 어둡게 보여도 실제 옷감 색으로 추정.
- pattern: "solid" | "stripe"(가는 줄) | "block"(굵은 블록 줄) | "check" | "dots". 작은 로고나 프린트는 solid.
- color2: 무늬의 두 번째 색 #rrggbb. 무늬가 없으면 null.
- warm: 두께감. 1=얇음(여름용) 2=보통 3=두꺼움(기모, 니트, 겨울용)
- form: 1=편한·캐주얼 2=무난 3=단정·격식
- confidence: 0~1
- note: 사용자에게 보여줄 한 문장 (예: "오버핏 회색 후드티로 보여요.")`;

const SCHEMA = {
  type: 'object',
  properties: {
    category: { type: 'string', enum: ['top', 'bottom', 'outer', 'shoes', 'none'] },
    shape: { type: 'string', enum: Object.keys(SHAPES) },
    name: { type: 'string' },
    color: { type: 'string' },
    pattern: { type: 'string', enum: ['solid', 'stripe', 'block', 'check', 'dots'] },
    color2: { type: ['string', 'null'] },
    warm: { type: 'integer' },
    form: { type: 'integer' },
    confidence: { type: 'number' },
    note: { type: 'string' },
  },
  required: ['category', 'shape', 'name', 'color', 'pattern', 'color2', 'warm', 'form', 'confidence', 'note'],
  additionalProperties: false,
};

const json = (data, status, headers) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', ...headers } });

export default {
  async fetch(request, env) {
    const origin = request.headers.get('Origin') || '';
    const allowed = ALLOWED_ORIGINS.includes(origin);
    const cors = {
      'Access-Control-Allow-Origin': allowed ? origin : ALLOWED_ORIGINS[0],
      'Access-Control-Allow-Methods': 'POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
      'Vary': 'Origin',
    };
    if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors });
    if (request.method !== 'POST') return json({ error: 'POST로 보내 주세요.' }, 405, cors);
    if (!allowed) return json({ error: '허용되지 않은 주소에서 온 요청이에요.' }, 403, cors);
    if (!env.ANTHROPIC_API_KEY) return json({ error: 'ANTHROPIC_API_KEY가 설정되지 않았어요.' }, 500, cors);

    let body;
    try { body = await request.json(); } catch (e) { return json({ error: 'JSON 본문이 필요해요.' }, 400, cors); }
    const { image, media_type } = body || {};
    if (typeof image !== 'string' || image.length > MAX_IMAGE_CHARS || !['image/jpeg', 'image/png', 'image/webp'].includes(media_type)) {
      return json({ error: 'image(base64)와 media_type(jpeg/png/webp)이 필요해요.' }, 400, cors);
    }

    const res = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': env.ANTHROPIC_API_KEY,
        'anthropic-version': '2023-06-01',
        'anthropic-beta': 'server-side-fallback-2026-07-01',
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: 1024,
        fallbacks: 'default',
        output_config: { effort: 'low', format: { type: 'json_schema', schema: SCHEMA } },
        messages: [{
          role: 'user',
          content: [
            { type: 'image', source: { type: 'base64', media_type, data: image } },
            { type: 'text', text: PROMPT },
          ],
        }],
      }),
    });
    if (!res.ok) {
      const detail = await res.text().catch(() => '');
      console.log('anthropic error', res.status, detail.slice(0, 300));
      return json({ error: `분석 서버 오류 (${res.status})` }, 502, cors);
    }
    const msg = await res.json();
    if (msg.stop_reason === 'refusal') return json({ category: 'none', note: '이 사진은 분석하지 않았어요. 옷만 나오게 다시 찍어 주세요.' }, 200, cors);
    const text = (msg.content || []).filter(b => b.type === 'text').map(b => b.text).join('');
    try { return json(JSON.parse(text), 200, cors); }
    catch (e) { return json({ error: '답을 읽지 못했어요.' }, 502, cors); }
  },
};
