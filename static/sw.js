// Service Worker - Offline Mode
const CACHE_NAME = 'prufungskalender-v1';
const urlsToCache = [
  '/',
  '/index.html',
  '/add.html',
  '/delete.html',
  '/offline.html',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css',
  'https://cdn.jsdelivr.net/npm/fullcalendar@6.1.10/index.global.min.css',
  'https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.1/font/bootstrap-icons.css',
  'https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/js/bootstrap.bundle.min.js',
  'https://cdn.jsdelivr.net/npm/fullcalendar@6.1.10/index.global.min.js',
];

// Installation event
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      // Críticas dosyaları cache'e ekle
      return cache.addAll([
        '/',
        '/index.html',
        '/offline.html'
      ]).catch(() => {
        // Hata olsa bile devam et
        return Promise.resolve();
      });
    })
  );
  self.skipWaiting();
});

// Activation event
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheName !== CACHE_NAME) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
  self.clients.claim();
});

// Fetch event - Offline fallback
self.addEventListener('fetch', event => {
  const { request } = event;
  const url = new URL(request.url);

  // GET requests sadece
  if (request.method !== 'GET') {
    return;
  }

  // API endpoints - Cache first, network fallback
  if (url.pathname === '/events' || url.pathname === '/api/subjects') {
    event.respondWith(
      caches.match(request)
        .then(response => {
          if (response) return response;
          return fetch(request).then(response => {
            if (!response || response.status !== 200) {
              return response;
            }
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then(cache => {
              cache.put(request, responseClone);
            });
            return response;
          });
        })
        .catch(() => {
          // İnternet yok, cache'deki veri var ise kullan
          return caches.match(request)
            .then(response => response || caches.match('/offline.html'));
        })
    );
    return;
  }

  // HTML sayfaları - Network first, cache fallback
  if (request.headers.get('accept').includes('text/html')) {
    event.respondWith(
      fetch(request)
        .then(response => {
          if (!response || response.status !== 200) {
            return response;
          }
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then(cache => {
            cache.put(request, responseClone);
          });
          return response;
        })
        .catch(() => {
          return caches.match(request)
            .then(response => response || caches.match('/offline.html'));
        })
    );
    return;
  }

  // Diğer kaynaklar - Cache first
  event.respondWith(
    caches.match(request)
      .then(response => {
        if (response) return response;
        return fetch(request)
          .then(response => {
            if (!response || response.status !== 200) {
              return response;
            }
            const responseClone = response.clone();
            caches.open(CACHE_NAME).then(cache => {
              cache.put(request, responseClone);
            });
            return response;
          })
          .catch(() => {
            // Fallback icon veya boş response
            return new Response('Offline', { status: 503 });
          });
      })
  );
});

// Background Sync
self.addEventListener('sync', event => {
  if (event.tag === 'sync-exams') {
    event.waitUntil(syncOfflineData());
  }
});

async function syncOfflineData() {
  try {
    // IndexedDB'den offline verileri oku
    const db = await openDB();
    const pendingExams = await db.getAll('pendingExams');
    
    if (pendingExams && pendingExams.length > 0) {
      for (const exam of pendingExams) {
        await fetch('/add', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({
            subjects: exam.subject,
            date: exam.date
          })
        });
      }
      
      // Başarılı olduysa sil
      await db.clear('pendingExams');
      
      // Tüm clients'e bildir
      const clients = await self.clients.matchAll();
      clients.forEach(client => {
        client.postMessage({
          type: 'SYNC_COMPLETE',
          message: 'Offline veriler senkron edildi'
        });
      });
    }
  } catch (error) {
    console.error('Sync hatası:', error);
  }
}

function openDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('PrufungskalenderDB', 1);
    
    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const db = request.result;
      resolve({
        getAll: (storeName) => {
          return new Promise((res, rej) => {
            const trans = db.transaction(storeName, 'readonly');
            const store = trans.objectStore(storeName);
            const req = store.getAll();
            req.onsuccess = () => res(req.result);
            req.onerror = () => rej(req.error);
          });
        },
        clear: (storeName) => {
          return new Promise((res, rej) => {
            const trans = db.transaction(storeName, 'readwrite');
            const store = trans.objectStore(storeName);
            const req = store.clear();
            req.onsuccess = () => res();
            req.onerror = () => rej(req.error);
          });
        }
      });
    };
    
    request.onupgradeneeded = (event) => {
      const db = event.target.result;
      if (!db.objectStoreNames.contains('pendingExams')) {
        db.createObjectStore('pendingExams', { keyPath: 'id', autoIncrement: true });
      }
      if (!db.objectStoreNames.contains('cachedExams')) {
        db.createObjectStore('cachedExams', { keyPath: 'id' });
      }
    };
  });
}
