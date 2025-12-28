let activeStream = null;
function stopStream(stream = activeStream) {
  if (!stream) return;
  stream.getTracks().forEach((t) => t.stop());
  activeStream = null;
}
function closeBioModal() {
  const modal = document.getElementById('bioModal');
  const videoSection = document.getElementById('videoSection');
  const bioRegForm = document.getElementById('bioRegForm');
  if (modal) modal.classList.remove('show');
  if (videoSection) videoSection.style.display = 'none';
  if (bioRegForm) bioRegForm.style.display = 'block';
  stopStream();
}

(() => {
  const videoEl = document.getElementById('video');
  const canvasEl = document.getElementById('captureCanvas');
  const ctx = canvasEl ? canvasEl.getContext('2d') : null;
  const modal = document.getElementById('bioModal');
  const modalMsg = document.getElementById('modalMsg');
  const scanMsg = document.getElementById('scanMsg');
  const bioRegForm = document.getElementById('bioRegForm');
  const videoSection = document.getElementById('videoSection');
  const cameraIcon = document.getElementById('cameraIcon');
  const bioBtn = document.getElementById('bioBtn');
  const loginForm = document.getElementById('loginForm');

  if (!videoEl || !canvasEl || !ctx || !modal || !modalMsg || !scanMsg || !bioRegForm || !videoSection || !cameraIcon || !bioBtn || !loginForm) {
    return;
  }
  const MODEL_URL = 'https://cdn.jsdelivr.net/npm/face-api.js@0.22.2/weights';
  const MATCH_THRESHOLD = 0.55;
  const MODEL_TIMEOUT_MS = 6000;

  async function ensureModels() {
    if (!window.faceapi) throw new Error('face-api.js yüklenemedi');
    // Load once; subsequent calls are fast
    const loadPromise = Promise.all([
      faceapi.nets.tinyFaceDetector.loadFromUri(MODEL_URL),
      faceapi.nets.faceLandmark68Net.loadFromUri(MODEL_URL),
      faceapi.nets.faceRecognitionNet.loadFromUri(MODEL_URL)
    ]);
    const timeoutPromise = new Promise((_, rej) => setTimeout(() => rej(new Error('Modeller zaman aşımına uğradı')), MODEL_TIMEOUT_MS));
    return Promise.race([loadPromise, timeoutPromise]);
  }

  async function getDetection() {
    const det = await faceapi
      .detectSingleFace(videoEl, new faceapi.TinyFaceDetectorOptions({ scoreThreshold: 0.5 }))
      .withFaceLandmarks()
      .withFaceDescriptor();
    return det || null;
  }

  async function getDescriptor() {
    const det = await getDetection();
    if (!det || !det.descriptor) return null;
    return Array.from(det.descriptor);
  }

  async function waitForHeadTurn(direction) {
    // direction: 'left' or 'right'
    const maxMs = 3000;
    const start = Date.now();
    let baseX = null;
    while (Date.now() - start < maxMs) {
      const det = await getDetection();
      if (det && det.landmarks) {
        const nose = det.landmarks.getNose();
        const x = nose && nose[3] ? nose[3].x : null; // nose tip approx
        if (x != null) {
          if (baseX == null) baseX = x;
          const dx = x - baseX;
          if (direction === 'left' && dx < -12) return true;
          if (direction === 'right' && dx > 12) return true;
        }
      }
      await new Promise((r) => setTimeout(r, 150));
    }
    return false;
  }

  function euclidean(a, b) {
    if (!a || !b || a.length !== b.length) return Infinity;
    let s = 0;
    for (let i = 0; i < a.length; i++) {
      const d = a[i] - b[i];
      s += d * d;
    }
    return Math.sqrt(s);
  }



  async function startCameraSafe() {
    try {
      if (!window.isSecureContext && location.hostname !== 'localhost' && location.hostname !== '127.0.0.1') {
        throw new Error('Güvenli bağlam yok (HTTPS gerekli)');
      }
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: 640, height: 480 } });
      videoEl.srcObject = stream;
      activeStream = stream;
      await new Promise((res) => {
        if (videoEl.readyState >= 2) res();
        else videoEl.onloadedmetadata = res;
      });
      return stream;
    } catch (_err) {
      return null;
    }
  }

  async function quickFrameCheck() {
    if (!ctx) return false;
    return new Promise(async (resolve) => {
      let frames = 0;
      let faceDetectedFrames = 0;
      let last = null;
      let motionSpikes = 0;
      let brightOkCount = 0;
      const timer = setInterval(async () => {
        if (!videoEl.videoWidth) return;
        canvasEl.width = videoEl.videoWidth;
        canvasEl.height = videoEl.videoHeight;
        ctx.drawImage(videoEl, 0, 0, canvasEl.width, canvasEl.height);
        const { data } = ctx.getImageData(0, 0, canvasEl.width, canvasEl.height);
        let sum = 0;
        let motion = 0;
        if (last) {
          for (let i = 0; i < data.length; i += 4) {
            const diff = Math.abs(data[i] - last[i]) + Math.abs(data[i + 1] - last[i + 1]) + Math.abs(data[i + 2] - last[i + 2]);
            if (diff > 70) motion += 1;
          }
        }
        for (let i = 0; i < data.length; i += 4) {
          sum += data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114;
        }
        last = data.slice();
        frames += 1;
        
        // Yüz tespiti yap (modeller önceden yüklü olduğunu varsay)
        try {
          const det = await faceapi
            .detectSingleFace(videoEl, new faceapi.TinyFaceDetectorOptions({ scoreThreshold: 0.5 }))
            .withFaceLandmarks();
          if (det) faceDetectedFrames += 1;
        } catch (_e) {}
        
        const avg = sum / (data.length / 4);
        const motionRatio = motion / (data.length / 4);
        const brightOk = avg > 60;
        const motionOk = motionRatio > 0.02;
        if (brightOk) brightOkCount += 1;
        if (motionRatio > 0.04) motionSpikes += 1;

        const enoughSamples = frames >= 6;
        const faceOk = faceDetectedFrames >= 3;
        const livenessOk = brightOkCount >= 3 && motionSpikes >= 1 && motionOk && faceOk;

        if (livenessOk && enoughSamples) {
          clearInterval(timer);
          resolve(true);
        } else if (frames >= 25) {
          clearInterval(timer);
          resolve(false);
        }
      }, 100);
    });
  }

  // show camera icon only if we have saved face data
  if (localStorage.getItem('faceData')) {
    cameraIcon.style.display = 'flex';
  } else {
    cameraIcon.style.display = 'none';
  }

  cameraIcon.addEventListener('click', async () => {
    const faceDataRaw = localStorage.getItem('faceData');
    if (!faceDataRaw) {
      alert('Yüz kaydı bulunamadı. Lütfen tekrar kaydedin.');
      cameraIcon.style.display = 'none';
      return;
    }
    let faceData;
    try {
      faceData = JSON.parse(faceDataRaw);
    } catch (_err) {
      alert('Kayıt hatalı. Yeniden kaydedin.');
      localStorage.removeItem('faceData');
      cameraIcon.style.display = 'none';
      return;
    }

    modal.classList.add('show');
    bioRegForm.style.display = 'none';
    videoSection.style.display = 'block';
    modalMsg.innerHTML = 'Yüzünüz doğrulanıyor...';
    scanMsg.innerHTML = '📸 Lütfen kameraya bakın';

    modalMsg.innerHTML = 'Kamera açılıyor...';
    const stream = await startCameraSafe();
    if (!stream) {
      scanMsg.innerHTML = '<div class="err">Kamera açılamadı</div>';
      setTimeout(closeBioModal, 1200);
      return;
    }
    modalMsg.innerHTML = 'Kamera hazır';

    scanMsg.innerHTML = '📸 Liveness kontrolü yapılıyor...';
    // Modelleri önceden yükle
    try {
      scanMsg.innerHTML = '📦 Modeller yükleniyor...';
      await ensureModels();
      scanMsg.innerHTML = '📸 Liveness kontrolü yapılıyor...';
    } catch (modelErr) {
      scanMsg.innerHTML = '<div class="err">Model yükleme hatalı: ' + modelErr.message + '</div>';
      setTimeout(() => {
        stopStream();
        closeBioModal();
      }, 1400);
      return;
    }
    const ok = await quickFrameCheck();
    if (!ok) {
      scanMsg.innerHTML = '<div class="err">Yüz algılanamadı. Daha aydınlık bir ortamda tekrar deneyin.</div>';
      setTimeout(() => {
        videoSection.style.display = 'none';
        bioRegForm.style.display = 'block';
        closeBioModal();
      }, 1400);
      return;
    }

    // Face descriptor match + challenge required
    try {
      scanMsg.innerHTML = '📦 Modeller yükleniyor...';
      try {
        await ensureModels();
      } catch (modelErr) {
        scanMsg.innerHTML = '<div class="err">Model yükleme hatalı: ' + modelErr.message + '</div>';
        setTimeout(closeBioModal, 1400);
        stopStream();
        return;
      }
      // Random challenge: left or right
      const dir = Math.random() < 0.5 ? 'left' : 'right';
      scanMsg.innerHTML = dir === 'left' ? '↩️ Başınızı sola çevirin' : '↪️ Başınızı sağa çevirin';
      const turned = await waitForHeadTurn(dir);
      if (!turned) {
        scanMsg.innerHTML = '<div class="err">Hareket doğrulaması başarısız.</div>';
        setTimeout(closeBioModal, 1400);
        stopStream();
        return;
      }
      scanMsg.innerHTML = '✅ Hareket doğrulandı, yüz eşleşmesi yapılıyor...';
      const desc = await getDescriptor();
      if (!desc) {
        scanMsg.innerHTML = '<div class="err">Yüz tespit edilemedi.</div>';
        setTimeout(closeBioModal, 1200);
        stopStream();
        return;
      }
      if (!faceData.d || !Array.isArray(faceData.d)) {
        scanMsg.innerHTML = '<div class="err">Kayıtlı yüz bulunamadı. Lütfen yeniden kaydedin.</div>';
        setTimeout(closeBioModal, 1200);
        stopStream();
        return;
      }
      const dist = euclidean(desc, faceData.d);
      if (dist > MATCH_THRESHOLD) {
        scanMsg.innerHTML = '<div class="err">Yüz eşleşmedi (' + dist.toFixed(3) + ').</div>';
        setTimeout(closeBioModal, 1400);
        stopStream();
        return;
      }
    } catch (err) {
      scanMsg.innerHTML = '<div class="err">Doğrulama hatası: ' + err.message + '</div>';
      setTimeout(closeBioModal, 1400);
      stopStream();
      return;
    }

    document.querySelector('input[name=username]').value = faceData.u;
    document.querySelector('input[name=password]').value = atob(faceData.p);
    modalMsg.innerHTML = 'Başarılı, giriş yapılıyor…';
    stopStream();
    closeBioModal();
    try { loginForm.submit(); } catch (_e) {}
  });

  bioBtn.addEventListener('click', () => {
    modal.classList.add('show');
  });

  bioRegForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const user = document.getElementById('bioUser').value.trim();
    const pass = document.getElementById('bioPass').value.trim();
    modalMsg.innerHTML = 'Doğrulanıyor...';
    try {
      const verifyResp = await fetch('/stats/verify-credentials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: user, password: pass })
      });
      const verifyData = await verifyResp.json();
      if (!verifyData.success) {
        modalMsg.innerHTML = '<div class="err">' + verifyData.error + '</div>';
        return;
      }
      modalMsg.innerHTML = 'Kimlik doğrulandı! Kamera açılıyor...';
      bioRegForm.style.display = 'none';
      videoSection.style.display = 'block';
      modalMsg.innerHTML = 'Kamera açılıyor...';
      const stream = await startCameraSafe();
      if (!stream) throw new Error('Kamera açılamadı');
      modalMsg.innerHTML = 'Kamera hazır';
      scanMsg.innerHTML = '📸 Liveness kontrolü yapılıyor...';
      // Modelleri önceden yükle
      try {
        scanMsg.innerHTML = '📦 Modeller yükleniyor...';
        await ensureModels();
        scanMsg.innerHTML = '📸 Liveness kontrolü yapılıyor...';
      } catch (modelErr) {
        scanMsg.innerHTML = '<div class="err">Model yükleme hatalı: ' + modelErr.message + '</div>';
        stopStream(stream);
        videoSection.style.display = 'none';
        bioRegForm.style.display = 'block';
        return;
      }
      const ok = await quickFrameCheck();
      if (!ok) {
        scanMsg.innerHTML = '<div class="err">Yüz algılanamadı. Daha aydınlıkta tekrar deneyin.</div>';
        stopStream(stream);
        videoSection.style.display = 'none';
        bioRegForm.style.display = 'block';
        return;
      }
      // Yüz descriptor üret ve kaydet
      // Random challenge: left or right
      const dir = Math.random() < 0.5 ? 'left' : 'right';
      scanMsg.innerHTML = dir === 'left' ? '↩️ Başınızı sola çevirin' : '↪️ Başınızı sağa çevirin';
      const turned = await waitForHeadTurn(dir);
      if (!turned) {
        scanMsg.innerHTML = '<div class="err">Hareket doğrulaması başarısız. Başınızı daha belirgin şekilde çevirin.</div>';
        stopStream(stream);
        videoSection.style.display = 'none';
        bioRegForm.style.display = 'block';
        return;
      }
      scanMsg.innerHTML = '✅ Hareket doğrulandı, yüz kaydediliyor...';
      let desc;
      for (let attempt = 0; attempt < 3; attempt++) {
        desc = await getDescriptor();
        if (desc) break;
        await new Promise(r => setTimeout(r, 200)); // 200ms bekle ve retry
      }
      if (!desc) {
        scanMsg.innerHTML = '<div class="err">Yüz tespit edilemedi. Kameraya doğru bakın ve çerçeve içinde durun.</div>';
        stopStream(stream);
        videoSection.style.display = 'none';
        bioRegForm.style.display = 'block';
        return;
      }
      scanMsg.innerHTML = '✓ Yüz algılandı!';
      scanMsg.style.color = '#28a745';
      scanMsg.innerHTML = '💾 Kaydediliyor...';
      const payload = { u: user, p: btoa(pass), d: desc, t: Date.now() };
      localStorage.setItem('faceData', JSON.stringify(payload));
      cameraIcon.style.display = 'flex'; // Show camera icon for next login
      stopStream(stream);
      modalMsg.innerHTML = '<div class="success">✓ Yüz tanıma kaydedildi!</div>';
      setTimeout(() => {
        closeBioModal();
        bioRegForm.style.display = 'block';
        videoSection.style.display = 'none';
        document.getElementById('bioUser').value = '';
        document.getElementById('bioPass').value = '';
        // Başarı mesajını göster ve login formuna yönlendir
        alert('✓ Yüz tanıma başarıyla kaydedildi! Şimdi kamera ikonu ile giriş yapabilirsiniz.');
        location.href = '/stats/login'; // Login sayfasına yönlendir
      }, 1200);
    } catch (err) {
      modalMsg.innerHTML = '<div class="err">Hata: ' + err.message + '</div>';
      videoSection.style.display = 'none';
      bioRegForm.style.display = 'block';
    }
  });
})();
