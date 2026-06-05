from sqlalchemy import or_
from models import Schedule, LabRequirement, TheoryRequirement, FacultyAvailability, Subject, Room
import random

DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
SLOTS_PER_DAY = 8
MAX_CONTINUOUS_TEACHING_SLOTS = 2
MAX_THEORY_SESSIONS_PER_SUBJECT_PER_DAY = 2
THEORY_SLOT_ORDER = [0, 1, 2, 3, 4, 5, 6, 7]
LAB_START_ORDER = {
    3: [5, 0],
    2: [5, 6, 3, 0, 1],
    1: [5, 6, 7, 3, 4, 0, 1, 2]
}

def _normalize(value):
    return str(value or '').strip().lower()

def _batch_tokens(value):
    normalized = _normalize(value)
    if not normalized:
        return set()
    for separator in ('/', ',', '+', '&'):
        normalized = normalized.replace(separator, ' ')
    normalized = normalized.replace(' and ', ' ')
    return {token for token in normalized.split() if token}

def _batches_overlap(left, right):
    left_tokens = _batch_tokens(left)
    right_tokens = _batch_tokens(right)
    if not left_tokens or not right_tokens:
        return True
    return bool(left_tokens.intersection(right_tokens))

def _same_physical_room(left, right):
    if not left or not right:
        return False
    if int(left.id) == int(right.id):
        return True
    return _normalize(left.name) == _normalize(right.name) and _normalize(left.room_type) == _normalize(right.room_type)

def _student_group_conflict_reason(
    db_session,
    schedule_type,
    subject_id,
    semester,
    section,
    batch,
    day,
    start_slot,
    duration,
    academic_year,
    exclude_schedule_id=None
):
    if not schedule_type or semester in (None, '') or not section:
        return None

    candidate_department_id = _department_id_for_subject(db_session, subject_id)
    existing_query = db_session.query(Schedule).filter(
        Schedule.day == day,
        Schedule.academic_year == academic_year,
        Schedule.slot_index <= int(start_slot) + int(duration) - 1,
        Schedule.slot_index + Schedule.duration_slots > int(start_slot),
        Schedule.semester == int(semester)
    )
    if exclude_schedule_id:
        existing_query = existing_query.filter(Schedule.id != exclude_schedule_id)

    for schedule in existing_query.all():
        if _schedule_department_id(schedule) != candidate_department_id:
            continue
        if _normalize(schedule.section) != _normalize(section):
            continue
        if schedule.schedule_type == 'Theory' or schedule_type == 'Theory':
            return 'Student section already has a class or lab for this time slot.'
        if _batches_overlap(schedule.batch, batch):
            return 'Student batch already has a lab for this time slot.'
    return None

def _faculty_ids(faculty_id, asst_faculty_id=None):
    ids = []
    for value in (faculty_id, asst_faculty_id):
        if value in (None, ''):
            continue
        next_id = int(value)
        if next_id not in ids:
            ids.append(next_id)
    return ids

def _department_id_for_subject(db_session, subject_id):
    subject = db_session.get(Subject, int(subject_id)) if subject_id else None
    return subject.department_id if subject else None

def _schedule_department_id(schedule):
    return schedule.subject.department_id if schedule.subject else None

def _has_too_many_continuous_slots(occupied_slots, candidate_slots):
    streak = 0
    current_run = []
    for slot in range(SLOTS_PER_DAY):
        if slot in occupied_slots:
            streak += 1
            current_run.append(slot)
            if streak > MAX_CONTINUOUS_TEACHING_SLOTS:
                if set(current_run).issubset(candidate_slots):
                    continue
                return True
        else:
            streak = 0
            current_run = []
    return False

