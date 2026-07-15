import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import citizen_id_search_forms, encrypt_nik
from app.models.gcp import IdCardType, Person, PersonStatus


class PersonRepository:
    """Akses tabel `person`. Tidak melakukan commit — transaksi milik service."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, person_id: uuid.UUID) -> Person | None:
        return self.db.get(Person, person_id)

    def get_by_citizen_id(self, citizen_id: str) -> Person | None:
        # Cocokkan semua bentuk yang mungkin tersimpan: CBC (baru), ECB (data
        # lama), dan plaintext (data paling lama). Tanpa ini, baris yang masih
        # ber-ECB tidak akan ketemu setelah pindah ke CBC — dan bisa terbuat
        # identitas ganda untuk NIK yang sebenarnya sudah ada.
        return self.db.scalar(
            select(Person).where(
                Person.citizen_id.in_(citizen_id_search_forms(citizen_id))
            )
        )

    def exists(self, person_id: uuid.UUID) -> bool:
        return self.get_by_id(person_id) is not None

    def create(
        self,
        *,
        full_name: str,
        citizen_id: str,
        id_card_type: str = IdCardType.KTP,
        status: str = PersonStatus.ACTIVE,
    ) -> Person:
        # Enkripsi NIK sebelum disimpan ke database
        person = Person(
            full_name=full_name,
            citizen_id=encrypt_nik(citizen_id),
            id_card_type=id_card_type,
            status=status,
        )
        self.db.add(person)
        self.db.flush()
        return person
