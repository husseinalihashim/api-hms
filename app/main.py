from datetime import date, datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, or_
from sqlmodel import Session, SQLModel, col, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.database import get_session
from app.deps import get_current_user, require_roles
from app.models.core import (
    Admission,
    Allergy,
    Bed,
    Patient,
    Prescription,
    PrescriptionStatus,
    Role,
    User,
    Vitals,
)
from app.security import create_access_token, verify_password

app = FastAPI(title="HMS API")

def ok(data=None, message: str = "Request completed successfully"):
    return {"success": True, "data": data, "message": message}


@app.exception_handler(StarletteHTTPException)
async def http_error(request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.detail, "errors": {}},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc: RequestValidationError):
    errors = {".".join(str(p) for p in e["loc"]): e["msg"] for e in exc.errors()}
    return JSONResponse(
        status_code=422,
        content={"success": False, "message": "Validation failed", "errors": errors},
    )

def get_admission_or_404(session: Session, admission_id: int) -> Admission:
    admission = session.get(Admission, admission_id)
    if admission is None:
        raise HTTPException(status_code=404, detail="Admission not found")
    return admission


def get_own_admission(session: Session, admission_id: int, doctor: User) -> Admission:
    admission = get_admission_or_404(session, admission_id)
    if admission.doctor_id != doctor.id:
        raise HTTPException(status_code=403, detail="This is not your patient")
    return admission


def check_nurse_ward(session: Session, nurse: User, admission: Admission) -> None:
    bed = session.get(Bed, admission.bed_id)
    if bed is None or nurse.ward_id is None or bed.ward_id != nurse.ward_id:
        raise HTTPException(status_code=403, detail="This patient is not in your ward")

class PatientCreate(SQLModel):
    name: str
    birth_date: date
    phone: str | None = None
    allergies: list[str] = []


class AdmissionCreate(SQLModel):
    patient_id: int
    doctor_id: int
    bed_id: int


class TextIn(SQLModel):
    text: str


class PrescriptionCreate(SQLModel):
    medicine: str
    dose: str


class VitalsCreate(SQLModel):
    temperature: float
    blood_pressure: str

@app.post("/auth/login")
def login(
    form: OAuth2PasswordRequestForm = Depends(),
    session: Session = Depends(get_session),
):
    user = session.exec(
        select(User).where(
            or_(col(User.email) == form.username, col(User.username) == form.username)
        )
    ).first()
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Wrong email or password")
    token = create_access_token(user.username, user.role.value)
    return {"access_token": token, "token_type": "bearer", "role": user.role.value}


@app.get("/me")
def me(user: User = Depends(get_current_user)):
    return ok({"id": user.id, "name": user.name, "email": user.email, "role": user.role})

@app.post("/patients", status_code=201)
def create_patient(
    data: PatientCreate,
    user: User = Depends(require_roles(Role.reception)),
    session: Session = Depends(get_session),
):


    patient = Patient(name=data.name, birth_date=data.birth_date, phone=data.phone)
    session.add(patient)
    session.commit()
    session.refresh(patient)
    assert patient.id is not None
    for medicine in data.allergies:
        session.add(Allergy(patient_id=patient.id, medicine=medicine))
    session.commit()
    return ok({**patient.model_dump(), "allergies": data.allergies}, "Patient created")


@app.get("/patients")
def list_patients(
    q: str | None = None,
    skip: int = 0,
    limit: int = 50,
    user: User = Depends(require_roles(Role.reception)),
    session: Session = Depends(get_session),
):
    query = select(Patient)
    if q:
        query = query.where(col(Patient.name).ilike(f"%{q}%"))
    return ok(session.exec(query.offset(skip).limit(limit)).all())


@app.get("/beds")
def list_beds(
    free: bool = False,
    skip: int = 0,
    limit: int = 50,
    user: User = Depends(require_roles(Role.reception)),
    session: Session = Depends(get_session),
):
    query = select(Bed)
    if free:
        query = query.where(col(Bed.patient_id).is_(None))
    return ok(session.exec(query.offset(skip).limit(limit)).all())


