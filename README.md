# PIVS — Passenger Identity Verification System

Sistem registrasi & verifikasi identitas penumpang kapal: **KTP (OCR)** + **Face
Recognition**. Tahap saat ini: **PoC**, dirancang agar pipeline AI-nya siap
dipindah ke **Axelera Metis** tanpa mengubah business logic.

## Arsitektur saat ini

| Komponen | Teknologi |
|---|---|
| OCR KTP | **PaddleOCR** |
| Deteksi wajah + 5 landmark | **RetinaFace ResNet50** (ONNX, artefak resmi Axelera Model Zoo, input 840×840) |
| Alignment wajah | Similarity transform 5-landmark ke template ArcFace (di host, `cv2`/`skimage`) |
| Pose (yaw/pitch untuk quality check) | Estimasi `solvePnP` dari 5 landmark |
| Embedding wajah | **FaceNet InceptionResnetV1** (`vggface2`), 512-d, L2-normalized |
| Backend inference | Dipilih via `FACE_ENGINE` — `cpu` (onnxruntime + facenet-pytorch) sekarang; `metis` (Axelera Runtime) menyusul |
| Database | **PostgreSQL GCP** (`cvasdp`) — skema dikelola di luar aplikasi |
| Penyimpanan embedding | **pgvector** `embedding_metadata.vector(512)`, index HNSW cosine (`<=>`) |
| Object storage | **MinIO** — foto KTP, crop wajah, selfie |
| Enkripsi NIK | **AES-256-CBC** (deterministik) → `person.citizen_id` |

InsightFace, SCRFD, `buffalo_l`, dan penyimpanan `.npy` **sudah tidak dipakai**.

### Pipeline wajah (identik untuk registrasi & verifikasi)

```
gambar → RetinaFace ResNet50 (deteksi + 5 landmark)
       → align_face (template ArcFace 5-titik)
       → FaceNet vggface2 (embedding 512-d)
       → DetectedFace(bbox, det_score, landmarks, aligned, embedding, pose)
```

