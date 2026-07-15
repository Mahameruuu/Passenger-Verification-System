# PIVS — Desain Database (Tahap 1)

Tahap ini **hanya database**. Belum ada FastAPI, endpoint, OCR, face recognition, atau upload file.

---

## 1. ERD

```
                        ┌──────────────────────────────┐
                        │         passengers           │
                        ├──────────────────────────────┤
                        │ PK id                  UUID  │
                        │ UQ nik            VARCHAR(16)│
                        │    full_name          VARCHAR│
                        │    birth_date            DATE│
                        │    gender                ENUM│
                        │    address               TEXT│
                        │ IX registration_status   ENUM│
                        │    created_at     TIMESTAMPTZ│
                        │    updated_at     TIMESTAMPTZ│
                        └───────────────┬──────────────┘
                                        │ 1
        ┌───────────────────┬───────────┴───────┬───────────────────┐
        │ N                 │ N                 │ N                 │ N
        │ CASCADE           │ CASCADE           │ RESTRICT          │ SET NULL
┌───────┴──────────┐ ┌──────┴─────────────┐ ┌───┴──────────────┐ ┌──┴───────────────┐
│  ktp_documents   │ │ face_registrations │ │  boarding_logs   │ │    audit_logs    │
├──────────────────┤ ├────────────────────┤ ├──────────────────┤ ├──────────────────┤
│ PK id            │ │ PK id              │ │ PK id            │ │ PK id            │
│ FK passenger_id  │ │ FK passenger_id    │ │ FK passenger_id? │ │ FK passenger_id? │
│    image_path    │ │    face_image_path │ │    camera_name   │ │    actor         │
│    ocr_json      │ │    embedding_path  │ │    boarding_type │ │    action        │
│    ocr_status    │ │    quality_score   │ │    result        │ │    entity_type   │
│    uploaded_at   │ │    verification_.. │ │    match_score   │ │    entity_id     │
│                  │ │    registration_.. │ │    captured_ima..│ │    payload       │
│                  │ │    created_at      │ │ IX boarding_time │ │    created_at    │
└──────────────────┘ └────────────────────┘ └──────────────────┘ └──────────────────┘

( ? = nullable )
```

Semua foto **tidak** disimpan di PostgreSQL. Kolom `*_path` menyimpan lokasi file:

```
storage/ktp/2026/07/3175123456789012.jpg
storage/faces/2026/07/<passenger_id>.jpg
storage/boarding/2026/07/13/<uuid>.jpg
```

---

## 2. Fungsi Setiap Tabel

| Tabel | Fungsi |
|---|---|
| `passengers` | Master identitas penumpang. Satu baris = satu orang, dikunci oleh NIK yang unik. Menyimpan hasil OCR yang **sudah divalidasi** (bukan hasil mentah) dan status registrasi keseluruhan. |
| `ktp_documents` | Riwayat setiap kali foto KTP di-upload beserta hasil mentah OCR (`ocr_json`). Dibuat sebagai riwayat, bukan di-overwrite, sehingga upload ulang / re-OCR bisa ditelusuri. |
| `face_registrations` | Pendaftaran wajah penumpang: path foto selfie, path embedding, skor kualitas. Mendukung pendaftaran ulang — hanya satu baris boleh berstatus `ACTIVE`. |
| `boarding_logs` | Catatan setiap percobaan verifikasi di gerbang: kamera, arah (masuk/keluar), skor kecocokan, foto tangkapan. Ini adalah data operasional & bukti audit. |
| `audit_logs` | Jejak audit generik atas data pribadi: siapa (`actor`) melakukan apa (`action`) terhadap entitas mana, kapan. |

**Kenapa data identitas ada di `passengers`, sementara `ocr_json` ada di `ktp_documents`?**
`passengers` adalah *source of truth* yang sudah bersih dan bisa dikoreksi manual. `ocr_json` adalah bukti mentah dari mesin OCR. Memisahkan keduanya berarti hasil OCR yang keliru bisa diperbaiki tanpa menghilangkan jejak apa yang sebenarnya dibaca mesin.