@app.post("/admissions", status_code=201)
def admit_patient(
    data: AdmissionCreate,
    user: User = Depends(require_roles(Role.reception)),
    session: Session = Depends(get_session),
):
    if session.get(Patient, data.patient_id) is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    doctor = session.get(User, data.doctor_id)
    if doctor is None or doctor.role != Role.doctor:
        raise HTTPException(status_code=404, detail="Doctor not found")
    bed = session.get(Bed, data.bed_id)
    if bed is None:
        raise HTTPException(status_code=404, detail="Bed not found")
    if bed.patient_id is not None:
        raise HTTPException(status_code=400, detail="Bed is already occupied")
    already = session.exec(
        select(Admission).where(
            Admission.patient_id == data.patient_id,
            col(Admission.discharged_at).is_(None),
        )
    ).first()
    if already:
        raise HTTPException(status_code=400, detail="Patient is already admitted")
    admission = Admission(
        patient_id=data.patient_id,
        doctor_id=data.doctor_id,
        bed_id=data.bed_id,
        admitted_at=datetime.now(timezone.utc),
    )
    bed.patient_id = data.patient_id
    session.add(admission)
    session.add(bed)
    session.commit()
    session.refresh(admission)
    return ok(admission, "Patient admitted")


@app.post("/admissions/{admission_id}/discharge")
def discharge(
    admission_id: int,
    user: User = Depends(require_roles(Role.reception)),
    session: Session = Depends(get_session),
):
    admission = get_admission_or_404(session, admission_id)
    if admission.discharged_at is not None:
        raise HTTPException(status_code=400, detail="Patient is already discharged")
    if not admission.discharge_note:
        raise HTTPException(status_code=400, detail="Discharge note is required first")
    admission.discharged_at = datetime.now(timezone.utc)
    bed = session.get(Bed, admission.bed_id)
    if bed is not None:
        bed.patient_id = None
        session.add(bed)
    session.add(admission)
    session.commit()
    session.refresh(admission)
    return ok(admission, "Patient discharged")


@app.get("/reports/today")
def report_today(
    user: User = Depends(require_roles(Role.reception)),
    session: Session = Depends(get_session),
):
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    admitted_today = session.exec(
        select(func.count()).select_from(Admission).where(Admission.admitted_at >= start)
    ).one()
    total_beds = session.exec(select(func.count()).select_from(Bed)).one()
    beds_in_use = session.exec(
        select(func.count()).select_from(Bed).where(col(Bed.patient_id).is_not(None))
    ).one()
    return ok(
        {
            "patients_admitted_today": admitted_today,
            "beds_in_use": beds_in_use,
            "beds_free": total_beds - beds_in_use,
        }
    )

@app.get("/me/patients")
def my_patients(
    user: User = Depends(require_roles(Role.doctor, Role.nurse)),
    session: Session = Depends(get_session),
):
    if user.role == Role.doctor:
        query = (
            select(Patient)
            .join(Admission, col(Admission.patient_id) == col(Patient.id))
            .where(Admission.doctor_id == user.id, col(Admission.discharged_at).is_(None))
        )
    else:
        if user.ward_id is None:
            return ok([])
        query = (
            select(Patient)
            .join(Bed, col(Bed.patient_id) == col(Patient.id))
            .where(Bed.ward_id == user.ward_id)
        )
    return ok(session.exec(query).all())


@app.post("/admissions/{admission_id}/diagnosis")
def set_diagnosis(
    admission_id: int,
    data: TextIn,
    user: User = Depends(require_roles(Role.doctor)),
    session: Session = Depends(get_session),
):
    admission = get_own_admission(session, admission_id, user)
    admission.diagnosis = data.text
    session.add(admission)
    session.commit()
    session.refresh(admission)
    return ok(admission, "Diagnosis saved")


