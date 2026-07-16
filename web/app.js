const API = "";               // sama origin dengan server FastAPI
const $ = (id) => document.getElementById(id);

let passengerId = null;       // penumpang aktif, hasil OCR
let documentId = null;        // dokumen KTP terakhir di-upload

/* ------------------------------------------------------------------ tabs */
document.querySelectorAll(".tab").forEach((tab) => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    $(tab.dataset.panel).classList.add("active");
  };
});

function setStatus(el, message, kind = "") {
  el.textContent = message;
  el.className = "status " + kind;
}

async function call(path, formData) {
  const res = await fetch(API + path, { method: "POST", body: formData });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

async function callJson(path, data) {
  const res = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    // Error validasi FastAPI bisa berupa array {loc,msg,...}
    const d = body.detail;
    const msg = Array.isArray(d) ? d.map((e) => e.msg).join("; ") : d || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return body;
}

function renderWarnings(list) {
  const warnings = $("ocrWarnings");
  warnings.innerHTML = "";
  (list || []).forEach((w) => {
    const li = document.createElement("li");
    li.textContent = w;
    warnings.appendChild(li);
  });
}

/* Preloader layar penuh dengan teks yang bisa disesuaikan (OCR / wajah). */
function showPreloader(text) {
  $("preloaderText").textContent = text || "Memproses…";
  $("preloader").classList.remove("hidden");
}
function hidePreloader() {
  $("preloader").classList.add("hidden");
}

/** Set penumpang aktif untuk tab registrasi & verifikasi wajah. */
function setActivePassenger(p) {
  passengerId = p.id;

  const html =
    `Wajah akan didaftarkan untuk <b>${p.full_name}</b> ` +
    `<span class="nik">NIK ${p.nik}</span>`;
  $("regTarget").innerHTML = html;
  $("regTarget").className = "target";
}

/* =========================================================== 1. KTP + OCR */
$("ktpFile").onchange = (e) => {
  const file = e.target.files[0];
  $("btnUpload").disabled = !file;
  $("btnOcr").disabled = true;
  // Dokumen baru → reset form hasil OCR sebelumnya supaya tidak tersimpan keliru.
  $("ocrForm").classList.add("hidden");
  $("ocrEmpty").classList.remove("hidden");
  $("btnEdit").disabled = true;
  $("btnSave").disabled = true;
  renderWarnings([]);
  if (file) {
    $("ktpPreview").src = URL.createObjectURL(file);
    $("ktpPreview").classList.remove("hidden");
  }
};

$("btnUpload").onclick = async () => {
  const file = $("ktpFile").files[0];
  if (!file) return;
  const fd = new FormData();
  fd.append("file", file);

  setStatus($("ktpStatus"), "Mengunggah…", "busy");
  try {
    const doc = await call("/api/v1/ktp/upload", fd);
    documentId = doc.id;
    setStatus($("ktpStatus"), `Tersimpan di MinIO: ${doc.raw_image_key}`, "ok");
    $("btnOcr").disabled = false;
  } catch (err) {
    setStatus($("ktpStatus"), err.message, "err");
  }
};

/** Aktif/nonaktifkan mode edit pada form hasil OCR. */
function setFormEditable(on) {
  ["f-nik", "f-name", "f-bplace", "f-bdate", "f-address"].forEach((id) => {
    $(id).readOnly = !on;
  });
  $("f-gender").disabled = !on;
}

$("btnOcr").onclick = async () => {
  if (!documentId) return;
  // Preloader layar penuh: memberi tahu user OCR sedang diproses.
  showPreloader("Memproses OCR…");
  setStatus($("ktpStatus"), "", "");
  $("btnOcr").disabled = true;

  try {
    const res = await call(`/api/v1/ktp/${documentId}/ocr`, new FormData());
    const p = res.parsed;

    // Isi form. Awalnya READONLY — petugas meninjau dulu.
    $("f-nik").value = p.nik ?? "";
    $("f-name").value = p.full_name ?? "";
    $("f-bplace").value = p.birth_place ?? "";
    $("f-bdate").value = p.birth_date ?? "";
    $("f-gender").value = p.gender ?? "";
    $("f-address").value = p.address ?? "";

    $("ocrEmpty").classList.add("hidden");
    $("ocrForm").classList.remove("hidden");
    setFormEditable(false);
    renderWarnings(res.warnings);

    // Belum simpan ke DB: penumpang belum aktif sampai petugas menekan Simpan.
    $("btnEdit").disabled = false;
    $("btnSave").disabled = false;
  } catch (err) {
    $("ocrEmpty").classList.remove("hidden");
    setStatus($("ktpStatus"), err.message, "err");
  } finally {
    hidePreloader();
    $("btnOcr").disabled = false;
  }
};

$("btnEdit").onclick = () => {
  setFormEditable(true);
  $("f-nik").focus();
  setStatus($("ktpStatus"), "Mode edit aktif — koreksi seperlunya, lalu Simpan.", "busy");
};

$("btnSave").onclick = async () => {
  if (!documentId) return;
  const nik = $("f-nik").value.trim();
  const fullName = $("f-name").value.trim();

  if (!/^\d{16}$/.test(nik)) {
    setStatus($("ktpStatus"), "NIK harus tepat 16 digit angka.", "err");
    return;
  }
  if (!fullName) {
    setStatus($("ktpStatus"), "Nama wajib diisi.", "err");
    return;
  }

  // Konfirmasi eksplisit sebelum data masuk database.
  const ok = confirm(
    "Apakah data sudah benar dan sesuai dengan KTP?\n\n" +
      `NIK   : ${nik}\n` +
      `Nama  : ${fullName}\n\n` +
      "Klik OK untuk menyimpan ke database."
  );
  if (!ok) return;

  const payload = {
    nik,
    full_name: fullName,
    birth_place: $("f-bplace").value.trim() || null,
    birth_date: $("f-bdate").value || null,
    gender: $("f-gender").value || null,
    address: $("f-address").value.trim() || null,
  };

  setStatus($("ktpStatus"), "Menyimpan ke database…", "busy");
  $("btnSave").disabled = true;
  try {
    const res = await callJson(`/api/v1/ktp/${documentId}/confirm`, payload);
    setFormEditable(false);
    $("btnEdit").disabled = true;
    renderWarnings(res.warnings);

    if (res.person) {
      setActivePassenger(res.person);
      $("btnShotReg").disabled = !camRegOn;
      setStatus(
        $("ktpStatus"),
        res.person_created
          ? "Tersimpan. Penumpang dibuat — lanjut ke Registrasi Wajah."
          : "Tersimpan. NIK sudah terdaftar — dokumen ditautkan ke penumpang yang ada.",
        "ok"
      );
    } else {
      setStatus($("ktpStatus"), "Tersimpan, tetapi penumpang tidak terbentuk. Periksa peringatan.", "err");
      $("btnSave").disabled = false;
    }
  } catch (err) {
    setStatus($("ktpStatus"), err.message, "err");
    $("btnSave").disabled = false;
  }
};

/* ------------------------------------------------------------- kamera */
/* Kamera berjalan di browser, sedangkan pilihannya ada di .env (server).
   Jembatannya: GET /api/v1/config. Ubah CAMERA_LABEL / CAMERA_INDEX di .env,
   restart server, refresh halaman — kamera ikut berganti. */
let CONFIG = null;

async function loadConfig() {
  if (!CONFIG) CONFIG = await fetch("/api/v1/config").then((r) => r.json());
  return CONFIG;
}

async function listCameras() {
  const devices = await navigator.mediaDevices.enumerateDevices();
  return devices.filter((d) => d.kind === "videoinput");
}

/** Pilih perangkat sesuai CAMERA_INDEX di .env. 0 = kamera laptop, 1 = webcam. */
function pickDevice(cameras, cfg) {
  const wanted = cfg.camera.index;

  if (wanted >= 0 && wanted < cameras.length) {
    return { device: cameras[wanted], note: `CAMERA_INDEX=${wanted}` };
  }

  // Index di luar jangkauan — jangan diam-diam memakai kamera lain, karena
  // merekam dari kamera yang salah tidak akan memunculkan error apa pun.
  return {
    device: cameras[0],
    note: `CAMERA_INDEX=${wanted} tidak ada (hanya terdeteksi ${cameras.length} kamera) — memakai index 0`,
    warn: true,
  };
}

const streams = {}; // stream aktif per video, supaya bisa ditutup saat berganti

async function openStream(video, overlay, deviceId, cfg) {
  if (streams[video.id]) {
    streams[video.id].getTracks().forEach((t) => t.stop());
  }
  const stream = await navigator.mediaDevices.getUserMedia({
    video: {
      deviceId: deviceId ? { exact: deviceId } : undefined,
      width: { ideal: cfg.camera.width },
      height: { ideal: cfg.camera.height },
    },
  });
  streams[video.id] = stream;
  video.srcObject = stream;
  await video.play();
  overlay.width = video.videoWidth;
  overlay.height = video.videoHeight;
  return stream;
}

async function startCamera(video, overlay, select, info, onReady) {
  try {
    const cfg = await loadConfig();

    // Nama perangkat baru terbaca SETELAH izin diberikan, jadi minta izin dulu
    // dengan kamera apa adanya, lalu tutup dan buka ulang kamera yang benar.
    const probe = await navigator.mediaDevices.getUserMedia({ video: true });
    probe.getTracks().forEach((t) => t.stop());

    const cameras = await listCameras();
    if (!cameras.length) {
      info.textContent = "Tidak ada kamera terdeteksi.";
      info.className = "cam-info warn";
      return false;
    }

    const { device, note, warn } = pickDevice(cameras, cfg);

    select.innerHTML = "";
    cameras.forEach((d, i) => {
      const option = document.createElement("option");
      option.value = d.deviceId;
      option.textContent = d.label || `Kamera ${i}`;
      option.selected = d.deviceId === device.deviceId;
      select.appendChild(option);
    });
    select.disabled = false;
    select.onchange = async () => {
      await openStream(video, overlay, select.value, cfg);
      info.textContent = `Aktif: ${select.selectedOptions[0].textContent} (dipilih manual)`;
      info.className = "cam-info";
    };

    await openStream(video, overlay, device.deviceId, cfg);
    const resolution = `${video.videoWidth}×${video.videoHeight}`;
    info.textContent = `Aktif: ${device.label || "kamera"} — ${resolution} · ${note}`;
    info.className = "cam-info" + (warn ? " warn" : "");

    onReady();
    return true;
  } catch (err) {
    info.textContent = "Tidak bisa mengakses kamera: " + err.message;
    info.className = "cam-info warn";
    return false;
  }
}

/** Ambil frame saat ini sebagai JPEG. Cermin di preview TIDAK ikut disimpan —
 *  yang dikirim ke server adalah gambar apa adanya. */
function grabFrame(video) {
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.95));
}

