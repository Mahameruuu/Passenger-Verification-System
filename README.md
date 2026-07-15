# PIVS — Passenger Identity Verification System

Sistem registrasi & verifikasi identitas penumpang kapal (KTP + Face Recognition).

## Arsitektur saat ini (PoC)

| Komponen | Teknologi |
|---|---|
| Deteksi + alignment + pose wajah | InsightFace `buffalo_l` (SCRFD + landmark 3D) |
| **Embedding wajah** | **FaceNet InceptionResnetV1** (`vggface2`), 512-d, lokal CPU/GPU |
| OCR KTP | PaddleOCR (PP-OCRv6) |
| Database | **PostgreSQL GCP** (`cvasdp`) — skema dikelola di luar aplikasi |
| Penyimpanan embedding | **pgvector** `embedding_metadata.vector(512)`, index HNSW cosine |
| Object storage | **MinIO** — KTP, crop wajah, selfie |
| Redis | belum dipakai |

### Pemetaan ke skema GCP

| Data | Tabel |
|---|---|
| Identitas (nama, NIK) | `person` (`full_name`, `citizen_id`) |
| Embedding wajah | `embedding_metadata` (`vector`, `model_version`) |
| Object key MinIO + hasil OCR | `detection_event` (`raw_image_key`, `ocr_result`) |

`detection_event` mencatat tiap kejadian: `KTP` (upload+OCR), `ENROLL` (registrasi
wajah), `VERIFY` (verifikasi). Kolom `event_type` adalah `VARCHAR(6)` — nilai baru
harus ≤6 karakter.

> **Batas skema.** `person` tidak punya kolom tanggal lahir, tempat lahir, jenis
> kelamin, maupun alamat. OCR tetap mengekstraknya, tapi field itu hanya tersimpan
> sebagai JSON di `detection_event.ocr_result` — **tidak bisa di-query sebagai kolom**.

> **Alembic dinonaktifkan.** Migration di `alembic/` membuat skema LAMA. Menjalankannya
> terhadap database GCP akan membuat tabel asing di sana, jadi `alembic/env.py`
> menolak berjalan kecuali `PIVS_ALLOW_ALEMBIC=1`.

### Threshold FaceNet — hasil pengukuran, bukan tebakan

| | Cosine similarity |
|---|---|
| Orang **sama** (foto/pencahayaan berbeda) | min **+0.93** |
| Orang **berbeda** | maks **+0.57** |

`FACE_MATCH_THRESHOLD=0.75` berada di tengah celah itu.

**Ambang model lama tidak boleh dipakai.** Pada model ini, threshold ArcFace lama
(0.40) meloloskan **9 dari 135** pasangan orang berbeda — tanpa error apa pun.
Setiap kali model embedding diganti, threshold **wajib** diukur ulang, dan semua
embedding lama harus didaftarkan ulang (embedding antar-model tidak sebanding —
karena itu `model_version` disimpan dan pencarian memfilter berdasarkan kolom itu).

---

## Status: Tahap 6 — Face Matching

Selesai:

- [x] **Tahap 1** — Database: 5 tabel ternormalisasi, UUID PK, SQLAlchemy models, Alembic migration ([docs/database.md](docs/database.md))
- [x] **Tahap 2** — Pondasi backend: struktur project, konfigurasi FastAPI, koneksi PostgreSQL, `.env`, `GET /health`, Swagger
- [x] **Tahap 3** — Upload KTP: validasi JPG/PNG + maks 5 MB, simpan ke `storage/ktp/<tahun>/<bulan>/`, catat path ke PostgreSQL
- [x] **Tahap 4** — OCR KTP: PaddleOCR membaca NIK, Nama, TTL, Jenis Kelamin, Alamat → simpan ke `passengers`, update `ktp_documents`
- [x] **Tahap 5** — Registrasi wajah: deteksi → alignment → quality check → crop → embedding InsightFace 512-d disimpan sebagai `.npy`
- [x] **Tahap 6** — Face matching: selfie → embedding → muat `.npy` acuan → cosine similarity → threshold → update database

Belum dikerjakan (urutan berikutnya):

- [ ] Boarding verification (`boarding_logs`, masuk/keluar kapal)
- [ ] pgvector

## Struktur

