import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import Gender, RegistrationStatus
from app.models.passenger import Passenger


class PassengerRepository:
    """Akses data tabel passengers. Tidak melakukan commit —
    transaksi dikendalikan oleh service."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def get_by_id(self, passenger_id: uuid.UUID) -> Passenger | None:
        return self.db.get(Passenger, passenger_id)

    def get_by_nik(self, nik: str) -> Passenger | None:
        return self.db.scalar(select(Passenger).where(Passenger.nik == nik))

    def exists(self, passenger_id: uuid.UUID) -> bool:
        return self.get_by_id(passenger_id) is not None

    def create(
        self,
        *,
        nik: str,
        full_name: str,
        birth_place: str | None = None,
        birth_date: date | None = None,
        gender: Gender | None = None,
        address: str | None = None,
        registration_status: RegistrationStatus = RegistrationStatus.KTP_VERIFIED,
    ) -> Passenger:
        passenger = Passenger(
            nik=nik,
            full_name=full_name,
            birth_place=birth_place,
            birth_date=birth_date,
            gender=gender,
            address=address,
            registration_status=registration_status,
        )
        self.db.add(passenger)
        self.db.flush()
        return passenger