/* ==================================================== 2. REGISTRASI WAJAH */
let camRegOn = false;

$("btnCamReg").onclick = async () => {
  const ok = await startCamera(
    $("camReg"), $("ovlReg"), $("camSelReg"), $("camInfoReg"),
    () => {
      camRegOn = true;
      $("btnShotReg").disabled = !passengerId;
      $("btnCamReg").textContent = "Kamera Aktif";
      $("btnCamReg").disabled = true;
    }
  );
  if (ok && !passengerId) {
    setStatus($("regStatus"), "Selesaikan OCR KTP dulu untuk mendapat penumpang.", "err");
  }
};

async function registerFace(blob, filename) {
  if (!passengerId) {
    setStatus($("regStatus"), "Belum ada penumpang aktif. Jalankan OCR KTP dulu.", "err");
    return;
  }

  const fd = new FormData();
  fd.append("passenger_id", passengerId);
  fd.append("file", blob, filename);

  showPreloader("Memproses wajah…");
  setStatus($("regStatus"), "", "");
  try {
    const res = await call("/api/v1/faces/register", fd);
    const accepted = res.registration_status === "ACCEPTED";

    $("regEmpty").classList.add("hidden");
    $("regResult").classList.remove("hidden");

    const verdict = $("regVerdict");
    verdict.textContent = accepted
      ? "DITERIMA — wajah menjadi acuan verifikasi"
      : "DITOLAK — kualitas di bawah ambang";
    verdict.className = "verdict " + (accepted ? "ok" : "bad");

    // Foto wajah hasil proses — presigned URL dari penyimpanan.
    $("regCrop").src = res.face_url || "";

    // Catatan: metrik kualitas & info embedding SENGAJA tidak ditampilkan ke
    // user (tetap diproses & disimpan di server, hanya tidak dirender di UI).

    const reasons = $("regReasons");
    reasons.innerHTML = "";
    res.quality.reasons.forEach((r) => {
      const li = document.createElement("li");
      li.textContent = r;
      reasons.appendChild(li);
    });

    setStatus(
      $("regStatus"),
      accepted ? "Wajah berhasil didaftarkan." : "Wajah ditolak — perbaiki dan coba lagi.",
      accepted ? "ok" : "err"
    );
  } catch (err) {
    setStatus($("regStatus"), err.message, "err");
  } finally {
    hidePreloader();
  }
}

$("btnShotReg").onclick = async () => {
  const blob = await grabFrame($("camReg"));
  registerFace(blob, "capture.jpg");
};

// Verifikasi wajah dipindah ke halaman terpisah: /verify (web/verify.js).
