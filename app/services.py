from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel import Session, col, func, select

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
from app.schemas import AdmissionCreate, PatientCreate, PrescriptionCreate, VitalsCreate


def now() -> datetime:
    return datetime.now(timezone.utc)


def get_admission(session: Session, admission_id: int) -> Admission:
    admission = session.get(Admission, admission_id)
    if admission is None:
        raise HTTPException(status_code=404, detail="Admission not found")
    return admission


def get_prescription(session: Session, prescription_id: int) -> Prescription:
    prescription = session.get(Prescription, prescription_id)
    if prescription is None:
        raise HTTPException(status_code=404, detail="Prescription not found")
    return prescription


def create_patient(session: Session, data: PatientCreate) -> dict:
    patient = Patient(name=data.name, birth_date=data.birth_date, phone=data.phone)
    session.add(patient)
    session.flush()
    assert patient.id is not None
    for medicine in data.allergies:
        session.add(Allergy(patient_id=patient.id, medicine=medicine))
    session.commit()
    session.refresh(patient)
    return {**patient.model_dump(), "allergies": data.allergies}


def list_patients(session: Session, q: str | None, limit: int, offset: int) -> list[Patient]:
    query = select(Patient).order_by(col(Patient.id))
    if q:
        query = query.where(col(Patient.name).ilike(f"%{q}%"))
    return list(session.exec(query.offset(offset).limit(limit)).all())


def list_beds(session: Session, free: bool | None) -> list[Bed]:
    query = select(Bed).order_by(col(Bed.id))
    if free is True:
        query = query.where(col(Bed.patient_id).is_(None))
    elif free is False:
        query = query.where(col(Bed.patient_id).is_not(None))
    return list(session.exec(query).all())


def admit_patient(session: Session, data: AdmissionCreate) -> Admission:
    if session.get(Patient, data.patient_id) is None:
        raise HTTPException(status_code=404, detail="Patient not found")
    if session.get(User, data.doctor_id) is None:
        raise HTTPException(status_code=404, detail="Doctor not found")
    bed = session.get(Bed, data.bed_id)
    if bed is None:
        raise HTTPException(status_code=404, detail="Bed not found")
    admission = Admission(
        patient_id=data.patient_id,
        doctor_id=data.doctor_id,
        bed_id=data.bed_id,
        admitted_at=now(),
    )
    bed.patient_id = data.patient_id
    session.add(admission)
    session.add(bed)
    session.commit()
    session.refresh(admission)
    return admission


def my_patients(session: Session, user: User) -> list[dict]:
    query = (
        select(Admission, Patient, Bed)
        .join(Patient, col(Patient.id) == col(Admission.patient_id))
        .join(Bed, col(Bed.id) == col(Admission.bed_id))
        .where(col(Admission.discharged_at).is_(None))
    )
    if user.role == Role.doctor:
        query = query.where(Admission.doctor_id == user.id)
    else:
        if user.ward_id is None:
            return []
        query = query.where(Bed.ward_id == user.ward_id)
    result = []
    for admission, patient, bed in session.exec(query).all():
        allergies = session.exec(
            select(Allergy.medicine).where(Allergy.patient_id == patient.id)
        ).all()
        result.append(
            {
                "admission_id": admission.id,
                "diagnosis": admission.diagnosis,
                "admitted_at": admission.admitted_at,
                "patient": patient,
                "allergies": list(allergies),
                "bed": {"id": bed.id, "number": bed.number, "ward_id": bed.ward_id},
            }
        )
    return result


def set_diagnosis(session: Session, user: User, admission_id: int, text: str) -> Admission:
    admission = get_admission(session, admission_id)
    admission.diagnosis = text
    session.add(admission)
    session.commit()
    session.refresh(admission)
    return admission


def add_prescription(
    session: Session, user: User, admission_id: int, data: PrescriptionCreate
) -> Prescription:
    get_admission(session, admission_id)
    prescription = Prescription(
        admission_id=admission_id, medicine=data.medicine, dose=data.dose, created_at=now()
    )
    session.add(prescription)
    session.commit()
    session.refresh(prescription)
    return prescription


def list_prescriptions(
    session: Session, status: PrescriptionStatus | None, limit: int, offset: int
) -> list[Prescription]:
    query = select(Prescription).order_by(col(Prescription.id))
    if status is not None:
        query = query.where(Prescription.status == status)
    return list(session.exec(query.offset(offset).limit(limit)).all())


def mark_given_out(session: Session, prescription_id: int) -> Prescription:
    prescription = get_prescription(session, prescription_id)
    prescription.status = PrescriptionStatus.given_out
    session.add(prescription)
    session.commit()
    session.refresh(prescription)
    return prescription


def mark_administered(session: Session, user: User, prescription_id: int) -> Prescription:
    prescription = get_prescription(session, prescription_id)
    prescription.status = PrescriptionStatus.administered
    session.add(prescription)
    session.commit()
    session.refresh(prescription)
    return prescription


def add_vitals(session: Session, user: User, admission_id: int, data: VitalsCreate) -> Vitals:
    get_admission(session, admission_id)
    assert user.id is not None
    vitals = Vitals(
        admission_id=admission_id,
        temperature=data.temperature,
        blood_pressure=data.blood_pressure,
        recorded_by=user.id,
        recorded_at=now(),
    )
    session.add(vitals)
    session.commit()
    session.refresh(vitals)
    return vitals


def set_discharge_note(session: Session, user: User, admission_id: int, text: str) -> Admission:
    admission = get_admission(session, admission_id)
    admission.discharge_note = text
    session.add(admission)
    session.commit()
    session.refresh(admission)
    return admission


def discharge(session: Session, admission_id: int) -> Admission:
    admission = get_admission(session, admission_id)
    admission.discharged_at = now()
    session.add(admission)
    session.commit()
    session.refresh(admission)
    return admission


def today_report(session: Session) -> dict:
    start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    patients_today = session.exec(
        select(func.count()).select_from(Admission).where(Admission.admitted_at >= start)
    ).one()
    beds_in_use = session.exec(
        select(func.count()).select_from(Bed).where(col(Bed.patient_id).is_not(None))
    ).one()
    return {"patients_today": patients_today, "beds_in_use": beds_in_use}