---

## 3. Penjelasan Relasi

Semua relasi adalah **one-to-many** dari `passengers`:

- `passengers 1 ── N ktp_documents` — satu penumpang bisa upload KTP lebih dari sekali (foto buram, re-OCR).
- `passengers 1 ── N face_registrations` — wajah bisa didaftarkan ulang; yang lama menjadi `REPLACED`.
- `passengers 1 ── N boarding_logs` — setiap naik/turun kapal menghasilkan satu baris.
- `passengers 1 ── N audit_logs` — banyak aksi per penumpang.

**Perilaku cascade dipilih berbeda per tabel, dan ini disengaja:**

| Relasi | ON DELETE | Alasan |
|---|---|---|
| `ktp_documents` | `CASCADE` | Dokumen tidak punya makna tanpa pemiliknya. Hapus penumpang (mis. permintaan penghapusan data pribadi) → dokumen ikut terhapus. |
| `face_registrations` | `CASCADE` | Sama: data biometrik harus ikut hilang saat penumpang dihapus. |
| `boarding_logs` | `RESTRICT` | **Riwayat boarding tidak boleh hilang diam-diam.** Ini catatan siapa yang ada di kapal — kalau ikut ter-cascade, bukti keselamatan/investigasi lenyap. Penghapusan penumpang akan ditolak sampai riwayatnya diarsipkan/dianonimkan secara sadar. |
| `audit_logs` | `SET NULL` | Jejak audit harus tetap ada, tapi boleh kehilangan tautan ke penumpang yang sudah dihapus. |

`boarding_logs.passenger_id` juga **nullable** — percobaan verifikasi yang wajahnya tidak dikenali tetap perlu dicatat, dan saat itu belum ada penumpang untuk ditautkan.

---

## 4. Index

| Index | Tabel | Alasan |
|---|---|---|
| `ix_passengers_nik` (UNIQUE) | passengers | Lookup utama saat registrasi/pencarian penumpang. |
| `ix_passengers_registration_status` | passengers | Dashboard: "berapa yang masih DRAFT / belum daftar wajah". |
| `ix_ktp_documents_passenger_id` | ktp_documents | Ambil dokumen milik satu penumpang. |
| `ix_ktp_documents_ocr_status` | ktp_documents | Worker mengambil antrian `PENDING`. |
| `ix_face_registrations_passenger_id` | face_registrations | Ambil wajah milik satu penumpang. |
| `ix_face_registrations_registration_status` | face_registrations | Memuat semua wajah `ACTIVE` ke memori saat verifikasi. |
| `uq_face_registrations_passenger_active` (UNIQUE, partial) | face_registrations | Menjamin **hanya satu** wajah `ACTIVE` per penumpang. |
| `ix_boarding_logs_passenger_id` | boarding_logs | FK lookup. |
| `ix_boarding_logs_boarding_time` | boarding_logs | Laporan per rentang waktu / manifest keberangkatan. |
| `ix_boarding_logs_passenger_id_boarding_time` | boarding_logs | Query tersering: riwayat satu penumpang, terbaru dulu. |
| `ix_audit_logs_passenger_id`, `ix_audit_logs_created_at`, `ix_audit_logs_entity_type_entity_id` | audit_logs | Penelusuran audit. |

---

## 5. Constraint

- `nik` **UNIQUE** + `CHECK (nik ~ '^[0-9]{16}$')` — NIK wajib 16 digit angka. Ini menghentikan hasil OCR sampah (`3175O123...` dengan huruf O) masuk ke database.
- `full_name` tidak boleh string kosong.
- `quality_score`, `verification_score`, `match_score` — `CHECK BETWEEN 0 AND 1`. Tipe `NUMERIC(5,4)` dipilih daripada `FLOAT` supaya ambang batas (mis. `>= 0.6500`) berperilaku persis, tanpa kejutan floating point.
- `CHECK (result <> 'MATCHED' OR passenger_id IS NOT NULL)` — hasil `MATCHED` mustahil tanpa penumpang.
- Semua FK lengkap dengan `ON DELETE` eksplisit (lihat tabel di atas).