@app.post("/admissions/{admission_id}/prescriptions", status_code=201)
def create_prescription(
    admission_id: int,
    data: PrescriptionCreate,
    user: User = Depends(require_roles(Role.doctor)),
    session: Session = Depends(get_session),
):
    admission = get_own_admission(session, admission_id, user)
    if admission.discharged_at is not None:
        raise HTTPException(status_code=400, detail="Patient is already discharged")
    allergic = session.exec(
        select(Allergy).where(
            Allergy.patient_id == admission.patient_id,
            func.lower(Allergy.medicine) == data.medicine.lower(),
        )
    ).first()
    if allergic:
        raise HTTPException(
            status_code=400,
            detail=f"Patient is allergic to {data.medicine}",
        )
    prescription = Prescription(
        admission_id=admission_id,
        medicine=data.medicine,
        dose=data.dose,
        status=PrescriptionStatus.waiting,
        created_at=datetime.now(timezone.utc),
    )


    session.add(prescription)
    session.commit()
    session.refresh(prescription)
    return ok(prescription, "Prescription created")



@app.post("/admissions/{admission_id}/discharge-note")
def set_discharge_note(
    admission_id: int,
    data: TextIn,
    user: User = Depends(require_roles(Role.doctor)),
    session: Session = Depends(get_session),
):
    admission = get_own_admission(session, admission_id, user)
    admission.discharge_note = data.text
    session.add(admission)
    session.commit()
    session.refresh(admission)
    return ok(admission, "Discharge note saved")


@app.post("/admissions/{admission_id}/vitals", status_code=201)
def add_vitals(
    admission_id: int,
    data: VitalsCreate,
    user: User = Depends(require_roles(Role.nurse)),
    session: Session = Depends(get_session),
):
    admission = get_admission_or_404(session, admission_id)
    check_nurse_ward(session, user, admission)
    assert user.id is not None
    vitals = Vitals(
        admission_id=admission_id,
        temperature=data.temperature,
        blood_pressure=data.blood_pressure,
        recorded_by=user.id,
        recorded_at=datetime.now(timezone.utc),
    )
    session.add(vitals)
    session.commit()
    session.refresh(vitals)
    return ok(vitals, "Vitals recorded")


@app.post("/prescriptions/{prescription_id}/administered")
def mark_administered(
    prescription_id: int,
    user: User = Depends(require_roles(Role.nurse)),
    session: Session = Depends(get_session),
):
    prescription = session.get(Prescription, prescription_id)
    if prescription is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    admission = get_admission_or_404(session, prescription.admission_id)
    check_nurse_ward(session, user, admission)
    if prescription.status != PrescriptionStatus.given_out:
        raise HTTPException(
            status_code=400, detail="Pharmacy has not given out this medicine yet"
        )
    prescription.status = PrescriptionStatus.administered
    session.add(prescription)
    session.commit()
    session.refresh(prescription)
    return ok(prescription, "Medicine marked as given")

@app.get("/prescriptions")
def list_prescriptions(
    status: PrescriptionStatus = PrescriptionStatus.waiting,
    skip: int = 0,
    limit: int = 50,
    user: User = Depends(require_roles(Role.pharmacy)),
    session: Session = Depends(get_session),
):

    query = select(Prescription).where(Prescription.status == status)
    return ok(session.exec(query.offset(skip).limit(limit)).all())


@app.patch("/prescriptions/{prescription_id}/given-out")
def mark_given_out(
    prescription_id: int,
    user: User = Depends(require_roles(Role.pharmacy)),
    session: Session = Depends(get_session),
):


    prescription = session.get(Prescription, prescription_id)
    if prescription is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    if prescription.status != PrescriptionStatus.waiting:
        raise HTTPException(status_code=400, detail="Prescription is not waiting")
    prescription.status = PrescriptionStatus.given_out
    session.add(prescription)
    session.commit()
    session.refresh(prescription)
    return ok(prescription, "Medicine given out")