from sqlalchemy import or_
from models import Schedule, LabRequirement, TheoryRequirement, FacultyAvailability, Subject
import random

DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
SLOTS_PER_DAY = 8
MAX_CONTINUOUS_TEACHING_SLOTS = 2

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
    exclude_schedule_id=None
):
    start_slot = int(start_slot)
    duration = int(duration)
    room_id = int(room_id)
    faculty_ids = _faculty_ids(faculty_id, asst_faculty_id)
    candidate_department_id = _department_id_for_subject(db_session, subject_id)
    candidate_slots = set(range(start_slot, start_slot + duration))

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

        faculty_busy_filters = []
        for current_faculty_id in faculty_ids:
            faculty_busy_filters.extend([
                Schedule.faculty_id == current_faculty_id,
                Schedule.asst_faculty_id == current_faculty_id
            ])
        busy_filters = [Schedule.room_id == room_id] + faculty_busy_filters
        existing_query = db_session.query(Schedule).filter(
            Schedule.day == day,
            Schedule.academic_year == academic_year,
            Schedule.slot_index <= slot,
            Schedule.slot_index + Schedule.duration_slots > slot
        )
        if exclude_schedule_id:
            existing_query = existing_query.filter(Schedule.id != exclude_schedule_id)
        existing = existing_query.filter(or_(*busy_filters)).first()
        if existing:
            if existing.room_id == room_id:
                return 'Room is already booked for this time slot.'
            return 'Faculty is already booked for this time slot.'

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
    exclude_schedule_id=None
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
        exclude_schedule_id
    ) is not None

def generate_timetable(db, academic_year='2025-26', mode='full', department_id=None):
    generated = {'labs': 0, 'theory': 0}
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
        duration = 3 if lab.subject.subject_type == 'Core Lab' else 2
        scheduled = False
        
        # Try finding a slot
        for day in DAYS:
            if scheduled: break
            # Valid start slots: must fit in a single half (before short break, before lunch, after lunch)
            # Actually, to keep it simple, just ensure start_slot + duration <= SLOTS_PER_DAY
            # For 3 slots: [0, 1, 2], [4, 5, 6] etc.
            # Avoid crossing breaks if possible, but let's just use strict indices for now.
            valid_starts = [0, 1, 2, 3, 4, 5, 6, 7]
            if duration == 2:
                valid_starts = [0, 1, 3, 5, 6] # avoid breaking across breaks
            elif duration == 3:
                valid_starts = [0, 5]
                
            for slot in valid_starts:
                if slot + duration > SLOTS_PER_DAY: continue
                if day == 'Saturday' and slot + duration > 5: continue # Half day constraint
                
                if not check_conflict(
                    db.session,
                    lab.main_faculty_id,
                    lab.room_id,
                    day,
                    slot,
                    duration,
                    lab.subject_id,
                    academic_year,
                    lab.asst_faculty_id
                ):
                    # Found a slot!
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
                    generated['labs'] += 1
                    scheduled = True
                    break

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
            # Try to place 1 hour per day
            placed_today = False
            for slot in range(SLOTS_PER_DAY):
                if day == 'Saturday' and slot >= 5: continue # Half day constraint
                
                if not check_conflict(
                    db.session,
                    theory.faculty_id,
                    theory.room_id,
                    day,
                    slot,
                    1,
                    theory.subject_id,
                    academic_year
                ):
                    # Check if student group is free
                    student_conflict = db.session.query(Schedule).filter(
                        Schedule.day == day,
                        Schedule.slot_index <= slot,
                        Schedule.slot_index + Schedule.duration_slots > slot,
                        Schedule.semester == theory.semester,
                        Schedule.section == theory.section
                    ).first()
                    
                    if not student_conflict:
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
                        hours_scheduled += 1
                        generated['theory'] += 1
                        placed_today = True
                        break # Only 1 theory class of same subject per day

    db.session.commit()
    print("Timetable generation complete.")
    return generated