```
app/
  main.py                       # FastAPI app, CORS, lifespan, router
  core/
    config.py                   # Settings (pydantic-settings, baca .env)
    exceptions.py               # Exception domain (bebas dari HTTP)
  db/
    base.py                     # Base, mixin UUID PK & timestamp, naming convention
    session.py                  # engine, SessionLocal, dependency get_db()
  models/                       # passengers, ktp_documents, face_registrations,
                                # boarding_logs, audit_logs, enums
  repositories/                 # akses DB murni (tanpa commit)
    ktp_document.py
    passenger.py
  services/                     # logika bisnis
    storage.py                  # validasi + tulis file ke local storage
    ktp_service.py              # orkestrasi upload: file + baris DB
    ocr_service.py              # orkestrasi OCR: baca → parse → passengers
    face_service.py             # orkestrasi registrasi wajah
    match_service.py            # orkestrasi face matching (1:1 dan 1:N)
    ocr/
      engine.py                 # wrapper PaddleOCR (lazy, dimuat sekali)
      ktp_parser.py             # baris OCR mentah → field KTP terstruktur
    face/
      engine.py                 # wrapper InsightFace: deteksi + align + embedding
      quality.py                # quality check (ketajaman, cahaya, pose, ukuran)
      matcher.py                # cosine similarity + perankingan kandidat
  schemas/                      # Pydantic request/response
  api/v1/
    router.py
    endpoints/
      health.py                 # GET /health
      ktp.py                    # upload, OCR, get metadata
      faces.py                  # registrasi wajah, verify 1:1, identify 1:N
tools/capture_face.py           # Live Camera: webcam → POST /faces/register
alembic/                        # migration
storage/                        # tidak masuk git
  ktp/<tahun>/<bulan>/          # foto KTP
  faces/<tahun>/<bulan>/        # crop wajah 112x112 hasil alignment
  embeddings/<nik>.npy          # embedding 512-d wajah aktif
  embeddings/archive/           # embedding lama (registrasi ulang)
sql/schema.sql                  # SQL referensi
docs/database.md                # ERD + penjelasan lengkap
```

Alur data: **controller** (HTTP) → **service** (aturan bisnis) → **repository** (SQL).
Controller tidak menyentuh database; repository tidak tahu apa itu HTTP.

## Setup

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# WAJIB: sesuaikan POSTGRES_PASSWORD di .env dengan password PostgreSQL Anda

