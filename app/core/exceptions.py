class PIVSError(Exception):
    """Base exception domain PIVS."""


class InvalidFileTypeError(PIVSError):
    """File bukan JPG/PNG."""


class FileTooLargeError(PIVSError):
    """Ukuran file melebihi batas."""


class EmptyFileError(PIVSError):
    """File kosong."""


class PassengerNotFoundError(PIVSError):
    """passenger_id yang dirujuk tidak ada."""


class KTPDocumentNotFoundError(PIVSError):
    """Dokumen KTP tidak ditemukan."""


class StorageError(PIVSError):
    """Gagal menulis/menghapus file di storage."""


class OCREngineError(PIVSError):
    """Engine OCR gagal membaca gambar (file rusak, model gagal dimuat, dll)."""


class OCRExtractionError(PIVSError):
    """OCR berjalan, tapi field wajib (NIK/Nama) tidak berhasil diekstrak."""


class DuplicateNIKError(PIVSError):
    """NIK sudah terdaftar pada penumpang lain."""


class FaceEngineError(PIVSError):
    """Engine wajah (RetinaFace/FaceNet) gagal memuat model atau memproses gambar."""


class NoFaceDetectedError(PIVSError):
    """Tidak ada wajah pada gambar."""


class MultipleFacesError(PIVSError):
    """Lebih dari satu wajah pada gambar — acuan registrasi harus tunggal."""


class InvalidImageError(PIVSError):
    """Bytes gambar tidak bisa di-decode."""


class FaceNotRegisteredError(PIVSError):
    """Penumpang belum punya wajah acuan yang aktif."""


class EmbeddingNotFoundError(PIVSError):
    """File .npy embedding acuan hilang dari storage."""


class DuplicateFaceError(PIVSError):
    """Wajah ini sudah terdaftar sebagai penumpang LAIN.

    Satu orang tidak boleh punya dua identitas. Tanpa penjagaan ini, siapa pun
    bisa mendaftarkan wajahnya ke KTP orang lain dan lolos verifikasi
    sebagai orang itu.
    """

    def __init__(
        self,
        message: str,
        *,
        passenger_id: str,
        full_name: str,
        nik: str,
        similarity: float,
    ) -> None:
        super().__init__(message)
        self.passenger_id = passenger_id
        self.full_name = full_name
        self.nik = nik
        self.similarity = similarity


class FaceMismatchError(PIVSError):
    """Wajah yang didaftarkan TIDAK cocok dengan wajah orang ini yang sudah
    terdaftar.

    Mencegah menempelkan wajah orang BERBEDA ke satu identitas. Tanpa ini,
    setelah satu KTP dibuat, wajah siapa pun bisa didaftarkan sebagai orang itu
    — sehingga banyak orang berbeda dikenali sebagai satu orang yang sama.
    """