def schedule_conflict_reason(
    db_session,
    subject_id,
    faculty_id,
    room_id,
    day,
    start_slot,
    duration,
    academic_year='2025-26',
    asst_faculty_id=None,
    exclude_schedule_id=None,
    schedule_type=None,
    semester=None,
    section=None,
    batch=None
):
    start_slot = int(start_slot)
    duration = int(duration)
    room_id = int(room_id)
    faculty_ids = _faculty_ids(faculty_id, asst_faculty_id)
    candidate_department_id = _department_id_for_subject(db_session, subject_id)
    candidate_slots = set(range(start_slot, start_slot + duration))
    candidate_room = db_session.get(Room, room_id)

    if start_slot < 0 or start_slot + duration > SLOTS_PER_DAY:
        return 'Selected time slot is outside the timetable day.'
    if day == 'Saturday' and start_slot + duration > 5:
        return 'Saturday is a half day, so this slot is not allowed.'

    for slot in candidate_slots:
        for current_faculty_id in faculty_ids:
            not_avail = db_session.query(FacultyAvailability).filter(
                FacultyAvailability.faculty_id == current_faculty_id,
                FacultyAvailability.day == day,
                FacultyAvailability.time_slot == str(slot),
                FacultyAvailability.is_available == False
            ).first()
            if not_avail:
                return 'Faculty is marked unavailable for this time slot.'

        existing_query = db_session.query(Schedule).filter(
            Schedule.day == day,
            Schedule.academic_year == academic_year,
            Schedule.slot_index <= slot,
            Schedule.slot_index + Schedule.duration_slots > slot
        )
        if exclude_schedule_id:
            existing_query = existing_query.filter(Schedule.id != exclude_schedule_id)

        for existing in existing_query.all():
            if _same_physical_room(existing.room, candidate_room):
                return 'Room is already booked for this time slot.'
            if existing.faculty_id in faculty_ids or existing.asst_faculty_id in faculty_ids:
                return 'Faculty is already booked for this time slot.'

    student_conflict = _student_group_conflict_reason(
        db_session,
        schedule_type,
        subject_id,
        semester,
        section,
        batch,
        day,
        start_slot,
        duration,
        academic_year,
        exclude_schedule_id
    )
    if student_conflict:
        return student_conflict

    for current_faculty_id in faculty_ids:
        existing_query = db_session.query(Schedule).filter(
            Schedule.day == day,
            Schedule.academic_year == academic_year
        ).filter(or_(
            Schedule.faculty_id == current_faculty_id,
            Schedule.asst_faculty_id == current_faculty_id
        ))
        if exclude_schedule_id:
            existing_query = existing_query.filter(Schedule.id != exclude_schedule_id)
        existing_schedules = existing_query.all()

        occupied_slots = set(candidate_slots)
        for schedule in existing_schedules:
            occupied_slots.update(range(schedule.slot_index, schedule.slot_index + schedule.duration_slots))
        if _has_too_many_continuous_slots(occupied_slots, candidate_slots):
            return 'Faculty cannot be given extra back-to-back classes beyond 2 time slots.'

        for schedule in existing_schedules:
            schedule_end = schedule.slot_index + schedule.duration_slots
            candidate_end = start_slot + duration
            is_adjacent = schedule_end == start_slot or candidate_end == schedule.slot_index
            if is_adjacent and _schedule_department_id(schedule) != candidate_department_id:
                return 'Faculty needs a free gap before moving to another department.'

    return None

def check_conflict(
    db_session,
    faculty_id,
    room_id,
    day,
    start_slot,
    duration,
    subject_id=None,
    academic_year='2025-26',
    asst_faculty_id=None,
    exclude_schedule_id=None,
    schedule_type=None,
    semester=None,
    section=None,
    batch=None
):
    return schedule_conflict_reason(
        db_session,
        subject_id,
        faculty_id,
        room_id,
        day,
        start_slot,
        duration,
        academic_year,
        asst_faculty_id,
        exclude_schedule_id,
        schedule_type,
        semester,
        section,
        batch
    ) is not None

def _lab_base_duration(lab):
    return 3 if lab.subject and lab.subject.subject_type == 'Core Lab' else 2

def _valid_lab_starts(duration):
    return LAB_START_ORDER.get(int(duration), list(range(SLOTS_PER_DAY)))

def _valid_theory_slots():
    return THEORY_SLOT_ORDER

def _missing_item(requirement_type, req, required, scheduled, reason='No valid free slot found.'):
    subject = req.subject
    return {
        'type': requirement_type,
        'subject_code': subject.code if subject else '',
        'subject': subject.name if subject else '',
        'semester': req.semester,
        'section': req.section,
        'batch': getattr(req, 'batch', None),
        'required': int(required),
        'scheduled': int(scheduled),
        'missing': max(0, int(required) - int(scheduled)),
        'reason': reason
    }