---

## 6. Enum

| Enum | Nilai |
|---|---|
| `gender` | `LAKI_LAKI`, `PEREMPUAN` |
| `registration_status` | `DRAFT` → `KTP_VERIFIED` → `FACE_REGISTERED` → `ACTIVE`, atau `REJECTED` |
| `ocr_status` | `PENDING`, `PROCESSING`, `SUCCESS`, `PARTIAL`, `FAILED` |
| `face_registration_status` | `PENDING`, `ACTIVE`, `REPLACED`, `REJECTED` |
| `boarding_type` | `BOARDING`, `DISEMBARKING` |
| `boarding_result` | `MATCHED`, `NOT_MATCHED`, `NO_FACE_DETECTED`, `ERROR` |

`registration_status` sengaja dipecah menjadi 4 tahap karena registrasi memang berjalan dua langkah (KTP dulu, wajah menyusul). Dengan begitu sistem tahu persis penumpang mana yang berhenti di tengah jalan.

---

## 7. Rekomendasi Tabel

### Ditambahkan sekarang (1 tabel)

**`audit_logs`** — Anda menyebut "Audit Log" di daftar data yang disimpan PostgreSQL, tapi tabelnya belum ada di daftar tabel. Saya tambahkan. Untuk sistem yang menyimpan KTP dan data biometrik, jejak siapa mengakses/mengubah apa bukan fitur tambahan, melainkan kebutuhan dasar.

### Kolom tambahan di luar daftar Anda (2 kolom, keduanya di `boarding_logs`)

- `result` — tanpa ini, percobaan verifikasi yang **gagal** tidak punya tempat untuk dicatat. Padahal justru kegagalan yang paling perlu diselidiki.
- `passenger_id` dibuat **nullable** — konsekuensi langsung dari poin di atas.

### Belum ditambahkan — pertimbangkan pada tahap berikutnya

| Kandidat | Kapan diperlukan |
|---|---|
| `trips` / `schedules` (kapal, rute, jadwal) | Saat perlu manifest per keberangkatan. Sekarang `boarding_logs` hanya tahu "kapan", belum "kapal mana / pelayaran mana". **Ini yang paling cepat akan Anda butuhkan.** |
| `tickets` | Saat satu penumpang bisa punya banyak tiket. Registrasi memang dilakukan saat beli tiket, tapi identitas orang (`passengers`) tetap satu — jangan gabungkan keduanya. |
| `cameras` | Sekarang `camera_name` masih string bebas. Saat jumlah kamera bertambah dan perlu metadata (lokasi, gate, arah default), normalkan menjadi tabel + FK. |
| `users` / `operators` | Saat ada login petugas. `audit_logs.actor` akan berubah menjadi FK. |
| Kolom `embedding vector(512)` | Saat pgvector aktif. `embedding_path` akan menjadi cadangan / tetap sebagai arsip file `.npy`. |

### Tidak direkomendasikan

- Menyimpan foto sebagai `BYTEA` di PostgreSQL — sudah benar disimpan di local storage.
- Tabel terpisah untuk alamat KTP (provinsi/kota/kecamatan). Nilai OCR terlalu kotor untuk dinormalkan sekarang; simpan sebagai `TEXT` dulu.

---

## 8. Cara Menjalankan

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env    # sesuaikan kredensial PostgreSQL

# buat database (sekali saja)
psql -U postgres -c "CREATE DATABASE pivs;"

# jalankan migration
alembic upgrade head
```

Lihat SQL yang akan dieksekusi tanpa menyentuh database:

```powershell
alembic upgrade head --sql
```
