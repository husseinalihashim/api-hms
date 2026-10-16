from sqlmodel import Session, select

from app.database import engine
from app.models.core import Role, User , Ward , Bed
from app.security import hash_password

USERS = [
    ("Doctor One", "doctor1", Role.doctor),
    ("Nurse One", "nurse1", Role.nurse),
    ("Reception One", "reception1", Role.reception),
    ("Pharmacy One", "pharmacy1", Role.pharmacy),
]
with Session(engine) as session:
    for name, username, role in USERS:
        exists = session.exec(select(User).where(User.username == username)).first()
        if exists:
            continue
        session.add(User(name=name,username=username, hashed_password=hash_password("1234"), role=role))
    session.commit()
    ward = session.exec(select(Ward).where(Ward.name == "Ward A")).first()
    if ward is None:
        ward = Ward(name="Ward A")
        session.add(ward)
        session.commit()
        session.refresh(ward)
        assert ward.id is not None
        for number in range(1, 6):
            session.add(Bed(ward_id=ward.id, number=number))

    nurse = session.exec(select(User).where(User.username == "nurse1")).first()
    if nurse:
        nurse.ward_id = ward.id
        session.add(nurse)
    session.commit()
    print("Seed done")