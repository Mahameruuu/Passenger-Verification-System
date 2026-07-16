// Halaman Verifikasi Wajah (kiosk): kamera menyala otomatis, lalu wajah
// dicocokkan 1:N secara berkala TANPA menekan tombol apa pun.
const $ = (id) => document.getElementById(id);

const SCAN_INTERVAL_MS = 2000; // jarak antar pemindaian otomatis

/* ---------------------------------------------------------------- config */
let CONFIG = null;
async function loadConfig() {
  if (!CONFIG) CONFIG = await fetch("/api/v1/config").then((r) => r.json());
  return CONFIG;
}

async function listCameras() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices.filter((d) => d.kind === "videoinput");
}

/** Pilih perangkat sesuai CAMERA_INDEX di .env (0 = laptop, 1 = webcam). */
function pickDevice(cameras, cfg) {
  const wanted = cfg.camera.index;
  if (wanted >= 0 && wanted < cameras.length) return cameras[wanted];
  return cameras[0];
}

/* ---------------------------------------------------------------- kamera */
let stream = null;

/** Buka stream dari perangkat tertentu (menutup stream lama bila ada). */
async function openStream(deviceId, cfg) {
  const video = $("camVer");
  const overlay = $("ovlVer");
  if (stream) stream.getTracks().forEach((t) => t.stop());

  stream = await navigator.mediaDevices.getUserMedia({
    video: {
      deviceId: deviceId ? { exact: deviceId } : undefined,
      width: { ideal: cfg.camera.width },
      height: { ideal: cfg.camera.height },
    },
  });
  video.srcObject = stream;
  await video.play();
  overlay.width = video.videoWidth;
  overlay.height = video.videoHeight;
}

async function startCamera() {
  const cfg = await loadConfig();

  // Minta izin dulu supaya label perangkat terbaca, lalu tutup.
  const probe = await navigator.mediaDevices.getUserMedia({ video: true });
  probe.getTracks().forEach((t) => t.stop());

  const cameras = await listCameras();
  if (!cameras.length) throw new Error("Tidak ada kamera terdeteksi.");

  const device = pickDevice(cameras, cfg);

  // Isi dropdown perangkat — user bisa mengganti kamera (mis. hindari OBS).
  const select = $("camSelVer");
  select.innerHTML = "";
  cameras.forEach((d, i) => {
    const opt = document.createElement("option");
    opt.value = d.deviceId;
    opt.textContent = d.label || `Kamera ${i}`;
    opt.selected = d.deviceId === device.deviceId;
    select.appendChild(opt);
  });
  select.disabled = false;
  select.onchange = async () => {
    await openStream(select.value, cfg);
    $("camInfoVer").textContent = `Aktif: ${select.selectedOptions[0].textContent}`;
    $("camInfoVer").className = "cam-info";
  };

  await openStream(device.deviceId, cfg);
  const video = $("camVer");
  $("camInfoVer").textContent =
    `Aktif: ${device.label || "kamera"} — ${video.videoWidth}×${video.videoHeight}`;
  $("camInfoVer").className = "cam-info";
}

/** Ambil frame saat ini sebagai JPEG (dikirim apa adanya, bukan cerminnya). */
function grabFrame(video) {
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.95));
}

/* ---------------------------------------------------------------- hasil */
function maskNik(nik) {
  if (!nik) return "";
  return nik.length >= 8
    ? nik.slice(0, 4) + "*".repeat(nik.length - 8) + nik.slice(-4)
    : nik;
}

function setVerdict(state, title, who) {
  const v = $("verVerdict");
  v.textContent = title;
  v.className = "verdict big " + (state || "");
  $("verWho").textContent = who || "";
}

/* ----------------------------------------------------- loop 1:N otomatis */
let scanning = false;

async function scanOnce() {
  if (scanning) return; // jangan menumpuk request; lewati bila masih proses
  const video = $("camVer");
  if (!video || !video.videoWidth) return;

  scanning = true;
  try {
    const blob = await grabFrame(video);
    const fd = new FormData();
    fd.append("file", blob, "selfie.jpg");

    const res = await fetch("/api/v1/faces/identify", { method: "POST", body: fd });
    const body = await res.json().catch(() => ({}));

    if (res.ok) {
      if (body.matched && body.person) {
        setVerdict(
          "ok",
          "✓ Terverifikasi",
          `${body.person.full_name} · NIK ${maskNik(body.person.nik)}`
        );
      } else {
        setVerdict("bad", "✗ Tidak dikenali", "");
      }
    } else if (res.status === 422) {
      // Tidak ada wajah pada frame — keadaan normal saat menunggu penumpang.
      setVerdict("", "Mengarahkan…", "Posisikan wajah di depan kamera");
    } else if (res.status === 409) {
      setVerdict("", "Belum ada wajah terdaftar", "");
    } else {
      setVerdict("", "…", "");
    }
  } catch {
    // Error sesaat (jaringan/kamera) — diabaikan, dicoba lagi siklus berikutnya.
  } finally {
    scanning = false;
  }
}

/* ---------------------------------------------------------------- start */
let loopStarted = false;

function beginLoop() {
  if (loopStarted) return;
  loopStarted = true;
  setVerdict("", "Mengarahkan…", "Posisikan wajah di depan kamera");
  setInterval(scanOnce, SCAN_INTERVAL_MS);
}

async function boot() {
  try {
    await startCamera();
    $("btnCamVer").classList.add("hidden");
    beginLoop();
  } catch (err) {
    // Autostart bisa gagal bila browser menuntut interaksi lebih dulu —
    // tampilkan tombol sebagai jalan keluar.
    $("camInfoVer").textContent = "Kamera perlu diaktifkan: " + err.message;
    $("camInfoVer").className = "cam-info warn";
    $("btnCamVer").classList.remove("hidden");
    setVerdict("", "Kamera belum aktif", "");
  }
}

$("btnCamVer").onclick = async () => {
  try {
    await startCamera();
    $("btnCamVer").classList.add("hidden");
    beginLoop();
  } catch (err) {
    $("camInfoVer").textContent = "Tidak bisa mengakses kamera: " + err.message;
    $("camInfoVer").className = "cam-info warn";
  }
};

boot();