def generate_timetable(db, academic_year='2025-26', mode='full', department_id=None):
    generated = {
        'labs': 0,
        'lab_slots': 0,
        'theory': 0,
        'theory_slots': 0,
        'missing': []
    }
    subject_ids = None
    if department_id:
        subject_ids = [
            subject.id
            for subject in db.session.query(Subject.id).filter(Subject.department_id == int(department_id)).all()
        ]
        if not subject_ids:
            db.session.commit()
            return generated

    # Step 1: Clear existing generated schedules for the selected mode/year.
    # Requirement data is never deleted here.
    schedule_query = db.session.query(Schedule).filter(Schedule.academic_year == academic_year)
    if subject_ids is not None:
        schedule_query = schedule_query.filter(Schedule.subject_id.in_(subject_ids))
    if mode == 'lab':
        schedule_query = schedule_query.filter(Schedule.schedule_type == 'Lab')
    elif mode == 'theory':
        schedule_query = schedule_query.filter(Schedule.schedule_type == 'Theory')
    schedule_query.delete(synchronize_session=False)
    
    # Step 2: Generate Lab Timetable (Priority)
    lab_query = db.session.query(LabRequirement).filter_by(academic_year=academic_year)
    if subject_ids is not None:
        lab_query = lab_query.filter(LabRequirement.subject_id.in_(subject_ids))
    labs = lab_query.all() if mode in ('full', 'lab') else []
    # Sort labs by department priority (assuming subject->dept priority is fetched, here just random or by ID)
    
    for lab in labs:
        hours_needed = max(0, int(lab.weekly_hours))
        hours_scheduled = 0
        base_duration = _lab_base_duration(lab)

        for day in DAYS:
            if hours_scheduled >= hours_needed:
                break
            remaining = hours_needed - hours_scheduled
            duration = min(base_duration, remaining)
            if duration <= 0:
                break

            for slot in _valid_lab_starts(duration):
                if slot + duration > SLOTS_PER_DAY:
                    continue
                if day == 'Saturday' and slot + duration > 5:
                    continue

                if not check_conflict(
                    db.session,
                    lab.main_faculty_id,
                    lab.room_id,
                    day,
                    slot,
                    duration,
                    lab.subject_id,
                    academic_year,
                    lab.asst_faculty_id,
                    schedule_type='Lab',
                    semester=lab.semester,
                    section=lab.section,
                    batch=lab.batch
                ):
                    s = Schedule(
                        schedule_type='Lab',
                        subject_id=lab.subject_id,
                        faculty_id=lab.main_faculty_id,
                        asst_faculty_id=lab.asst_faculty_id,
                        room_id=lab.room_id,
                        day=day,
                        slot_index=slot,
                        duration_slots=duration,
                        semester=lab.semester,
                        section=lab.section,
                        batch=lab.batch,
                        academic_year=academic_year
                    )
                    db.session.add(s)
                    db.session.flush()
                    hours_scheduled += duration
                    generated['labs'] += 1
                    generated['lab_slots'] += duration
                    break

        if hours_scheduled < hours_needed:
            generated['missing'].append(_missing_item('Lab', lab, hours_needed, hours_scheduled))

    # Step 3: Generate Theory Timetable
    theory_query = db.session.query(TheoryRequirement).filter_by(academic_year=academic_year)
    if subject_ids is not None:
        theory_query = theory_query.filter(TheoryRequirement.subject_id.in_(subject_ids))
    theories = theory_query.all() if mode in ('full', 'theory') else []
    for theory in theories:
        hours_needed = int(theory.weekly_hours)
        hours_scheduled = 0
        
        for day in DAYS:
            if hours_scheduled >= hours_needed: break
            sessions_today = 0
            for slot in _valid_theory_slots():
                if hours_scheduled >= hours_needed or sessions_today >= MAX_THEORY_SESSIONS_PER_SUBJECT_PER_DAY:
                    break
                if day == 'Saturday' and slot >= 5: continue # Half day constraint
                
                if not check_conflict(
                    db.session,
                    theory.faculty_id,
                    theory.room_id,
                    day,
                    slot,
                    1,
                    theory.subject_id,
                    academic_year,
                    schedule_type='Theory',
                    semester=theory.semester,
                    section=theory.section
                ):
                    s = Schedule(
                        schedule_type='Theory',
                        subject_id=theory.subject_id,
                        faculty_id=theory.faculty_id,
                        room_id=theory.room_id,
                        day=day,
                        slot_index=slot,
                        duration_slots=1,
                        semester=theory.semester,
                        section=theory.section,
                        academic_year=academic_year
                    )
                    db.session.add(s)
                    db.session.flush()
                    hours_scheduled += 1
                    generated['theory'] += 1
                    generated['theory_slots'] += 1
                    sessions_today += 1

        if hours_scheduled < hours_needed:
            generated['missing'].append(_missing_item('Theory', theory, hours_needed, hours_scheduled))

    db.session.commit()
    print("Timetable generation complete.")
    return generated
