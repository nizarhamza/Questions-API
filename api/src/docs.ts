import { CATEGORIES, GENERATED, PATTERNS, TOTAL } from "./bank";
import { html } from "./http";

export function docsPage(url: URL): Response {
  const base = url.origin;
  const catRows = CATEGORIES.map(
    (c) => `<tr><td><code>${c.slug}</code></td><td><code>${c.id}</code></td><td>${c.name}</td>
      <td class="n">${c.counts.easy}</td><td class="n">${c.counts.medium}</td><td class="n">${c.counts.hard}</td>
      <td class="n"><b>${c.counts.total}</b></td></tr>`,
  ).join("");

  return html(`<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Questions API</title>
<style>
  :root{--bg:#fbfbfa;--fg:#1a1a18;--muted:#6b6b66;--line:#e3e2dd;--card:#fff;--accent:#b0552b;--code:#f2f1ed}
  @media (prefers-color-scheme:dark){:root{--bg:#151513;--fg:#e8e7e3;--muted:#9a998f;--line:#2c2c28;--card:#1c1c19;--accent:#e2915f;--code:#232320}}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
  .wrap{max-width:860px;margin:0 auto;padding:48px 24px 96px}
  h1{font-size:32px;margin:0 0 4px;letter-spacing:-.02em}
  h2{font-size:20px;margin:44px 0 12px;letter-spacing:-.01em;border-bottom:1px solid var(--line);padding-bottom:8px}
  h3{font-size:15px;margin:26px 0 6px}
  p{color:var(--muted);margin:0 0 14px}
  .lede{font-size:17px;color:var(--muted);margin-bottom:22px}
  code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:.9em;background:var(--code);padding:1px 5px;border-radius:4px}
  pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;overflow-x:auto;margin:0 0 14px}
  pre code{background:none;padding:0;font-size:13px;line-height:1.55}
  table{border-collapse:collapse;width:100%;font-size:14px;margin:0 0 16px;display:block;overflow-x:auto}
  th,td{text-align:left;padding:7px 12px 7px 0;border-bottom:1px solid var(--line);white-space:nowrap}
  th{font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
  td.n,th.n{text-align:right;padding-right:16px}
  .ep{display:flex;gap:10px;align-items:baseline;padding:9px 0;border-bottom:1px solid var(--line);flex-wrap:wrap}
  .m{flex:0 0 auto;width:42px;text-align:center;font:600 11px/1 ui-monospace,monospace;padding:5px 0;border-radius:4px;background:var(--code);color:var(--accent);letter-spacing:.04em}
  .ep code{background:none;padding:0;font-weight:500}
  .ep span:not(.m){color:var(--muted);font-size:13.5px;flex:1;min-width:220px}
  .stat{display:flex;gap:28px;flex-wrap:wrap;margin:0 0 26px;padding:16px 18px;background:var(--card);border:1px solid var(--line);border-radius:8px}
  .stat div{display:flex;flex-direction:column}
  .stat b{font-size:22px;letter-spacing:-.02em}
  .stat small{color:var(--muted);font-size:11.5px;text-transform:uppercase;letter-spacing:.06em}
  button{font:inherit;font-size:13px;padding:7px 14px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
  button:hover{border-color:var(--accent);color:var(--accent)}
  #out{max-height:320px;overflow:auto;white-space:pre-wrap;word-break:break-word}
  .try{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
  footer{margin-top:56px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:13px}
  a{color:var(--accent)}
</style></head><body><div class="wrap">

<h1>Questions API</h1>
<p class="lede">A trivia API over a question bank derived from Wikidata facts. No model recalled any of these answers &mdash; each one was read out of a structured database and poured into a template, so the error rate is near zero.</p>

<div class="stat">
  <div><b>${TOTAL.toLocaleString("en-US")}</b><small>questions</small></div>
  <div><b>${CATEGORIES.length}</b><small>categories</small></div>
  <div><b>${PATTERNS.length}</b><small>patterns</small></div>
  <div><b>CC0</b><small>license</small></div>
</div>

<h2>Try it</h2>
<div class="try">
  <button onclick="run('/v1/questions?amount=2&amp;category=geography')">2 geography</button>
  <button onclick="run('/v1/questions?amount=1&amp;difficulty=hard&amp;reveal=false')">1 sealed, hard</button>
  <button onclick="run('/api.php?amount=2&amp;category=23')">OpenTDB shape</button>
  <button onclick="run('/v1/stats')">stats</button>
</div>
<pre id="out"><code>Pick one above.</code></pre>
<script>
  async function run(path){
    const out=document.getElementById('out').firstChild;
    out.textContent='GET '+path+'\\n\\nloading…';
    try{const r=await fetch(path);out.textContent='GET '+path+'  →  '+r.status+'\\n\\n'+await r.text();}
    catch(e){out.textContent='GET '+path+'\\n\\n'+e;}
  }
</script>

<h2>Native API</h2>
<div class="ep"><span class="m">GET</span><code>/v1/questions</code><span>Draw questions. <code>amount</code> 1&ndash;100, <code>category</code>, <code>difficulty</code>, <code>pattern</code>, <code>token</code>, <code>seed</code>, <code>reveal=false</code>.</span></div>
<div class="ep"><span class="m">GET</span><code>/v1/questions/{id}</code><span>One question by id, with its answer and provenance.</span></div>
<div class="ep"><span class="m">POST</span><code>/v1/check</code><span>Score an answer, singly or a whole quiz at once.</span></div>
<div class="ep"><span class="m">GET</span><code>/v1/categories</code><span>Slugs, OpenTDB numeric ids, and per-difficulty counts.</span></div>
<div class="ep"><span class="m">GET</span><code>/v1/patterns</code><span>The ten question patterns and how many of each exist.</span></div>
<div class="ep"><span class="m">GET</span><code>/v1/stats</code><span>Bank totals, generation date, source.</span></div>
<div class="ep"><span class="m">POST</span><code>/v1/tokens</code><span>Mint a session token.</span></div>
<div class="ep"><span class="m">GET</span><code>/v1/tokens/{token}</code><span>How much of the bank that token has burned through.</span></div>
<div class="ep"><span class="m">POST</span><code>/v1/tokens/{token}/reset</code><span>Forget everything it has seen.</span></div>

<h3>Sealed questions</h3>
<p>With <code>reveal=false</code> the answer never leaves the server. Options come back in a per-request order alongside a signed <code>deal</code>; <code>/v1/check</code> scores against that exact order, so the client cannot read the answer off the wire or forge a position.</p>
<pre><code>curl -s '${base}/v1/questions?amount=1&amp;reveal=false'
curl -s '${base}/v1/check' -H 'content-type: application/json' \\
  -d '{"deal":"…","answer":2}'</code></pre>

<h3>Session tokens</h3>
<p>A token remembers every question it has been shown, so a player never sees a repeat until the pool runs dry. When it does, the API answers <code>409 token_exhausted</code>. Tokens expire after six idle hours.</p>
<pre><code>TOKEN=$(curl -s -X POST '${base}/v1/tokens' | jq -r .token)
curl -s "${base}/v1/questions?amount=10&amp;token=$TOKEN"</code></pre>

<h2>OpenTDB-compatible API</h2>
<p>Point an existing Open Trivia DB client at this host and it works unchanged &mdash; same paths, same parameter names, same <code>response_code</code> vocabulary, same numeric category ids.</p>
<div class="ep"><span class="m">GET</span><code>/api.php</code><span><code>amount</code>, <code>category</code>, <code>difficulty</code>, <code>type</code>, <code>encode</code>, <code>token</code>. Add <code>extended=1</code> for ids and explanations.</span></div>
<div class="ep"><span class="m">GET</span><code>/api_token.php</code><span><code>command=request</code> or <code>command=reset&amp;token=…</code></span></div>
<div class="ep"><span class="m">GET</span><code>/api_category.php</code><span>Category list in OpenTDB's shape.</span></div>
<div class="ep"><span class="m">GET</span><code>/api_count.php</code><span>Counts for one category.</span></div>
<div class="ep"><span class="m">GET</span><code>/api_count_global.php</code><span>Counts for everything.</span></div>
<p>Response codes: <code>0</code> success, <code>1</code> no results, <code>2</code> invalid parameter, <code>3</code> token not found, <code>4</code> token empty, <code>5</code> rate limited. Encodings: default HTML entities, <code>url3986</code>, <code>base64</code>, <code>legacy</code>. Every question here is four-option multiple choice, so <code>type=boolean</code> returns code <code>1</code>.</p>

<h2>Categories</h2>
<table><thead><tr><th>slug</th><th>id</th><th>name</th><th class="n">easy</th><th class="n">medium</th><th class="n">hard</th><th class="n">total</th></tr></thead><tbody>${catRows}</tbody></table>

<h2>Limits</h2>
<p>120 requests per minute per IP, and 12 token mints per minute. Reads of static data (categories, patterns, stats, single questions) are edge-cached; draws never are. CORS is open to every origin.</p>

<footer>
  Bank generated ${GENERATED} from Wikidata, which is CC0 &mdash; no attribution required, though it is polite.
  API code MIT.
</footer>
</div></body></html>`);
}
