from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

db = SQLAlchemy()

class User(db.Model):
    __tablename__ = 'users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False) # Admin, Faculty, Student
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=True)

    department = db.relationship('Department')
    faculty = db.relationship('Faculty', foreign_keys=[faculty_id])

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Department(db.Model):
    __tablename__ = 'departments'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    priority = db.Column(db.Integer, default=1)

class Faculty(db.Model):
    __tablename__ = 'faculty'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    home_department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False)
    max_hours_per_day = db.Column(db.Integer, default=6)
    capability = db.Column(db.String(20), default='Both') # Theory, Lab, Both
    is_interdepartmental = db.Column(db.Boolean, default=False)

    home_department = db.relationship('Department', backref='faculty_members')

class FacultyDepartmentMapping(db.Model):
    __tablename__ = 'faculty_department_mapping'
    id = db.Column(db.Integer, primary_key=True)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=False)
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False)

class FacultyAvailability(db.Model):
    __tablename__ = 'faculty_availability'
    id = db.Column(db.Integer, primary_key=True)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=False)
    day = db.Column(db.String(20), nullable=False) # Monday, Tuesday, etc.
    time_slot = db.Column(db.String(50), nullable=False) # e.g., 'Morning', '8:30-9:30'
    is_available = db.Column(db.Boolean, default=True)

class Subject(db.Model):
    __tablename__ = 'subjects'
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    subject_type = db.Column(db.String(20), nullable=False) # Theory, Core Lab, Integrated Lab
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=False)

class Room(db.Model):
    __tablename__ = 'rooms'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    room_type = db.Column(db.String(20), nullable=False) # Lab, Theory
    department_id = db.Column(db.Integer, db.ForeignKey('departments.id'), nullable=True)
    department = db.relationship('Department')

class LabRequirement(db.Model):
    __tablename__ = 'lab_requirements'
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    main_faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=False)
    asst_faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=True)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    semester = db.Column(db.Integer, nullable=False)
    section = db.Column(db.String(10), nullable=False)
    batch = db.Column(db.String(10), nullable=False)
    weekly_hours = db.Column(db.Integer, nullable=False)
    academic_year = db.Column(db.String(20), nullable=False, default='2025-26')
    
    subject = db.relationship('Subject', backref='lab_requirements')
    main_faculty = db.relationship('Faculty', foreign_keys=[main_faculty_id])
    asst_faculty = db.relationship('Faculty', foreign_keys=[asst_faculty_id])
    room = db.relationship('Room')

class TheoryRequirement(db.Model):
    __tablename__ = 'theory_requirements'
    id = db.Column(db.Integer, primary_key=True)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    semester = db.Column(db.Integer, nullable=False)
    section = db.Column(db.String(10), nullable=False)
    weekly_hours = db.Column(db.Integer, nullable=False)
    academic_year = db.Column(db.String(20), nullable=False, default='2025-26')
    
    subject = db.relationship('Subject', backref='theory_requirements')
    faculty = db.relationship('Faculty', foreign_keys=[faculty_id])
    room = db.relationship('Room')

class Schedule(db.Model):
    __tablename__ = 'schedules'
    id = db.Column(db.Integer, primary_key=True)
    schedule_type = db.Column(db.String(10), nullable=False) # 'Lab', 'Theory'
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=False)
    asst_faculty_id = db.Column(db.Integer, db.ForeignKey('faculty.id'), nullable=True)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    day = db.Column(db.String(20), nullable=False)
    slot_index = db.Column(db.Integer, nullable=False) # 0 to N slots per day
    duration_slots = db.Column(db.Integer, default=1) # 1 for theory, 2 for integrated, 3 for core
    semester = db.Column(db.Integer, nullable=False)
    section = db.Column(db.String(10), nullable=False)
    batch = db.Column(db.String(10), nullable=True)
    academic_year = db.Column(db.String(20), nullable=False, default='2025-26')
    
    subject = db.relationship('Subject')
    faculty = db.relationship('Faculty', foreign_keys=[faculty_id])
    room = db.relationship('Room')
