const CACHE_NAME = 'mh-score-v4';
const PRECACHE = ['./', './manifest.json'];
self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE_NAME).then(c => c.addAll(PRECACHE)));
  self.skipWaiting();
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(
    ks.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))
  )));
  self.clients.claim();
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  // API calls: network-first
  if (url.hostname.includes('yahoo') || url.hostname.includes('fred') ||
      url.hostname.includes('githubusercontent') || url.hostname.includes('cboe') ||
      url.hostname.includes('cnn')) {
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    return;
  }
  // App shell: cache-first
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request).then(res => {
    const clone = res.clone();
    caches.open(CACHE_NAME).then(c => c.put(e.request, clone));
    return res;
  })));
});
