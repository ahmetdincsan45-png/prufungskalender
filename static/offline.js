// Offline Mode - IndexedDB & Background Sync
let db = null;

// IndexedDB Initialization
function initDB() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open('PrufungskalenderDB', 1);
    
    request.onerror = () => {
      console.error('DB açma hatası:', request.error);
      reject(request.error);
    };
    
    request.onsuccess = () => {
      db = request.result;
      console.log('✅ IndexedDB initialized');
      resolve(db);
    };
    
    request.onupgradeneeded = (event) => {
      db = event.target.result;
      
      if (!db.objectStoreNames.contains('cachedExams')) {
        db.createObjectStore('cachedExams', { keyPath: 'id' });
      }
      if (!db.objectStoreNames.contains('cachedSubjects')) {
        db.createObjectStore('cachedSubjects');
      }
      if (!db.objectStoreNames.contains('pendingExams')) {
        db.createObjectStore('pendingExams', { keyPath: 'id', autoIncrement: true });
      }
      if (!db.objectStoreNames.contains('syncLog')) {
        db.createObjectStore('syncLog');
      }
    };
  });
}

// Exam verilerini cache'e ekle
async function cacheExams(exams) {
  if (!db) await initDB();
  
  return new Promise((resolve, reject) => {
    const trans = db.transaction('cachedExams', 'readwrite');
    const store = trans.objectStore('cachedExams');
    
    store.clear();
    exams.forEach(exam => {
      store.add(exam);
    });
    
    trans.oncomplete = () => {
      console.log('✅ Sınavlar cached:', exams.length);
      resolve();
    };
    trans.onerror = () => reject(trans.error);
  });
}

// Cache'den exams oku
async function getCachedExams() {
  if (!db) await initDB();
  
  return new Promise((resolve, reject) => {
    const trans = db.transaction('cachedExams', 'readonly');
    const store = trans.objectStore('cachedExams');
    const request = store.getAll();
    
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

// Subjects cache'e ekle
async function cacheSubjects(subjects) {
  if (!db) await initDB();
  
  return new Promise((resolve, reject) => {
    const trans = db.transaction('cachedSubjects', 'readwrite');
    const store = trans.objectStore('cachedSubjects');
    
    store.put(subjects, 'subjects');
    
    trans.oncomplete = () => {
      console.log('✅ Subjects cached');
      resolve();
    };
    trans.onerror = () => reject(trans.error);
  });
}

// Cache'den subjects oku
async function getCachedSubjects() {
  if (!db) await initDB();
  
  return new Promise((resolve, reject) => {
    const trans = db.transaction('cachedSubjects', 'readonly');
    const store = trans.objectStore('cachedSubjects');
    const request = store.get('subjects');
    
    request.onsuccess = () => resolve(request.result || []);
    request.onerror = () => reject(request.error);
  });
}

// Offline exam kaydet
async function savePendingExam(subject, date) {
  if (!db) await initDB();
  
  return new Promise((resolve, reject) => {
    const trans = db.transaction('pendingExams', 'readwrite');
    const store = trans.objectStore('pendingExams');
    
    store.add({ subject, date, timestamp: Date.now() });
    
    trans.oncomplete = () => {
      console.log('✅ Offline exam kaydedildi:', subject);
      resolve();
    };
    trans.onerror = () => reject(trans.error);
  });
}

// Service Worker Message Listener
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.ready.then(registration => {
    if (registration.sync) {
      // Sync event listener
      navigator.serviceWorker.addEventListener('message', event => {
        if (event.data.type === 'SYNC_COMPLETE') {
          console.log('🔄 Background sync tamamlandı');
          // Sayfayı yenile (sessiz)
          location.reload();
        }
      });
    }
  });
}

// İnternet durumu dinleme
window.addEventListener('online', async () => {
  console.log('📡 İnternet bağlandı!');
  showMinimalNotification('📡 Bağlantı geri geldi');
  
  // Background sync tetikle
  if ('serviceWorker' in navigator && 'SyncManager' in window) {
    try {
      const registration = await navigator.serviceWorker.ready;
      await registration.sync.register('sync-exams');
      console.log('🔄 Sync tetiklendi');
    } catch (error) {
      console.error('Sync hatası:', error);
    }
  }
});

window.addEventListener('offline', () => {
  console.log('📴 İnternet kesildi - Offline Mode');
  showMinimalNotification('📴 Çevrimdışı Mod');
});

// Minimal Notification (3 sn sonra kapanır)
function showMinimalNotification(message) {
  const existing = document.querySelector('.offline-indicator');
  if (existing) existing.remove();
  
  const indicator = document.createElement('div');
  indicator.className = 'offline-indicator';
  indicator.innerHTML = `
    <div style="
      position: fixed;
      bottom: 20px;
      right: 20px;
      background: rgba(0,0,0,0.8);
      color: white;
      padding: 12px 16px;
      border-radius: 8px;
      font-size: 0.9em;
      z-index: 10000;
      animation: slideIn 0.3s ease-out;
    ">${message}</div>
  `;
  document.body.appendChild(indicator);
  
  setTimeout(() => indicator.remove(), 3000);
}

// Initialize DB on load
document.addEventListener('DOMContentLoaded', async () => {
  try {
    await initDB();
  } catch (error) {
    console.warn('DB init hatası (eski browser?):', error);
  }
});