Abstraksi `FaceEngine` (`app/services/face/engine.py`) memisahkan primitive yang
bergantung backend (`detect_face`, `generate_embedding`) dari geometri yang murni
(`align_face`) dan orkestrasi (`detect`). Pindah ke Metis = ganti config
`FACE_ENGINE`, **tanpa** menyentuh business logic. Lihat
[Kesiapan Axelera Metis](#kesiapan-axelera-metis).

### Pemetaan ke skema GCP

| Data | Tabel |
|---|---|
| Identitas (nama, NIK terenkripsi) | `person` (`full_name`, `citizen_id`) |
| Embedding wajah | `embedding_metadata` (`vector`, `model_version`) |
| Object key MinIO + jejak kejadian | `detection_event` (`raw_image_key`, `detection_status`) |

`detection_event` mencatat tiap kejadian. Tabelnya **ter-partisi RANGE** menurut
`event_timestamp` (ada partisi `DEFAULT`). Kolom **`detection_status VARCHAR(20)`**
menyimpan gabungan **`"JENIS:STATUS"`**, mis. `KTP:PENDING`, `KTP:SUCCESS`,
`ENROLL:ACCEPTED`, `VERIFY:MATCHED`. Di model, `event_type` & `verification_status`
adalah **properti** yang encode/decode kolom itu.

> **Batas skema.** Tabel `detection_event` **tidak** punya kolom `ocr_result`,
> `event_type`, `verification_status`, maupun `gate_id`. `person` juga tidak punya
> kolom TTL/tempat lahir/jenis kelamin/alamat. OCR tetap mengekstrak field itu dan
> menampilkannya di UI, tapi **hanya NIK + Nama yang dipersistensikan** (di `person`).
> Metrik kualitas wajah dihitung tapi tidak disimpan (tak ada kolomnya).

### Keamanan NIK

NIK dienkripsi **AES-256-CBC deterministik** sebelum disimpan ke `person.citizen_id`
(`app/core/security.py`). Deterministik supaya tetap bisa dicari & `UNIQUE` berlaku;
hasilnya 32 hex agar muat di `VARCHAR(32)`. Dekripsi kompatibel-mundur (mengenali
data ECB lama & plaintext). Kunci dari `ENCRYPTION_KEY` di `.env` — **wajib** diisi
nilai acak yang kuat di produksi.

### Threshold FaceNet — hasil pengukuran, bukan tebakan

| | Cosine similarity |
|---|---|
| Orang **sama** (foto/pencahayaan berbeda) | min **+0.93** |
| Orang **berbeda** | maks **+0.57** |

`FACE_MATCH_THRESHOLD=0.75` berada di tengah celah itu. `FACE_DUPLICATE_THRESHOLD=0.65`
dipakai saat registrasi untuk mencegah satu wajah didaftarkan sebagai dua identitas.

**Ambang wajib diukur ulang setiap kali model embedding berganti** — embedding
antar-model tidak sebanding, karena itu `model_version` disimpan dan pencarian
memfilter berdasarkan kolom itu.

---

## Alur aplikasi

### 1. KTP + OCR (halaman `/ui/`)
Upload foto KTP → **Jalankan OCR** (read-only, belum menyimpan person) → hasil
tampil sebagai **form yang bisa dikoreksi** (readonly dulu) → **Edit** bila perlu →
**Simpan** (ada konfirmasi) → baru `person` dibuat. OCR dan penyimpanan **dipisah**
agar hasil OCR yang keliru tidak langsung jadi identitas. NIK `UNIQUE`: bila sudah
terdaftar, dokumen ditautkan ke person yang ada tanpa menimpa.

### 2. Registrasi wajah (halaman `/ui/`, tab Registrasi)
Kamera → RetinaFace → alignment → quality check → FaceNet embedding → simpan ke
`embedding_metadata` (pgvector). **Hanya lewat kamera** (upload file dihapus). Dua
pengaman identitas:
- **Cek wajah milik orang lain** — bila wajah cocok dengan person LAIN di atas
  `FACE_DUPLICATE_THRESHOLD` → ditolak (`409`).
- **Cek konsistensi identitas** — bila person sudah punya wajah, wajah baru **wajib
  cocok** dengan yang ada; kalau tidak → ditolak (`409`). Mencegah menempelkan wajah
  orang berbeda ke satu identitas.

Ditolak juga bila: tidak ada wajah, >1 wajah, wajah terlalu kecil, atau kualitas
buruk (buram/gelap/menoleh).

### 3. Verifikasi wajah (halaman terpisah `/verify`)
**Kiosk otomatis 1:N** — kamera menyala otomatis, wajah dicocokkan berkala (~2 detik)
**tanpa menekan tombol**. Menampilkan **✓ Terverifikasi + Nama** atau **✗ Tidak
dikenali**. Ada dropdown pemilih kamera (mis. untuk menghindari OBS Virtual Camera).
Angka teknis (similarity/threshold) sengaja tidak ditampilkan.

---

## Struktur

```
app/
  main.py                       # FastAPI app, CORS, lifespan, warm-up OCR, mount /ui + /verify
  core/
    config.py                   # Settings (pydantic-settings) — engine, RetinaFace, threshold, dll
    security.py                 # enkripsi/dekripsi NIK (AES-256-CBC)
    exceptions.py               # exception domain (bebas HTTP)
  db/session.py                 # engine, SessionLocal, get_db()
  models/gcp.py                 # pemetaan tabel GCP: person, embedding_metadata, detection_event
  repositories/                 # akses DB murni (tanpa commit)
    person.py  embedding.py  detection_event.py  ...
  services/
    storage.py                  # MinIO: upload, presigned URL
    ktp_service.py              # upload KTP → detection_event
    ocr_service.py              # OCR (read-only) + confirm() (simpan person)
    face_service.py             # registrasi wajah + cek duplikat & konsistensi identitas
    match_service.py            # verifikasi 1:1 & identifikasi 1:N (pgvector)
    ocr/
      engine.py                 # PaddleOCR (singleton, warm-up di startup)
      ktp_parser.py             # baris OCR → field KTP terstruktur
    face/
      engine.py                 # FaceEngine (ABC) + CPUFaceEngine (RetinaFace ONNX + FaceNet) + factory
      quality.py                # quality check (ketajaman, cahaya, pose, ukuran)
      matcher.py                # cosine similarity + perankingan
  schemas/                      # Pydantic request/response
  api/v1/endpoints/
    health.py  ktp.py  faces.py  config.py
web/
  index.html  app.js            # halaman registrasi (/ui/): KTP+OCR, Registrasi Wajah
  verify.html  verify.js        # halaman verifikasi kiosk (/verify): 1:N otomatis
  style.css
models/                         # artefak ONNX (tidak masuk git — lihat catatan)
  Retinaface_resnet50_840.onnx  # auto-unduh dari Axelera Model Zoo saat pertama jalan
  facenet_inceptionresnetv1_vggface2.onnx   # hasil tools/export_facenet_onnx.py (untuk Metis)
tools/
  export_facenet_onnx.py        # export FaceNet → ONNX + spec sheet (bahan tes compile Metis)
  capture_face.py               # live camera client (opsional)
```

Alur data: **controller** (HTTP) → **service** (aturan bisnis) → **repository** (SQL).

---

## Setup

Database **PostgreSQL GCP** dan **MinIO** dikelola di luar aplikasi — tidak ada
`postgres`/`alembic` lokal yang perlu dijalankan.

```powershell
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# WAJIB isi: kredensial POSTGRES_*, MINIO_*, dan ENCRYPTION_KEY (nilai acak kuat)
```

Saat pertama jalan, RetinaFace ONNX **terunduh otomatis** ke `models/` (dari
`RETINAFACE_MODEL_URL`, md5 diverifikasi) dan bobot FaceNet di-cache oleh
facenet-pytorch. Tidak perlu Docker.

## Menjalankan

```powershell
uvicorn app.main:app --reload
```

| URL | Isi |
|---|---|
| **http://127.0.0.1:8000/ui/** | **Registrasi**: KTP + OCR, lalu Registrasi Wajah |
| **http://127.0.0.1:8000/verify** | **Verifikasi wajah** (kiosk 1:N otomatis) |
| http://127.0.0.1:8000/docs | Swagger UI |
| http://127.0.0.1:8000/health | Health check (app + koneksi PostgreSQL) |

File UI di-serve dengan header **no-cache** (agar perubahan tampilan langsung
terlihat tanpa hard-refresh).

### Memilih kamera

Kamera berjalan di **browser** (`getUserMedia`), tidak bisa membaca `.env` server.
Jembatannya `GET /api/v1/config`. Default mengikuti `CAMERA_INDEX` di `.env`
(`0` = kamera laptop, `1` = webcam USB); di halaman ada **dropdown** untuk berpindah
kamera sesaat (mis. menghindari OBS Virtual Camera).

## Endpoint utama

| Method & Path | Fungsi |
|---|---|
| `GET /health` | Cek app + koneksi PostgreSQL (`SELECT 1`) |
| `POST /api/v1/ktp/upload` | Upload foto KTP ke MinIO (JPG/PNG, maks 5 MB) → `detection_event` PENDING |
| `POST /api/v1/ktp/{id}/ocr` | Jalankan OCR (read-only) → kembalikan field terparse |
| `POST /api/v1/ktp/{id}/confirm` | Simpan identitas (data terkonfirmasi) → buat `person` |
| `POST /api/v1/faces/register` | Registrasi wajah → embedding ke pgvector (`409` bila duplikat/tak konsisten) |
| `POST /api/v1/faces/verify` | Verifikasi 1:1 (lawan satu person) |
| `POST /api/v1/faces/identify` | Identifikasi 1:N (cari di semua wajah) |
| `GET /api/v1/faces/passenger/{id}` | Riwayat embedding seseorang |
| `GET /api/v1/faces/model` | Info model & backend yang sedang dipakai |

---

## Kesiapan Axelera Metis

Tujuan: pindah CPU → Metis cukup **ganti inference backend**, tanpa mengubah model
maupun business logic.

**Sudah siap:**
- Abstraksi `FaceEngine` + factory `get_face_engine()` (backend via `FACE_ENGINE`).
- **RetinaFace** = artefak resmi Axelera; fungsi decode (`generate_priors` /
  `decode_loc` / `decode_landm`, cfg, letterbox 840) **identik** dengan operator
  `retinaface.py` Axelera.
- **FaceNet** sudah bisa di-export ke ONNX: `python tools/export_facenet_onnx.py`
  (terverifikasi faithful terhadap PyTorch, plus mencetak spec preprocessing).

**Belum / porsi tim Metis:**
- **`MetisFaceEngine`** belum ada — `FACE_ENGINE=metis` sengaja melempar error
  (bukan diam-diam fallback). Perlu implementasi `detect_face()` (Axelera Runtime) +
  `generate_embedding()`; `align_face()` diwarisi.
- **Kompilasi ONNX** lewat Voyager SDK (quantize + calibration). FaceNet bukan model
  resmi Axelera → **wajib tes compile dulu** (cek dukungan operator).
- **Konsistensi embedding:** bila FaceNet dijalankan ter-kuantisasi di Metis,
  embedding bergeser dari versi CPU → ukur ulang threshold, pakai `model_version`
  berbeda, dan register+verify harus backend FaceNet yang **sama**.

**Rekomendasi bertahap:** jalankan **RetinaFace di Metis** (resmi, berat) tapi
**FaceNet tetap di host CPU** → embedding identik dengan data registrasi, threshold
& re-registrasi tidak berubah.

---

## Batasan PoC (diketahui)

- **Belum ada login/otentikasi** petugas — "penumpang aktif" bersifat sesi browser.
- **Belum ada liveness / anti-spoofing** — foto/layar bisa lolos verifikasi.
- **Wajah tidak dicocokkan dengan foto di KTP** — registrasi wajah pertama berbasis
  kepercayaan petugas.
- Field KTP selain NIK & Nama tidak dipersistensikan (tak ada kolomnya).

> **Catatan git.** File `models/*.onnx` besar (±195 MB total) — masukkan ke
> `.gitignore` saat `git init`, jangan di-commit. RetinaFace terunduh otomatis;
> FaceNet ONNX dihasilkan skrip export.
