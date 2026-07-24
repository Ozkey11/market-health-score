/* Market Health Score — Service Worker v6
   重要な修正(2026-07): 旧v5では CORSプロキシ経由の株価取得(api.allorigins.win 等)が
   ホスト名に 'yahoo' を含まないため「アプリシェル用のcache-first」分岐に落ち、
   一度キャッシュされた株価JSONが永久に返り続けていた（＝数日前の株価が更新されない原因）。
   本版では「同一オリジンのみキャッシュ、クロスオリジンは常にネットワーク」に変更する。 */
const CACHE_NAME = 'mh-score-v6';
const PRECACHE = ['./', './manifest.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(
    ks.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
  )).then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);

  // ── クロスオリジン(株価API・プロキシ・FRED等)は一切キャッシュしない ──
  if (url.origin !== self.location.origin) {
    e.respondWith(fetch(req).catch(() => caches.match(req)));
    return;
  }

  // ── 同一オリジンのデータJSONはネットワーク優先(オフライン時のみキャッシュ) ──
  if (url.pathname.includes('/data/')) {
    e.respondWith(
      fetch(req).then(res => {
        const clone = res.clone();
        caches.open(CACHE_NAME).then(c => c.put(req, clone));
        return res;
      }).catch(() => caches.match(req))
    );
    return;
  }

  // ── アプリシェルはcache-first + 背後で更新(stale-while-revalidate) ──
  e.respondWith(caches.match(req).then(hit => {
    const net = fetch(req).then(res => {
      const clone = res.clone();
      caches.open(CACHE_NAME).then(c => c.put(req, clone));
      return res;
    }).catch(() => hit);
    return hit || net;
  }));
});