psql -U postgres -c "CREATE DATABASE pivs;"
alembic upgrade head
```

## Menjalankan

```powershell
uvicorn app.main:app --reload
```

| URL | Isi |
|---|---|
| **http://127.0.0.1:8000/** | **Demo UI** (HTML/CSS/JS) — lihat hasil CV secara visual |
| http://127.0.0.1:8000/docs | Swagger UI |
| http://127.0.0.1:8000/redoc | ReDoc |
| http://127.0.0.1:8000/health | Health check |

## Demo UI

Halaman statis di [web/](web/), di-serve langsung oleh FastAPI di `/ui`. Tiga tab
mengikuti alur sistem:

1. **KTP + OCR** — pilih foto KTP → *Upload* → *Jalankan OCR*. Field hasil ekstraksi,
   confidence, dan peringatan tampil di sebelah kanan. `passenger_id` yang terbentuk
   otomatis dipakai tab berikutnya.
2. **Registrasi Wajah** — nyalakan webcam → *Ambil & Daftarkan* (atau dari file).
   Menampilkan **crop wajah 112×112 hasil alignment**, quality score sebagai bar,
   metrik mentah (ketajaman, kecerahan, yaw, pitch), dan alasan bila ditolak.
3. **Verifikasi Wajah** — *Verifikasi 1:1* (lawan penumpang aktif) atau
   *Identifikasi 1:N* (cari di semua wajah terdaftar). Cosine similarity ditampilkan
   sebagai bar pada skala −1…+1, dengan garis threshold sebagai penanda.

Webcam memerlukan konteks aman — `127.0.0.1` dan `localhost` sudah dianggap aman
oleh browser, jadi tidak perlu HTTPS untuk development.

### Memilih kamera (laptop ↔ webcam USB) dari `.env`

Kamera UI berjalan di **browser** (`getUserMedia`), yang tidak bisa membaca `.env`
di server. Jembatannya adalah `GET /api/v1/config`: server meneruskan pilihan
kamera ke halaman, dan `web/app.js` mengikutinya.

Cukup ubah satu baris di `.env`:

```ini
CAMERA_INDEX=0    # kamera laptop
CAMERA_INDEX=1    # webcam eksternal (USB)
```

Restart server, refresh halaman. Kamera yang aktif ditulis di bawah preview.

Lihat kamera apa saja yang terdeteksi:

```powershell
python tools/capture_face.py --list-cameras
```

Bila `CAMERA_INDEX` menunjuk kamera yang tidak ada, UI **memberitahu** dan memakai
index 0 — bukan diam-diam mengganti tanpa penjelasan. Merekam dari kamera yang salah
tidak memunculkan error apa pun, jadi kegagalannya harus terlihat.

Dropdown di atas preview bisa dipakai untuk berpindah kamera sesaat tanpa mengubah
`.env` (berguna saat mencoba-coba). Nilai dari `.env` tetap menjadi default setiap
kali halaman dimuat.

`tools/capture_face.py` membaca `CAMERA_INDEX` yang sama; `--camera N` menimpanya
sesaat.

> **Peringatan keamanan.** Untuk keperluan demo, `storage/` di-mount sebagai
> static files di `/storage` **tanpa autentikasi** — siapa pun yang bisa menebak
> path bisa membuka foto KTP dan wajah. Sebelum dipakai sungguhan, ganti dengan
> endpoint yang memeriksa hak akses dan mencatat ke `audit_logs`.

### GET /health

Mengecek aplikasi **dan** koneksi PostgreSQL (`SELECT 1`), bukan sekadar membalas "ok".

Database sehat → `200`:

```json
{
  "status": "ok",
  "app": "Passenger Identity Verification System",
  "version": "0.1.0",
  "database": "connected",
  "database_error": null
}
```

Database bermasalah → `503`, sehingga bisa langsung dipakai sebagai readiness probe:

```json
{
  "status": "degraded",
  "app": "Passenger Identity Verification System",
  "version": "0.1.0",
  "database": "disconnected",
  "database_error": "connection failed: ... password authentication failed for user \"postgres\""
}
```

### POST /api/v1/ktp/upload

Upload foto KTP. Field: `file` (wajib), `passenger_id` (opsional).

| Aturan | Perilaku |
|---|---|
| Format | JPG / PNG — diperiksa dari **magic bytes**, bukan dari ekstensi atau Content-Type |
| Ukuran | Maks 5 MB — ditegakkan saat streaming, bukan dari header `Content-Length` |
| Nama file | Digenerate ulang sebagai UUID; nama dari client tidak dipakai sebagai path |
| Lokasi | `storage/ktp/<tahun>/<bulan>/<uuid>.jpg` |
| Database | Hanya path yang disimpan, `ocr_status = PENDING` |

Respons `201`:

```json
{
  "id": "167ee492-2859-45b3-931f-30b73fe40326",
  "passenger_id": null,
  "image_path": "ktp/2026/07/6817df40b6734b16935727dcbced582b.jpg",
  "original_filename": "ktp-budi.jpg",
  "content_type": "image/jpeg",
  "file_size": 506,
  "ocr_status": "PENDING",
  "uploaded_at": "2026-07-13T14:41:52+07:00"
}
```

Kode error: `400` bukan JPG/PNG atau file kosong · `413` lebih dari 5 MB · `404` `passenger_id` tidak ada.

### POST /api/v1/ktp/{document_id}/ocr

Membaca foto KTP dengan PaddleOCR, mengekstrak **NIK, Nama, TTL, Jenis Kelamin, Alamat**,
menyimpannya ke `passengers`, lalu memperbarui `ktp_documents`
(`ocr_json`, `ocr_status`, `passenger_id`).

Selalu `200` selama gambar bisa dibaca — yang menentukan adalah `ocr_status`:

| `ocr_status` | Arti |
|---|---|
| `SUCCESS` | NIK & Nama terbaca → baris `passengers` dibuat, dokumen ditautkan |
| `PARTIAL` | Ada teks terbaca, tapi NIK/Nama tidak lengkap → penumpang **tidak** dibuat, lihat `warnings` |
| `FAILED` | Tidak ada teks terbaca sama sekali |

Respons `200`:

```json
{
  "ocr_status": "SUCCESS",
  "parsed": {
    "nik": "3175012345678901",
    "full_name": "BUDI SANTOSO",
    "birth_place": "JAKARTA",
    "birth_date": "1985-08-17",
    "gender": "LAKI_LAKI",
    "address": "JL. MERDEKA NO. 10, 005/008, KEBAYORAN, KEBAYORAN BARU"
  },
  "confidence": 0.9979,
  "warnings": [],
  "passenger_created": true,
  "passenger": { "...": "..." },
  "document": { "...": "..." }
}
```

Bila NIK sudah terdaftar, dokumen ditautkan ke penumpang yang ada dan data
identitasnya **tidak ditimpa** — nilai yang tersimpan mungkin sudah dikoreksi
manual, dan hasil OCR belum tentu lebih benar.

Alamat disusun dari `Alamat + RT/RW + Kel/Desa + Kecamatan`, sesuai urutan di KTP.

### POST /api/v1/faces/register

Menerima **satu frame kamera** (`file`) + `passenger_id`, lalu menjalankan:

**Face Detection → Face Alignment → Quality Check → Crop → Embedding**

| Tahap | Hasil |
|---|---|
| Detection | InsightFace `buffalo_l` (SCRFD). Nol atau >1 wajah → `422` |
| Alignment | `norm_crop` meluruskan wajah ke 112×112 berdasarkan 5 landmark |
| Quality Check | ketajaman, kecerahan, ukuran wajah, yaw/pitch, skor detektor |
| Crop | disimpan ke `storage/faces/<tahun>/<bulan>/<uuid>.jpg` |
| Embedding | ArcFace 512-d (L2-normalized) → `storage/embeddings/<nik>.npy` |

Database hanya menyimpan **path**-nya. pgvector menyusul pada tahap berikutnya.

Balasan `201` tetap diberikan meski wajah ditolak — yang menentukan `registration_status`:

| Status | Arti |
|---|---|
| `ACTIVE` | Wajah diterima, menjadi acuan verifikasi. Wajah aktif sebelumnya jadi `REPLACED` |
| `REJECTED` | Kualitas di bawah ambang → **embedding tidak dibuat**. Lihat `quality.reasons` |

Contoh penolakan:

```json
{
  "registration_status": "REJECTED",
  "quality": {
    "passed": false,
    "score": 0.5709,
    "reasons": ["Foto terlalu buram (ketajaman 23 < 40.0). Pastikan kamera fokus."]
  }
}
```

Ambang kualitas bisa diatur lewat `.env` (`FACE_MIN_SHARPNESS`, `FACE_MAX_YAW`, dst).

### POST /api/v1/faces/verify — matching 1:1

**Selfie → embedding → muat `.npy` acuan → cosine similarity → threshold.**

Mencocokkan selfie dengan wajah acuan **satu** penumpang (`passenger_id`).

Bila `similarity >= FACE_MATCH_THRESHOLD` (default **0.40**):

- `face_registrations.verification_score` diperbarui
- `passengers.registration_status` menjadi **`ACTIVE`** — registrasi tuntas

```json
{
  "matched": true,
  "similarity": 0.9808,
  "threshold": 0.4,
  "faces_detected": 1,
  "probe_det_score": 0.8542,
  "passenger": { "registration_status": "ACTIVE", "...": "..." }
}
```

Skor **tetap dicatat meski tidak cocok** — percobaan gagal justru yang paling perlu
ditelusuri. Bila selfie berisi beberapa wajah, yang dipakai adalah wajah **terbesar**
(paling dekat ke kamera), supaya orang yang lewat di latar belakang tidak
menggagalkan verifikasi.

### POST /api/v1/faces/identify — matching 1:N

Membandingkan selfie dengan **semua** wajah acuan aktif, mengembalikan yang paling mirip.
`passenger` hanya terisi bila skor tertinggi melewati threshold.

Perhatikan `runner_up_similarity` (skor kandidat terbaik kedua): bila nilainya rapat
dengan `similarity`, sistem sebenarnya ragu antara dua orang — hasil seperti itu
layak diperiksa manusia.

Belum memakai pgvector: semua embedding dimuat ke memori dan dibandingkan satu per
satu. Cukup untuk ribuan penumpang; di atas itu pgvector menjadi wajib.

### Mengatur threshold

| Nilai | Efek |
|---|---|
| Terlalu rendah | Orang lain bisa lolos sebagai penumpang ini (false accept) |
| Terlalu tinggi | Penumpang asli sering ditolak (false reject) |

Angka nyata dari pengujian (model `buffalo_l`):

| Perbandingan | Similarity |
|---|---|
| Orang sama, foto berbeda (blur + pencahayaan berubah) | **+0.98** |
| Orang berbeda | **+0.01** |
| Orang berbeda lainnya | **+0.09** |

Jaraknya sangat lebar, jadi `0.40` aman. Naikkan bila keamanan lebih penting
daripada kenyamanan.

### Live Camera

Akses webcam ada di sisi **client**, bukan di server — server bisa berjalan di
mesin lain, dan `cv2.VideoCapture` di server hanya melihat kamera server itu.

```powershell
uvicorn app.main:app --reload           # terminal 1
python tools/capture_face.py --passenger-id <uuid>   # terminal 2
```

SPASI mengirim frame saat ini ke `/api/v1/faces/register`; Q keluar.

### Cara tes lewat Swagger

1. `uvicorn app.main:app --reload`, buka http://127.0.0.1:8000/docs
2. **POST /api/v1/ktp/upload** → pilih foto KTP → *Execute*
3. **POST /api/v1/ktp/{document_id}/ocr** → salin `passenger.id` dari respons
4. **POST /api/v1/faces/register** → isi `passenger_id`, pilih foto wajah → *Execute*
   (panggilan pertama mengunduh & memuat model InsightFace ~300 MB)
5. **GET /api/v1/faces/passenger/{passenger_id}** → lihat riwayat registrasi wajah
