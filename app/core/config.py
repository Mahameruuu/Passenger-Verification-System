from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Aplikasi
    app_name: str = "Passenger Identity Verification System"
    app_version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"
    debug: bool = False
    encryption_key: str = "pivs-super-secret-key-change-me!"

    # PostgreSQL (GCP)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "pivs"
    postgres_connect_timeout: int = 10

    # CORS — daftar origin dipisah koma, mis. "http://localhost:3000,http://localhost:5173"
    cors_origins: str = "*"

    # MinIO (object storage) — menggantikan folder lokal
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_bucket: str = "cvasdp"
    minio_secure: bool = False  # True bila MinIO di belakang HTTPS
    minio_presign_expiry_seconds: int = 3600

    # OCR
    ocr_lang: str = "en"  # KTP berhuruf Latin; model 'en' menangani ini dengan baik
    ocr_enable_mkldnn: bool = False  # lihat catatan di services/ocr/engine.py

    # Deteksi & alignment wajah (InsightFace) — HANYA detektor + landmark + pose.
    # Embedding-nya diambil dari FaceNet, lihat di bawah.
    face_detector_model: str = "buffalo_l"
    face_det_size: int = 640

    # Embedding wajah: FaceNet InceptionResnetV1 (512-d)
    #
    # model_version ikut disimpan ke embedding_metadata.model_version. Kolom itu
    # bukan hiasan: embedding dari model berbeda TIDAK sebanding satu sama lain,
    # dan kalau tercampur, similarity search akan menghasilkan angka yang
    # kelihatan wajar tapi salah — tanpa error apa pun.
    facenet_pretrained: str = "vggface2"  # atau "casia-webface"
    facenet_device: str = "cpu"  # "cuda" bila GPU tersedia
    face_model_version: str = "facenet-inceptionresnetv1-vggface2"
    face_embedding_dim: int = 512

    # Ambang batas kualitas foto wajah saat registrasi.
    # Foto acuan yang buruk merusak SEMUA verifikasi orang itu kelak,
    # jadi ambangnya sengaja ketat di sisi registrasi.
    face_min_det_score: float = 0.60
    face_min_size_px: int = 80
    face_min_sharpness: float = 40.0
    face_min_brightness: float = 50.0
    face_max_brightness: float = 220.0
    face_max_yaw: float = 30.0
    face_max_pitch: float = 30.0
    face_min_quality_score: float = 0.45

    # Kamera
    #
    # camera_index: 0 = kamera laptop, 1 = webcam eksternal, dst.
    #
    # Kamera UI berjalan di BROWSER (getUserMedia), yang tidak bisa membaca .env.
    # Karena itu nilai ini diekspos lewat GET /api/v1/config, lalu dipakai oleh
    # web/app.js.
    camera_index: int = 0
    camera_width: int = 640
    camera_height: int = 480

    # Ambang kecocokan wajah (cosine similarity antar embedding FaceNet).
    #
    # 0.75 BUKAN tebakan — hasil pengukuran pada model ini:
    #   orang sama    : min +0.93
    #   orang berbeda : maks +0.57
    # Celahnya bersih, 0.75 berada di tengahnya.
    #
    # Angka ini HARUS diukur ulang setiap kali model embedding berganti. Ambang
    # ArcFace lama (0.40) di model ini meloloskan 9 dari 135 pasangan orang
    # BERBEDA — tanpa error apa pun.
    face_match_threshold: float = 0.75

    # Ambang deteksi wajah ganda saat REGISTRASI: bila wajah yang didaftarkan
    # mirip dengan orang LAIN di atas ambang ini, registrasi ditolak.
    # Sengaja lebih rendah daripada ambang verifikasi (lebih sensitif): untuk
    # mencegah satu orang punya dua identitas, lebih baik salah-curiga dan minta
    # petugas memeriksa. Masih di atas skor tertinggi orang berbeda (+0.57).
    face_duplicate_threshold: float = 0.65

    # Berapa kandidat teratas yang diambil dari pgvector saat similarity search.
    # >1 supaya runner-up bisa dilaporkan (indikator sistem sedang ragu).
    face_search_top_k: int = 5

    @property
    def database_url(self) -> str:
        # Password di-URL-encode: karakter seperti @ : / # akan merusak DSN
        # (atau lebih buruk, menyambung ke host yang salah) bila ditulis mentah.
        return (
            f"postgresql+psycopg://{quote_plus(self.postgres_user)}:"
            f"{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
