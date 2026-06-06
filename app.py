import os
import shutil
import sqlite3
from flask import Flask, request, jsonify, render_template
from flask_jwt_extended import JWTManager, create_access_token, jwt_required, get_jwt_identity, get_jwt, verify_jwt_in_request
from flask_cors import CORS
from models import db, User, Department, Faculty, Subject, Room, LabRequirement, TheoryRequirement, Schedule, FacultyAvailability, FacultyDepartmentMapping
import pymysql
from sqlalchemy import func, or_, text, inspect
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

app = Flask(__name__)
CORS(app)

# Database Configuration - using PyMySQL for MySQL connection
# Create DB if it doesn't exist
try:
    conn = pymysql.connect(host='localhost', user='root', password='12345')
    cursor = conn.cursor()
    cursor.execute('CREATE DATABASE IF NOT EXISTS chrono_matrix')
    conn.commit()
    conn.close()
    app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:12345@localhost/chrono_matrix'
except Exception as e:
    print(f"MySQL connection failed: {e}. Falling back to SQLite.")
    sqlite_path = os.path.join(app.root_path, 'chrono_matrix.db')
    legacy_sqlite_path = os.path.join(app.instance_path, 'chrono_matrix.db')

    def saved_data_count(path):
        if not os.path.exists(path):
            return -1
        try:
            with sqlite3.connect(path) as conn:
                cursor = conn.cursor()
                total = 0
                for table in ('lab_requirements', 'theory_requirements', 'schedules'):
                    try:
                        total += cursor.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                    except sqlite3.Error:
                        pass
                return total
        except sqlite3.Error:
            return -1

    if saved_data_count(sqlite_path) == 0 and saved_data_count(legacy_sqlite_path) > 0:
        shutil.copy2(legacy_sqlite_path, sqlite_path)
    app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{sqlite_path}"

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['JWT_SECRET_KEY'] = 'chrono_matrix_super_secret_key_2026'
app.config['JWT_VERIFY_SUB'] = False

db.init_app(app)
jwt = JWTManager(app)


def current_identity():
    try:
        verify_jwt_in_request(optional=True)
        claims = get_jwt() or {}
        if isinstance(claims.get('user'), dict):
            return claims['user']
        identity = get_jwt_identity()
        if identity:
            user = User.query.get(int(identity))
            return user_payload(user) if user else {}
        return {}
    except Exception:
        return {}


def user_payload(user):
    department = user.department if getattr(user, 'department_id', None) else None
    faculty = user.faculty if getattr(user, 'faculty_id', None) else None
    return {
        "id": user.id,
        "username": user.username,
        "role": user.role,
        "department_id": user.department_id,
        "department": department.name if department else None,
        "faculty_id": user.faculty_id,
        "faculty": faculty.name if faculty else None,
        "is_global_admin": user.role == 'Admin' and not user.department_id
    }


def current_admin_department_id():
    identity = current_identity()
    department_id = identity.get('department_id')
    if identity.get('role') == 'Admin' and department_id:
        return int(department_id)
    return None


def current_faculty_id():
    identity = current_identity()
    faculty_id = identity.get('faculty_id')
    if identity.get('role') == 'Faculty' and faculty_id:
        return int(faculty_id)
    return None


def current_faculty_department_id():
    identity = current_identity()
    department_id = identity.get('department_id')
    if identity.get('role') == 'Faculty' and department_id:
        return int(department_id)
    return None


def current_user_is_global_admin():
    identity = current_identity()
    return identity.get('role') == 'Admin' and not identity.get('department_id')


def s_admin_exists():
    return User.query.filter(User.role == 'Admin', User.department_id.is_(None)).first() is not None


def require_global_admin():
    if current_user_is_global_admin():
        return None
    return jsonify({"error": "Only the main admin can manage admin accounts and departments."}), 403


def database_integrity_message(error):
    details = str(getattr(error, 'orig', error)).lower()
    if 'users.username' in details:
        return "Username already exists. Please use another username."
    if 'subjects.code' in details:
        return "Subject code already exists. Please use Alter or enter a different code."
    if 'departments.name' in details:
        return "Department already exists."
    if 'rooms.name' in details:
        return "Room already exists."
    return "This record already exists. Please check for duplicate data."


@app.errorhandler(IntegrityError)
def handle_integrity_error(error):
    db.session.rollback()
    return jsonify({"error": database_integrity_message(error)}), 400


@app.errorhandler(SQLAlchemyError)
def handle_database_error(error):
    db.session.rollback()
    return jsonify({"error": "Database error. Please try again."}), 500


def scoped_department_id(requested_department_id=None):
    admin_department_id = current_admin_department_id()
    if admin_department_id:
        return admin_department_id
    faculty_department_id = current_faculty_department_id()
    if faculty_department_id:
        return faculty_department_id
    if requested_department_id and requested_department_id != 'default':
        return int(requested_department_id)
    return None


def department_allowed(department_id):
    admin_department_id = current_admin_department_id()
    if admin_department_id:
        return int(department_id) == admin_department_id
    faculty_department_id = current_faculty_department_id()
    if faculty_department_id:
        return int(department_id) == faculty_department_id
    return True


def scoped_access_error():
    return jsonify({"error": "This admin can only manage their assigned department."}), 403


def subject_allowed(subject_id):
    subject = Subject.query.get(subject_id)
    return subject and department_allowed(subject.department_id)


def faculty_allowed(faculty_id):
    faculty = Faculty.query.get(faculty_id)
    logged_faculty_id = current_faculty_id()
    if logged_faculty_id:
        return faculty and int(faculty.id) == logged_faculty_id
    admin_department_id = current_admin_department_id()
    if admin_department_id:
        return faculty_can_teach_department(faculty, admin_department_id)
    return faculty and department_allowed(faculty.home_department_id)


def room_allowed(room_id):
    room = Room.query.get(room_id)
    if not room:
        return False
    admin_department_id = current_admin_department_id()
    return not admin_department_id or room.department_id in (None, admin_department_id)


def classroom_allowed_for_subject(room_id, subject_id):
    room = Room.query.get(room_id)
    subject = Subject.query.get(subject_id)
    if not room or not subject:
        return False
    return (
        room.room_type == 'Theory' and
        room.department_id is not None and
        int(room.department_id) == int(subject.department_id) and
        department_allowed(subject.department_id)
    )


def requirement_allowed(req):
    return req and req.subject and department_allowed(req.subject.department_id)


def schedule_allowed(schedule):
    return schedule and schedule.subject and department_allowed(schedule.subject.department_id)


def truthy(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def mapped_department_ids_for_faculty(faculty_id):
    return [
        mapping.department_id
        for mapping in FacultyDepartmentMapping.query.filter_by(faculty_id=faculty_id).all()
    ]


def faculty_mapped_to_department(faculty_id, department_id):
    if not faculty_id or not department_id:
        return False
    return FacultyDepartmentMapping.query.filter_by(
        faculty_id=int(faculty_id),
        department_id=int(department_id)
    ).first() is not None


def faculty_can_teach_department(faculty, department_id):
    if not faculty or not department_id:
        return False
    department_id = int(department_id)
    return (
        int(faculty.home_department_id) == department_id or
        faculty_mapped_to_department(faculty.id, department_id)
    )


def faculty_json(faculty):
    login_user = User.query.filter_by(role='Faculty', faculty_id=faculty.id).first()
    teaching_department_ids = mapped_department_ids_for_faculty(faculty.id)
    return {
        "id": faculty.id,
        "name": faculty.name,
        "department": faculty.home_department.name if faculty.home_department else "Unknown",
        "department_id": faculty.home_department_id,
        "home_department_id": faculty.home_department_id,
        "is_interdepartmental": bool(getattr(faculty, 'is_interdepartmental', False)),
        "teaching_department_ids": teaching_department_ids,
        "login_user_id": login_user.id if login_user else None,
        "login_username": login_user.username if login_user else None
    }


def assistant_faculty_name(schedule):
    if not getattr(schedule, 'asst_faculty_id', None):
        return ""
    assistant = Faculty.query.get(schedule.asst_faculty_id)
    return assistant.name if assistant else ""


def schedule_faculty_names(schedule):
    names = []
    if getattr(schedule, 'faculty', None):
        names.append(schedule.faculty.name)
    assistant_name = assistant_faculty_name(schedule)
    if assistant_name and assistant_name not in names:
        names.append(assistant_name)
    return names


def schedule_faculty_display(schedule):
    return " / ".join(schedule_faculty_names(schedule))


def interdepartment_mapping_json(mapping):
    faculty = Faculty.query.get(mapping.faculty_id)
    department = Department.query.get(mapping.department_id)
    home_department = Department.query.get(faculty.home_department_id) if faculty else None
    return {
        "id": mapping.id,
        "faculty_id": mapping.faculty_id,
        "faculty_name": faculty.name if faculty else "Unknown Faculty",
        "home_department_id": faculty.home_department_id if faculty else None,
        "home_department_name": home_department.name if home_department else "Unknown",
        "department_id": mapping.department_id,
        "department_name": department.name if department else "Unknown"
    }


def interdepartment_mapping_allowed(mapping):
    if current_user_is_global_admin():
        return True
    admin_department_id = current_admin_department_id()
    if not admin_department_id:
        return False
    faculty = Faculty.query.get(mapping.faculty_id)
    return (
        int(mapping.department_id) == admin_department_id or
        (faculty and int(faculty.home_department_id) == admin_department_id)
    )


def remove_legacy_room_name_unique_constraint():
    if db.engine.dialect.name != 'sqlite':
        return
    inspector = inspect(db.engine)
    unique_constraints = inspector.get_unique_constraints('rooms')
    has_legacy_unique = any(constraint.get('column_names') == ['name'] for constraint in unique_constraints)
    if not has_legacy_unique:
        return

    with db.engine.begin() as connection:
        connection.execute(text('PRAGMA foreign_keys=OFF'))
        connection.execute(text('DROP TABLE IF EXISTS rooms_new'))
        connection.execute(text('''
            CREATE TABLE rooms_new (
                id INTEGER NOT NULL,
                name VARCHAR(50) NOT NULL,
                room_type VARCHAR(20) NOT NULL,
                department_id INTEGER,
                PRIMARY KEY (id),
                FOREIGN KEY(department_id) REFERENCES departments (id)
            )
        '''))
        connection.execute(text('''
            INSERT INTO rooms_new (id, name, room_type, department_id)
            SELECT id, name, room_type, department_id FROM rooms
        '''))
        connection.execute(text('DROP TABLE rooms'))
        connection.execute(text('ALTER TABLE rooms_new RENAME TO rooms'))
        connection.execute(text('PRAGMA foreign_keys=ON'))


def remove_legacy_subject_code_unique_constraint():
    if db.engine.dialect.name != 'sqlite':
        return
    inspector = inspect(db.engine)
    unique_constraints = inspector.get_unique_constraints('subjects')
    has_legacy_unique = any(constraint.get('column_names') == ['code'] for constraint in unique_constraints)
    if not has_legacy_unique:
        return

    with db.engine.begin() as connection:
        connection.execute(text('PRAGMA foreign_keys=OFF'))
        connection.execute(text('DROP TABLE IF EXISTS subjects_new'))
        connection.execute(text('''
            CREATE TABLE subjects_new (
                id INTEGER NOT NULL,
                code VARCHAR(20) NOT NULL,
                name VARCHAR(100) NOT NULL,
                subject_type VARCHAR(20) NOT NULL,
                department_id INTEGER NOT NULL,
                PRIMARY KEY (id),
                FOREIGN KEY(department_id) REFERENCES departments (id)
            )
        '''))
        connection.execute(text('''
            INSERT INTO subjects_new (id, code, name, subject_type, department_id)
            SELECT id, code, name, subject_type, department_id FROM subjects
        '''))
        connection.execute(text('DROP TABLE subjects'))
        connection.execute(text('ALTER TABLE subjects_new RENAME TO subjects'))
        connection.execute(text('PRAGMA foreign_keys=ON'))


def ensure_runtime_schema():
    db.create_all()
    inspector = inspect(db.engine)
    user_columns = {column['name'] for column in inspector.get_columns('users')}
    if 'department_id' not in user_columns:
        db.session.execute(text('ALTER TABLE users ADD COLUMN department_id INTEGER'))
        db.session.commit()
    if 'faculty_id' not in user_columns:
        db.session.execute(text('ALTER TABLE users ADD COLUMN faculty_id INTEGER'))
        db.session.commit()
    faculty_columns = {column['name'] for column in inspector.get_columns('faculty')}
    if 'is_interdepartmental' not in faculty_columns:
        db.session.execute(text('ALTER TABLE faculty ADD COLUMN is_interdepartmental BOOLEAN DEFAULT 0'))
        db.session.commit()
    remove_legacy_room_name_unique_constraint()
    remove_legacy_subject_code_unique_constraint()
    default_accounts = [
        ('faculty', 'Faculty', 'faculty123'),
        ('student', 'Student', 'student123')
    ]
    for username, role, password in default_accounts:
        if not User.query.filter_by(username=username).first():
            user = User(username=username, role=role)
            user.set_password(password)
            db.session.add(user)
    db.session.commit()

# ---------------- Routes: HTML Templates ----------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')

@app.route('/super-admin')
def super_admin():
    return render_template('super_admin.html')

@app.route('/department-admin-access')
def department_admin_access():
    return render_template('department_admin_access.html')

@app.route('/lab-lobby')
def lab_lobby():
    return render_template('lab_lobby.html')

@app.route('/faculty-availability')
def faculty_availability():
    return render_template('faculty_availability.html')

@app.route('/theory-lobby')
def theory_lobby():
    return render_template('theory_lobby.html')

@app.route('/timetable')
def timetable():
    return render_template('timetable.html')

# ---------------- Routes: Authentication ----------------
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    role = data.get('role')
    department_id = data.get('department_id')
    faculty_id = data.get('faculty_id')
    selected_department = None
    selected_faculty = None

    if role == 'Student':
        if not department_id:
            return jsonify({"msg": "Please select department for student timetable"}), 400
        try:
            department_id = int(department_id)
        except (TypeError, ValueError):
            return jsonify({"msg": "Invalid department"}), 400
        department = Department.query.get(department_id)
        if not department:
            return jsonify({"msg": "Department not found"}), 404
        payload = {
            "id": None,
            "username": "Student",
            "role": "Student",
            "department_id": department.id,
            "department": department.name,
            "faculty_id": None,
            "faculty": None,
            "is_global_admin": False
        }
        access_token = create_access_token(identity=f"student:{department.id}", additional_claims={'user': payload})
        return jsonify(access_token=access_token, user=payload), 200

    if role == 'SAdmin':
        user = User.query.filter_by(username=username, role='Admin', department_id=None).first()
    elif role == 'Admin':
        if not department_id:
            return jsonify({"msg": "Please select department for admin login"}), 400
        user = User.query.filter_by(username=username, role='Admin', department_id=department_id).first()
    elif role == 'Faculty':
        user = User.query.filter_by(username=username, role='Faculty').first()
        if department_id or faculty_id:
            if not department_id:
                return jsonify({"msg": "Please select department for faculty login"}), 400
            if not faculty_id:
                return jsonify({"msg": "Please select faculty name"}), 400
            try:
                department_id = int(department_id)
                faculty_id = int(faculty_id)
            except (TypeError, ValueError):
                return jsonify({"msg": "Invalid department or faculty"}), 400
            selected_department = Department.query.get(department_id)
            selected_faculty = Faculty.query.get(faculty_id)
            if not selected_department or not selected_faculty or not faculty_can_teach_department(selected_faculty, selected_department.id):
                return jsonify({"msg": "Faculty does not belong to the selected department"}), 400
    else:
        user = User.query.filter_by(username=username, role=role).first()
    
    if user and user.check_password(password):
        payload = user_payload(user)
        if role == 'Faculty':
            if not payload.get('faculty_id'):
                if not selected_department or not selected_faculty:
                    return jsonify({"msg": "Please select department and faculty name"}), 400
                payload.update({
                    "department_id": selected_department.id,
                    "department": selected_department.name,
                    "faculty_id": selected_faculty.id,
                    "faculty": selected_faculty.name
                })
        access_token = create_access_token(identity=str(user.id), additional_claims={'user': payload})
        return jsonify(access_token=access_token, user=payload), 200
    
    return jsonify({"msg": "Bad username or password"}), 401

@app.route('/api/s-admin/status', methods=['GET'])
def s_admin_status():
    ensure_runtime_schema()
    return jsonify({"exists": s_admin_exists()})

@app.route('/api/setup', methods=['POST'])
def setup():
    ensure_runtime_schema()
    if s_admin_exists():
        return jsonify({"error": "S Admin already exists. Please login with the existing password."}), 400

    data = request.json or {}
    username = str(data.get('username') or '').strip()
    password = str(data.get('password') or '')

    if not username or not password:
        return jsonify({"error": "S Admin username and password are required."}), 400
    if len(password) < 4:
        return jsonify({"error": "Password must be at least 4 characters."}), 400
    if User.query.filter_by(username=username).first():
        return jsonify({"error": "Username already exists."}), 400

    admin = User(username=username, role='Admin', department_id=None)
    admin.set_password(password)
    db.session.add(admin)
    db.session.commit()

    payload = user_payload(admin)
    access_token = create_access_token(identity=str(admin.id), additional_claims={'user': payload})
    return jsonify({
        "msg": "S Admin created successfully.",
        "access_token": access_token,
        "user": payload
    }), 201


@app.route('/api/admin-users', methods=['GET', 'POST'])
def admin_users():
    blocked = require_global_admin()
    if blocked:
        return blocked

    if request.method == 'POST':
        data = request.json or {}
        username = str(data.get('username') or '').strip()
        password = str(data.get('password') or '').strip()
        department_id = data.get('department_id')

        if not username or not password or not department_id:
            return jsonify({"error": "Username, password, and department are required."}), 400
        if User.query.filter_by(username=username).first():
            return jsonify({"error": "Username already exists."}), 400
        department = Department.query.get(department_id)
        if not department:
            return jsonify({"error": "Department not found."}), 404

        new_admin = User(username=username, role='Admin', department_id=department.id)
        new_admin.set_password(password)
        db.session.add(new_admin)
        db.session.commit()
        return jsonify({"msg": "Department admin created", "user": user_payload(new_admin)}), 201

    admins = User.query.filter_by(role='Admin').order_by(User.id).all()
    return jsonify([user_payload(admin) for admin in admins])


@app.route('/api/admin-users/<int:id>', methods=['DELETE'])
def delete_admin_user(id):
    blocked = require_global_admin()
    if blocked:
        return blocked
    identity = current_identity()
    user = User.query.get(id)
    if not user or user.role != 'Admin':
        return jsonify({"error": "Admin user not found."}), 404
    if user.id == identity.get('id') or user.username == 'admin':
        return jsonify({"error": "The main admin account cannot be deleted here."}), 400
    db.session.delete(user)
    db.session.commit()
    return jsonify({"msg": "Department admin deleted"})


@app.route('/api/admin-users/<int:id>/password', methods=['PUT'])
def change_department_admin_password(id):
    blocked = require_global_admin()
    if blocked:
        return blocked

    user = User.query.get(id)
    if not user or user.role != 'Admin' or not user.department_id:
        return jsonify({"error": "Department admin not found."}), 404

    data = request.json or {}
    new_password = str(data.get('new_password') or '')
    if len(new_password) < 4:
        return jsonify({"error": "New password must be at least 4 characters."}), 400

    user.set_password(new_password)
    db.session.commit()
    return jsonify({"msg": "Admin password updated."})


@app.route('/api/s-admin/password', methods=['PUT'])
def change_s_admin_password():
    blocked = require_global_admin()
    if blocked:
        return blocked

    data = request.json or {}
    current_password = str(data.get('current_password') or '')
    new_password = str(data.get('new_password') or '')

    if len(new_password) < 4:
        return jsonify({"error": "New password must be at least 4 characters."}), 400

    identity = current_identity()
    user = User.query.get(identity.get('id'))
    if not user or user.role != 'Admin' or user.department_id:
        return jsonify({"error": "S Admin account not found."}), 404
    if not user.check_password(current_password):
        return jsonify({"error": "Current password is wrong."}), 400

    user.set_password(new_password)
    db.session.commit()
    return jsonify({"msg": "S Admin password updated. Please login again."})


@app.route('/api/s-admin/department-session', methods=['POST'])
def s_admin_department_session():
    blocked = require_global_admin()
    if blocked:
        return blocked

    data = request.json or {}
    department_id = data.get('department_id')
    if not department_id:
        return jsonify({"error": "Select department to view dashboard."}), 400

    department = Department.query.get(department_id)
    if not department:
        return jsonify({"error": "Department not found."}), 404

    identity = current_identity()
    payload = {
        "id": identity.get("id"),
        "username": identity.get("username") or "S Admin",
        "role": "Admin",
        "department_id": department.id,
        "department": department.name,
        "faculty_id": None,
        "faculty": None,
        "is_global_admin": False,
        "acting_from_s_admin": True
    }
    access_token = create_access_token(
        identity=f"sadmin-dept:{department.id}",
        additional_claims={'user': payload}
    )
    return jsonify({
        "msg": f"Opening {department.name} dashboard.",
        "access_token": access_token,
        "user": payload
    }), 200

# ---------------- Routes: CRUD ----------------
@app.route('/api/departments', methods=['GET', 'POST'])
def handle_departments():
    if request.method == 'POST':
        if current_admin_department_id():
            return scoped_access_error()
        data = request.json
        existing = Department.query.filter_by(name=data['name']).first()
        if existing:
            return jsonify({"error": "Department already exists!"}), 400
        new_dept = Department(name=data['name'], priority=data.get('priority', 1))
        db.session.add(new_dept)
        db.session.commit()
        return jsonify({"msg": "Department added", "id": new_dept.id}), 201
    else:
        query = Department.query
        admin_department_id = current_admin_department_id()
        show_all_for_interdepartment = request.args.get('scope') == 'interdepartmental'
        if admin_department_id and not show_all_for_interdepartment:
            query = query.filter_by(id=admin_department_id)
        depts = query.all()
        return jsonify([{"id": d.id, "name": d.name, "priority": d.priority} for d in depts])

@app.route('/api/departments/<int:id>', methods=['PUT'])
def update_department(id):
    blocked = require_global_admin()
    if blocked:
        return blocked
    obj = Department.query.get(id)
    if not obj: return jsonify({'error': 'Not found'}), 404
    data = request.json
    obj.name = data.get('name', obj.name)
    obj.priority = data.get('priority', obj.priority)
    db.session.commit()
    return jsonify({'msg': 'Department updated'})

@app.route('/api/faculty', methods=['GET', 'POST'])
def handle_faculty():
    if request.method == 'POST':
        data = request.json or {}
        admin_department_id = current_admin_department_id()
        faculty_name = str(data.get('name') or '').strip()
        faculty_username = str(data.get('username') or '').strip()
        faculty_password = str(data.get('password') or '').strip()
        is_interdepartmental = truthy(data.get('is_interdepartmental'))
        if not faculty_name:
            return jsonify({"error": "Faculty name is required."}), 400
        if faculty_username or faculty_password:
            if not faculty_username or not faculty_password:
                return jsonify({"error": "Faculty username and password are required together."}), 400
            if len(faculty_password) < 4:
                return jsonify({"error": "Faculty password must be at least 4 characters."}), 400
            if User.query.filter(func.lower(User.username) == faculty_username.lower()).first():
                return jsonify({"error": "Username already exists."}), 400
        home_department_id = admin_department_id or data.get('home_department_id')
        if not home_department_id:
            return jsonify({"error": "Select department before saving faculty."}), 400
        if not department_allowed(home_department_id):
            return scoped_access_error()
        if not Department.query.get(home_department_id):
            return jsonify({"error": "Selected department was not found."}), 404
        existing = Faculty.query.filter(
            Faculty.home_department_id == home_department_id,
            func.lower(func.trim(Faculty.name)) == faculty_name.lower()
        ).first()
        if existing:
            return jsonify({"error": "Faculty already exists in this department."}), 400

        new_faculty = Faculty(
            name=faculty_name,
            home_department_id=home_department_id,
            max_hours_per_day=data.get('max_hours_per_day', 6),
            capability=data.get('capability', 'Both'),
            is_interdepartmental=is_interdepartmental
        )
        db.session.add(new_faculty)
        db.session.flush()
        if faculty_username:
            faculty_user = User(
                username=faculty_username,
                role='Faculty',
                department_id=home_department_id,
                faculty_id=new_faculty.id
            )
            faculty_user.set_password(faculty_password)
            db.session.add(faculty_user)
        db.session.commit()
        return jsonify({"msg": "Faculty added", "id": new_faculty.id}), 201
    else:
        query = Faculty.query
        scope = request.args.get('scope')
        logged_faculty_id = current_faculty_id()
        admin_department_id = current_admin_department_id()
        if scope == 'department':
            requested_department_id = request.args.get('department_id')
            if requested_department_id:
                query = query.filter_by(home_department_id=int(requested_department_id))
            else:
                query = query.filter(text('1 = 0'))
        elif scope == 'home':
            requested_department_id = request.args.get('department_id')
            home_department_id = admin_department_id or requested_department_id
            if home_department_id:
                if not department_allowed(home_department_id):
                    return scoped_access_error()
                query = query.filter_by(home_department_id=int(home_department_id))
        elif scope == 'interdepartmental':
            query = query.filter(Faculty.is_interdepartmental == True)
            if admin_department_id:
                query = query.filter_by(home_department_id=admin_department_id)
        elif logged_faculty_id:
            query = query.filter_by(id=logged_faculty_id)
        elif admin_department_id:
            mapped_faculty_ids = [
                mapping.faculty_id
                for mapping in FacultyDepartmentMapping.query.filter_by(department_id=admin_department_id).all()
            ]
            if mapped_faculty_ids:
                query = query.filter(or_(
                    Faculty.home_department_id == admin_department_id,
                    Faculty.id.in_(mapped_faculty_ids)
                ))
            else:
                query = query.filter_by(home_department_id=admin_department_id)
        faculty = query.order_by(Faculty.name.asc()).all()
        return jsonify([faculty_json(f) for f in faculty])


@app.route('/api/interdepartment-faculty', methods=['GET', 'POST'])
def interdepartment_faculty():
    admin_department_id = current_admin_department_id()
    requested_department_id = request.args.get('department_id')
    if request.method == 'POST':
        data = request.json or {}
        faculty_id = data.get('faculty_id')
        department_id = data.get('department_id')
        if not faculty_id or not department_id:
            return jsonify({"error": "Select faculty and department."}), 400
        faculty = Faculty.query.get(faculty_id)
        department = Department.query.get(department_id)
        if not faculty or not department:
            return jsonify({"error": "Faculty or department not found."}), 404
        if admin_department_id and int(department_id) != admin_department_id:
            return scoped_access_error()
        if int(faculty.home_department_id) == int(department_id):
            return jsonify({"error": "Faculty already belongs to this home department."}), 400
        existing = FacultyDepartmentMapping.query.filter_by(
            faculty_id=faculty.id,
            department_id=int(department_id)
        ).first()
        if existing:
            return jsonify({"error": "This faculty is already assigned to this department."}), 400

        faculty.is_interdepartmental = True
        mapping = FacultyDepartmentMapping(faculty_id=faculty.id, department_id=int(department_id))
        db.session.add(mapping)
        db.session.commit()
        return jsonify({"msg": "Interdepartment faculty assigned.", "mapping": interdepartment_mapping_json(mapping)}), 201

    query = FacultyDepartmentMapping.query
    if admin_department_id:
        owned_faculty_ids = [
            faculty.id
            for faculty in Faculty.query.filter_by(home_department_id=admin_department_id).all()
        ]
        if owned_faculty_ids:
            query = query.filter(or_(
                FacultyDepartmentMapping.department_id == admin_department_id,
                FacultyDepartmentMapping.faculty_id.in_(owned_faculty_ids)
            ))
        else:
            query = query.filter_by(department_id=admin_department_id)
    elif requested_department_id:
        query = query.filter_by(department_id=int(requested_department_id))
    mappings = query.order_by(FacultyDepartmentMapping.department_id.asc()).all()
    return jsonify([interdepartment_mapping_json(mapping) for mapping in mappings])


@app.route('/api/interdepartment-faculty/my', methods=['GET'])
def my_interdepartment_faculty():
    faculty_id = current_faculty_id()
    if not faculty_id:
        return jsonify([])
    mappings = FacultyDepartmentMapping.query.filter_by(faculty_id=faculty_id).all()
    return jsonify([interdepartment_mapping_json(mapping) for mapping in mappings])


@app.route('/api/interdepartment-faculty/<int:id>', methods=['DELETE'])
def delete_interdepartment_faculty(id):
    mapping = FacultyDepartmentMapping.query.get(id)
    if not mapping:
        return jsonify({"error": "Assignment not found."}), 404
    if not interdepartment_mapping_allowed(mapping):
        return scoped_access_error()
    db.session.delete(mapping)
    db.session.commit()
    return jsonify({"msg": "Interdepartment assignment removed."})

@app.route('/api/faculty/<int:id>/password', methods=['PUT'])
def change_faculty_password(id):
    obj = Faculty.query.get(id)
    if not obj:
        return jsonify({"error": "Faculty not found."}), 404
    if not department_allowed(obj.home_department_id):
        return scoped_access_error()

    data = request.json or {}
    username = str(data.get('username') or '').strip()
    new_password = str(data.get('new_password') or '').strip()
    if not username:
        return jsonify({"error": "Username is required."}), 400
    if len(new_password) < 4:
        return jsonify({"error": "New password must be at least 4 characters."}), 400

    login_user = User.query.filter_by(role='Faculty', faculty_id=obj.id).first()
    if not login_user:
        return jsonify({"error": "This faculty does not have a login account yet."}), 404
    if login_user.department_id and not department_allowed(login_user.department_id):
        return scoped_access_error()
    existing_username = User.query.filter(User.username == username, User.id != login_user.id).first()
    if existing_username:
        return jsonify({"error": "Username already exists."}), 400

    login_user.username = username
    login_user.set_password(new_password)
    db.session.commit()
    return jsonify({"msg": "Faculty login updated."})

@app.route('/api/faculty/<int:id>', methods=['PUT'])
def update_faculty(id):
    obj = Faculty.query.get(id)
    if not obj: return jsonify({'error': 'Not found'}), 404
    if not department_allowed(obj.home_department_id):
        return scoped_access_error()
    data = request.json
    obj.name = data.get('name', obj.name)
    if data.get('home_department_id'):
        if not department_allowed(data['home_department_id']):
            return scoped_access_error()
        obj.home_department_id = data['home_department_id']
    obj.max_hours_per_day = data.get('max_hours_per_day', obj.max_hours_per_day)
    obj.capability = data.get('capability', obj.capability)
    if 'is_interdepartmental' in data:
        obj.is_interdepartmental = truthy(data.get('is_interdepartmental'))
        if not obj.is_interdepartmental:
            FacultyDepartmentMapping.query.filter_by(faculty_id=obj.id).delete(synchronize_session=False)
    db.session.commit()
    return jsonify({'msg': 'Faculty updated'})

@app.route('/api/subjects', methods=['GET', 'POST'])
def handle_subjects():
    if request.method == 'POST':
        data = request.json or {}
        admin_department_id = current_admin_department_id()
        department_id = admin_department_id or data.get('department_id')
        code = str(data.get('code') or '').strip()
        name = str(data.get('name') or '').strip()
        subject_type = str(data.get('subject_type') or '').strip()
        if not department_id:
            return jsonify({"error": "Select department before saving subject."}), 400
        if not code or not name or not subject_type:
            return jsonify({"error": "Subject code, name, and type are required."}), 400
        if not department_allowed(department_id):
            return scoped_access_error()
        if not Department.query.get(department_id):
            return jsonify({"error": "Selected department was not found."}), 404
        duplicate = Subject.query.filter(
            Subject.department_id == department_id,
            Subject.subject_type == subject_type,
            func.lower(func.trim(Subject.code)) == code.lower()
        ).first()
        if duplicate:
            return jsonify({"error": "This subject code already exists for this type in this department."}), 400

        new_subject = Subject(
            code=code,
            name=name,
            subject_type=subject_type,
            department_id=department_id
        )
        db.session.add(new_subject)
        db.session.commit()
        return jsonify({"msg": "Subject added", "id": new_subject.id}), 201
    else:
        query = Subject.query
        admin_department_id = current_admin_department_id()
        if admin_department_id:
            query = query.filter_by(department_id=admin_department_id)
        subjects = query.all()
        return jsonify([{"id": s.id, "code": s.code, "name": s.name, "type": s.subject_type, "department_id": s.department_id} for s in subjects])

@app.route('/api/subjects/<int:id>', methods=['PUT'])
def update_subject(id):
    obj = Subject.query.get(id)
    if not obj: return jsonify({'error': 'Not found'}), 404
    if not department_allowed(obj.department_id):
        return scoped_access_error()
    data = request.json or {}
    next_code = str(data.get('code', obj.code) or '').strip()
    next_name = str(data.get('name', obj.name) or '').strip()
    next_subject_type = str(data.get('subject_type', obj.subject_type) or '').strip()
    next_department_id = data.get('department_id') or obj.department_id
    if not next_code or not next_name or not next_subject_type:
        return jsonify({"error": "Subject code, name, and type are required."}), 400
    if data.get('department_id'):
        if not department_allowed(next_department_id):
            return scoped_access_error()
        if not Department.query.get(next_department_id):
            return jsonify({"error": "Selected department was not found."}), 404
    duplicate = Subject.query.filter(
        Subject.id != obj.id,
        Subject.department_id == next_department_id,
        Subject.subject_type == next_subject_type,
        func.lower(func.trim(Subject.code)) == next_code.lower()
    ).first()
    if duplicate:
        return jsonify({"error": "This subject code already exists for this type in this department."}), 400
    obj.code = next_code
    obj.name = next_name
    obj.subject_type = next_subject_type
    obj.department_id = next_department_id
    db.session.commit()
    return jsonify({'msg': 'Subject updated'})

@app.route('/api/rooms', methods=['GET', 'POST'])
def handle_rooms():
    if request.method == 'POST':
        data = request.json or {}
        admin_department_id = current_admin_department_id()
        department_id = admin_department_id or data.get('department_id')
        room_name = str(data.get('name') or '').strip()
        room_type = str(data.get('room_type') or '').strip()

        if not room_name or not room_type:
            return jsonify({"error": "Room name and type are required."}), 400
        if department_id and not department_allowed(department_id):
            return scoped_access_error()

        duplicate_query = Room.query.filter(
            func.lower(func.trim(Room.name)) == room_name.lower(),
            Room.room_type == room_type
        )
        if room_type == 'Theory':
            if not department_id:
                return jsonify({"error": "Classrooms must belong to a department."}), 400
            duplicate_query = duplicate_query.filter(Room.department_id == department_id)
            duplicate_error = "This classroom already exists in this department."
        elif department_id:
            duplicate_query = duplicate_query.filter(Room.department_id == department_id)
            duplicate_error = "This lab room already exists in this department."
        else:
            duplicate_query = duplicate_query.filter(Room.department_id.is_(None))
            duplicate_error = "This room already exists."
        if duplicate_query.first():
            return jsonify({"error": duplicate_error}), 400

        new_room = Room(name=room_name, room_type=room_type, department_id=department_id)
        db.session.add(new_room)
        db.session.commit()
        return jsonify({"msg": "Room added", "id": new_room.id}), 201
    else:
        query = Room.query
        admin_department_id = current_admin_department_id()
        if admin_department_id:
            query = query.filter(or_(Room.department_id == admin_department_id, Room.department_id.is_(None)))
        rooms = query.all()
        return jsonify([{
            "id": r.id,
            "name": r.name,
            "type": r.room_type,
            "department_id": r.department_id,
            "department": r.department.name if r.department else "Shared"
        } for r in rooms])

@app.route('/api/rooms/<int:id>', methods=['PUT'])
def update_room(id):
    obj = Room.query.get(id)
    if not obj: return jsonify({'error': 'Not found'}), 404
    if not room_allowed(id):
        return scoped_access_error()
    data = request.json or {}
    next_name = str(data.get('name', obj.name) or '').strip()
    next_type = str(data.get('room_type', obj.room_type) or '').strip()
    admin_department_id = current_admin_department_id()
    next_department_id = admin_department_id or data.get('department_id', obj.department_id)
    if not next_name or not next_type:
        return jsonify({"error": "Room name and type are required."}), 400
    if next_department_id and not department_allowed(next_department_id):
        return scoped_access_error()

    duplicate_query = Room.query.filter(
        Room.id != obj.id,
        func.lower(func.trim(Room.name)) == next_name.lower(),
        Room.room_type == next_type
    )
    if next_type == 'Theory':
        if not next_department_id:
            return jsonify({"error": "Classrooms must belong to a department."}), 400
        duplicate_query = duplicate_query.filter(Room.department_id == next_department_id)
        duplicate_error = "This classroom already exists in this department."
    elif next_department_id:
        duplicate_query = duplicate_query.filter(Room.department_id == next_department_id)
        duplicate_error = "This lab room already exists in this department."
    else:
        duplicate_query = duplicate_query.filter(Room.department_id.is_(None))
        duplicate_error = "This room already exists."
    if duplicate_query.first():
        return jsonify({"error": duplicate_error}), 400

    obj.name = next_name
    obj.room_type = next_type
    obj.department_id = next_department_id
    db.session.commit()
    return jsonify({'msg': 'Room updated'})

@app.route('/api/lab-requirements', methods=['GET', 'POST'])
def handle_lab_requirements():
    if request.method == 'POST':
        data = request.json
        subject_id = int(data['subject_id'])
        main_faculty_id = int(data['main_faculty_id'])
        asst_faculty_id = int(data['asst_faculty_id']) if data.get('asst_faculty_id') else None
        room_id = int(data['room_id'])
        semester = int(data['semester'])
        section = str(data.get('section') or '').strip()
        batch = str(data.get('batch') or '').strip()
        weekly_hours = int(data['weekly_hours'])
        academic_year = str(data.get('academic_year') or '2025-26').strip()
        if not subject_allowed(subject_id) or not faculty_allowed(main_faculty_id) or (asst_faculty_id and not faculty_allowed(asst_faculty_id)) or not room_allowed(room_id):
            return scoped_access_error()

        duplicate_query = LabRequirement.query.filter(
            LabRequirement.subject_id == subject_id,
            LabRequirement.main_faculty_id == main_faculty_id,
            LabRequirement.room_id == room_id,
            LabRequirement.semester == semester,
            func.lower(func.trim(LabRequirement.section)) == section.lower(),
            func.lower(func.trim(LabRequirement.batch)) == batch.lower(),
            LabRequirement.weekly_hours == weekly_hours,
            LabRequirement.academic_year == academic_year
        )
        if asst_faculty_id is None:
            duplicate_query = duplicate_query.filter(LabRequirement.asst_faculty_id.is_(None))
        else:
            duplicate_query = duplicate_query.filter(LabRequirement.asst_faculty_id == asst_faculty_id)
        existing = duplicate_query.first()
        if existing:
            return jsonify({
                "msg": "This lab requirement is already saved. It was not added again.",
                "duplicate": True,
                "id": existing.id
            }), 200

        new_req = LabRequirement(
            subject_id=subject_id,
            main_faculty_id=main_faculty_id,
            asst_faculty_id=asst_faculty_id,
            room_id=room_id,
            semester=semester,
            section=section,
            batch=batch,
            weekly_hours=weekly_hours,
            academic_year=academic_year
        )
        db.session.add(new_req)
        db.session.commit()
        return jsonify({"msg": "Lab requirement added"}), 201

    academic_year = request.args.get('academic_year')
    department_id = scoped_department_id(request.args.get('department_id'))
    query = LabRequirement.query
    if academic_year:
        query = query.filter_by(academic_year=academic_year)
    if department_id:
        query = query.join(Subject).filter(Subject.department_id == department_id)

    requirements = query.order_by(
        LabRequirement.academic_year.desc(),
        LabRequirement.semester,
        LabRequirement.section,
        LabRequirement.batch
    ).all()
    result = []
    for req in requirements:
        department = Department.query.get(req.subject.department_id) if req.subject else None
        result.append({
            "id": req.id,
            "department_id": req.subject.department_id if req.subject else None,
            "department": department.name if department else "",
            "subject_id": req.subject_id,
            "subject_code": req.subject.code if req.subject else "",
            "subject": req.subject.name if req.subject else "",
            "main_faculty_id": req.main_faculty_id,
            "assistant_faculty_id": req.asst_faculty_id,
            "room_id": req.room_id,
            "main_faculty": req.main_faculty.name if req.main_faculty else "",
            "assistant_faculty": req.asst_faculty.name if req.asst_faculty else "",
            "room": req.room.name if req.room else "",
            "semester": req.semester,
            "section": req.section,
            "batch": req.batch,
            "weekly_hours": req.weekly_hours,
            "academic_year": req.academic_year
        })
    return jsonify(result)

def lab_requirement_schedule_query(req):
    query = Schedule.query.filter(
        Schedule.schedule_type == 'Lab',
        Schedule.subject_id == req.subject_id,
        Schedule.faculty_id == req.main_faculty_id,
        Schedule.room_id == req.room_id,
        Schedule.semester == req.semester,
        Schedule.academic_year == req.academic_year,
        func.lower(func.trim(Schedule.section)) == str(req.section or '').strip().lower(),
        func.lower(func.trim(Schedule.batch)) == str(req.batch or '').strip().lower()
    )
    if req.asst_faculty_id is None:
        return query.filter(Schedule.asst_faculty_id.is_(None))
    return query.filter(Schedule.asst_faculty_id == req.asst_faculty_id)

def theory_requirement_schedule_query(req):
    return Schedule.query.filter(
        Schedule.schedule_type == 'Theory',
        Schedule.subject_id == req.subject_id,
        Schedule.faculty_id == req.faculty_id,
        Schedule.room_id == req.room_id,
        Schedule.semester == req.semester,
        Schedule.academic_year == req.academic_year,
        func.lower(func.trim(Schedule.section)) == str(req.section or '').strip().lower()
    )

@app.route('/api/lab-requirements/<int:id>', methods=['PUT', 'DELETE'])
def saved_lab_requirement(id):
    req = LabRequirement.query.get(id)
    if not req:
        return jsonify({'error': 'Not found'}), 404
    if not requirement_allowed(req):
        return scoped_access_error()

    if request.method == 'DELETE':
        lab_requirement_schedule_query(req).delete(synchronize_session=False)
        db.session.delete(req)
        db.session.commit()
        return jsonify({'msg': 'Saved lab requirement deleted'})

    data = request.json
    subject_id = int(data['subject_id'])
    main_faculty_id = int(data['main_faculty_id'])
    asst_faculty_id = int(data['asst_faculty_id']) if data.get('asst_faculty_id') else None
    room_id = int(data['room_id'])
    semester = int(data['semester'])
    section = str(data.get('section') or '').strip()
    batch = str(data.get('batch') or '').strip()
    weekly_hours = int(data['weekly_hours'])
    academic_year = str(data.get('academic_year') or '2025-26').strip()
    if not subject_allowed(subject_id) or not faculty_allowed(main_faculty_id) or (asst_faculty_id and not faculty_allowed(asst_faculty_id)) or not room_allowed(room_id):
        return scoped_access_error()

    duplicate_query = LabRequirement.query.filter(
        LabRequirement.id != id,
        LabRequirement.subject_id == subject_id,
        LabRequirement.main_faculty_id == main_faculty_id,
        LabRequirement.room_id == room_id,
        LabRequirement.semester == semester,
        func.lower(func.trim(LabRequirement.section)) == section.lower(),
        func.lower(func.trim(LabRequirement.batch)) == batch.lower(),
        LabRequirement.weekly_hours == weekly_hours,
        LabRequirement.academic_year == academic_year
    )
    if asst_faculty_id is None:
        duplicate_query = duplicate_query.filter(LabRequirement.asst_faculty_id.is_(None))
    else:
        duplicate_query = duplicate_query.filter(LabRequirement.asst_faculty_id == asst_faculty_id)
    existing = duplicate_query.first()
    if existing:
        return jsonify({
            "msg": "Another saved lab requirement already has these same details.",
            "duplicate": True,
            "id": existing.id
        }), 200

    schedule_query = lab_requirement_schedule_query(req)
    subject = Subject.query.get(subject_id)
    duration = 3 if subject and subject.subject_type == 'Core Lab' else 2
    schedule_query.update({
        Schedule.subject_id: subject_id,
        Schedule.faculty_id: main_faculty_id,
        Schedule.asst_faculty_id: asst_faculty_id,
        Schedule.room_id: room_id,
        Schedule.semester: semester,
        Schedule.section: section,
        Schedule.batch: batch,
        Schedule.duration_slots: duration,
        Schedule.academic_year: academic_year
    }, synchronize_session=False)

    req.subject_id = subject_id
    req.main_faculty_id = main_faculty_id
    req.asst_faculty_id = asst_faculty_id
    req.room_id = room_id
    req.semester = semester
    req.section = section
    req.batch = batch
    req.weekly_hours = weekly_hours
    req.academic_year = academic_year
    db.session.commit()
    return jsonify({'msg': 'Saved lab requirement updated'})

@app.route('/api/theory-requirements', methods=['GET', 'POST'])
def handle_theory_requirements():
    if request.method == 'POST':
        data = request.json
        subject_id = int(data['subject_id'])
        faculty_id = int(data['faculty_id'])
        room_id = int(data['room_id'])
        semester = int(data['semester'])
        section = str(data.get('section') or '').strip()
        weekly_hours = int(data['weekly_hours'])
        academic_year = str(data.get('academic_year') or '2025-26').strip()
        if not subject_allowed(subject_id) or not faculty_allowed(faculty_id) or not classroom_allowed_for_subject(room_id, subject_id):
            return scoped_access_error()

        existing = TheoryRequirement.query.filter(
            TheoryRequirement.subject_id == subject_id,
            TheoryRequirement.faculty_id == faculty_id,
            TheoryRequirement.room_id == room_id,
            TheoryRequirement.semester == semester,
            func.lower(func.trim(TheoryRequirement.section)) == section.lower(),
            TheoryRequirement.weekly_hours == weekly_hours,
            TheoryRequirement.academic_year == academic_year
        ).first()
        if existing:
            return jsonify({
                "msg": "This theory requirement is already saved. It was not added again.",
                "duplicate": True,
                "id": existing.id
            }), 200

        new_req = TheoryRequirement(
            subject_id=subject_id,
            faculty_id=faculty_id,
            room_id=room_id,
            semester=semester,
            section=section,
            weekly_hours=weekly_hours,
            academic_year=academic_year
        )
        db.session.add(new_req)
        db.session.commit()
        return jsonify({"msg": "Theory requirement added"}), 201

    academic_year = request.args.get('academic_year')
    department_id = scoped_department_id(request.args.get('department_id'))
    query = TheoryRequirement.query
    if academic_year:
        query = query.filter_by(academic_year=academic_year)
    if department_id:
        query = query.join(Subject).filter(Subject.department_id == department_id)

    requirements = query.order_by(
        TheoryRequirement.academic_year.desc(),
        TheoryRequirement.semester,
        TheoryRequirement.section
    ).all()
    result = []
    for req in requirements:
        department = Department.query.get(req.subject.department_id) if req.subject else None
        result.append({
            "id": req.id,
            "department_id": req.subject.department_id if req.subject else None,
            "department": department.name if department else "",
            "subject_id": req.subject_id,
            "subject_code": req.subject.code if req.subject else "",
            "subject": req.subject.name if req.subject else "",
            "faculty_id": req.faculty_id,
            "room_id": req.room_id,
            "faculty": req.faculty.name if req.faculty else "",
            "room": req.room.name if req.room else "",
            "semester": req.semester,
            "section": req.section,
            "weekly_hours": req.weekly_hours,
            "academic_year": req.academic_year
        })
    return jsonify(result)

@app.route('/api/theory-requirements/<int:id>', methods=['PUT', 'DELETE'])
def saved_theory_requirement(id):
    req = TheoryRequirement.query.get(id)
    if not req:
        return jsonify({'error': 'Not found'}), 404
    if not requirement_allowed(req):
        return scoped_access_error()

    if request.method == 'DELETE':
        theory_requirement_schedule_query(req).delete(synchronize_session=False)
        db.session.delete(req)
        db.session.commit()
        return jsonify({'msg': 'Saved theory requirement deleted'})

    data = request.json
    subject_id = int(data['subject_id'])
    faculty_id = int(data['faculty_id'])
    room_id = int(data['room_id'])
    semester = int(data['semester'])
    section = str(data.get('section') or '').strip()
    weekly_hours = int(data['weekly_hours'])
    academic_year = str(data.get('academic_year') or '2025-26').strip()
    if not subject_allowed(subject_id) or not faculty_allowed(faculty_id) or not classroom_allowed_for_subject(room_id, subject_id):
        return scoped_access_error()

    existing = TheoryRequirement.query.filter(
        TheoryRequirement.id != id,
        TheoryRequirement.subject_id == subject_id,
        TheoryRequirement.faculty_id == faculty_id,
        TheoryRequirement.room_id == room_id,
        TheoryRequirement.semester == semester,
        func.lower(func.trim(TheoryRequirement.section)) == section.lower(),
        TheoryRequirement.weekly_hours == weekly_hours,
        TheoryRequirement.academic_year == academic_year
    ).first()
    if existing:
        return jsonify({
            "msg": "Another saved theory requirement already has these same details.",
            "duplicate": True,
            "id": existing.id
        }), 200

    theory_requirement_schedule_query(req).update({
        Schedule.subject_id: subject_id,
        Schedule.faculty_id: faculty_id,
        Schedule.room_id: room_id,
        Schedule.semester: semester,
        Schedule.section: section,
        Schedule.academic_year: academic_year
    }, synchronize_session=False)

    req.subject_id = subject_id
    req.faculty_id = faculty_id
    req.room_id = room_id
    req.semester = semester
    req.section = section
    req.weekly_hours = weekly_hours
    req.academic_year = academic_year
    db.session.commit()
    return jsonify({'msg': 'Saved theory requirement updated'})

@app.route('/api/sections', methods=['GET'])
def get_saved_sections():
    academic_year = request.args.get('academic_year')
    department_id = scoped_department_id(request.args.get('department_id'))
    semester = request.args.get('semester')
    seen = set()
    sections = []

    def add_section(value):
        cleaned = str(value or '').strip()
        if not cleaned or 'lab' in cleaned.lower():
            return
        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            sections.append(cleaned)

    def batch_looks_like_section(value):
        cleaned = str(value or '').strip()
        if not cleaned:
            return False
        upper = cleaned.upper()
        return "'" in cleaned or upper in {'A', 'B', 'C', 'D'} or upper.startswith(('I ', 'II ', 'III ', 'IV ', 'V ', 'VI ', 'VII ', 'VIII '))

    theory_query = TheoryRequirement.query
    lab_query = LabRequirement.query
    schedule_query = Schedule.query
    if academic_year:
        theory_query = theory_query.filter_by(academic_year=academic_year)
        lab_query = lab_query.filter_by(academic_year=academic_year)
        schedule_query = schedule_query.filter_by(academic_year=academic_year)
    if semester:
        theory_query = theory_query.filter_by(semester=semester)
        lab_query = lab_query.filter_by(semester=semester)
        schedule_query = schedule_query.filter_by(semester=semester)
    if department_id:
        theory_query = theory_query.join(Subject).filter(Subject.department_id == department_id)
        lab_query = lab_query.join(Subject).filter(Subject.department_id == department_id)
        schedule_query = schedule_query.join(Subject).filter(Subject.department_id == department_id)

    for req in theory_query.all():
        add_section(req.section)
    for req in lab_query.all():
        add_section(req.section)
        if batch_looks_like_section(req.batch):
            add_section(req.batch)
    for schedule in schedule_query.all():
        add_section(schedule.section)
        if batch_looks_like_section(schedule.batch):
            add_section(schedule.batch)

    return jsonify([{"section": section} for section in sorted(sections, key=lambda value: value.lower())])

@app.route('/api/room-timetable', methods=['GET'])
def get_room_timetable():
    room_id = request.args.get('room_id')
    if not room_id:
        return jsonify([])
    if not room_allowed(room_id):
        return scoped_access_error()
    academic_year = request.args.get('academic_year')
    q = Schedule.query.filter_by(room_id=room_id)
    if academic_year: q = q.filter_by(academic_year=academic_year)
    admin_department_id = current_admin_department_id()
    if admin_department_id:
        q = q.join(Subject).filter(Subject.department_id == admin_department_id)
    schedules = q.all()
    result = []
    for s in schedules:
        result.append({
            "id": s.id,
            "type": s.schedule_type,
            "subject_code": s.subject.code,
            "subject": s.subject.name,
            "faculty_id": s.faculty_id,
            "faculty": s.faculty.name,
            "assistant_faculty_id": s.asst_faculty_id,
            "assistant_faculty": assistant_faculty_name(s),
            "faculty_display": schedule_faculty_display(s),
            "day": s.day,
            "slot_index": s.slot_index,
            "duration": s.duration_slots,
            "semester": s.semester,
            "section": s.section,
            "batch": s.batch,
            "academic_year": s.academic_year
        })
    return jsonify(result)

from scheduler import generate_timetable, schedule_conflict_reason
@app.route('/api/generate', methods=['POST'])
def generate():
    try:
        academic_year = request.json.get('academic_year', '2025-26') if request.json else '2025-26'
        mode = request.json.get('mode', 'full') if request.json else 'full'
        if mode not in ('full', 'lab', 'theory'):
            return jsonify({"msg": "Invalid generation mode"}), 400
        requested_department_id = request.json.get('department_id') if request.json else None
        department_id = scoped_department_id(requested_department_id)
        generated = generate_timetable(db, academic_year, mode, department_id)
        labels = {'full': 'Timetable', 'lab': 'Lab timetable', 'theory': 'Theory timetable'}
        missing = generated.get('missing', [])
        if missing:
            msg = f"{labels[mode]} generated with warnings. Some weekly hours could not be placed."
        else:
            msg = f"{labels[mode]} generated successfully. Saved input data was kept."
        return jsonify({
            "msg": msg,
            "labs": generated.get('labs', 0),
            "lab_slots": generated.get('lab_slots', generated.get('labs', 0)),
            "theory": generated.get('theory', 0),
            "theory_slots": generated.get('theory_slots', generated.get('theory', 0)),
            "missing": missing,
            "department_id": department_id
        }), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"msg": f"Generation failed: {str(e)}"}), 500

@app.route('/api/timetable', methods=['GET'])
def get_timetable():
    semester = request.args.get('semester')
    section = request.args.get('section')
    academic_year = request.args.get('academic_year')
    faculty_id = request.args.get('faculty_id')
    department_id = scoped_department_id(request.args.get('department_id'))
    logged_faculty_id = current_faculty_id()
    if logged_faculty_id:
        faculty_id = logged_faculty_id
    query = Schedule.query
    if academic_year: query = query.filter_by(academic_year=academic_year)
    if semester: query = query.filter_by(semester=semester)
    if section:
        normalized_section = section.strip().lower()
        query = query.filter(or_(
            func.lower(func.trim(Schedule.section)) == normalized_section,
            func.lower(func.trim(Schedule.batch)) == normalized_section
        ))
    if faculty_id:
        query = query.filter(or_(
            Schedule.faculty_id == faculty_id,
            Schedule.asst_faculty_id == faculty_id
        ))
    if department_id: query = query.join(Subject).filter(Subject.department_id == department_id)
    
    schedules = query.all()
    result = []
    for s in schedules:
        result.append({
            "id": s.id,
            "type": s.schedule_type,
            "subject_code": s.subject.code,
            "subject_id": s.subject_id,
            "subject": s.subject.name,
            "faculty_id": s.faculty_id,
            "faculty": s.faculty.name,
            "assistant_faculty_id": s.asst_faculty_id,
            "assistant_faculty": assistant_faculty_name(s),
            "faculty_display": schedule_faculty_display(s),
            "room_id": s.room_id,
            "room": s.room.name,
            "day": s.day,
            "slot_index": s.slot_index,
            "duration": s.duration_slots,
            "semester": s.semester,
            "section": s.section,
            "academic_year": s.academic_year,
            "batch": s.batch
        })
    return jsonify(result)

@app.route('/timetable/print', methods=['GET'])
def print_timetable():
    semester = request.args.get('semester')
    section = request.args.get('section')
    academic_year = request.args.get('academic_year') or '2025-26'
    faculty_id = request.args.get('faculty_id')
    department_id = request.args.get('department_id')
    class_teacher = request.args.get('class_teacher') or '-'
    hod = request.args.get('hod') or '-'

    query = Schedule.query
    if academic_year:
        query = query.filter_by(academic_year=academic_year)
    if semester:
        query = query.filter_by(semester=semester)
    if section:
        normalized_section = section.strip().lower()
        query = query.filter(or_(
            func.lower(func.trim(Schedule.section)) == normalized_section,
            func.lower(func.trim(Schedule.batch)) == normalized_section
        ))
    if faculty_id:
        query = query.filter(or_(
            Schedule.faculty_id == faculty_id,
            Schedule.asst_faculty_id == faculty_id
        ))
    if department_id:
        query = query.join(Subject).filter(Subject.department_id == department_id)

    schedules = []
    for schedule in query.all():
        schedules.append({
            "id": schedule.id,
            "type": schedule.schedule_type,
            "subject_code": schedule.subject.code,
            "subject_id": schedule.subject_id,
            "subject": schedule.subject.name,
            "faculty": schedule.faculty.name,
            "faculty_id": schedule.faculty_id,
            "assistant_faculty_id": schedule.asst_faculty_id,
            "assistant_faculty": assistant_faculty_name(schedule),
            "faculty_display": schedule_faculty_display(schedule),
            "room": schedule.room.name,
            "day": schedule.day,
            "slot_index": schedule.slot_index,
            "duration": schedule.duration_slots,
            "semester": schedule.semester,
            "section": schedule.section,
            "academic_year": schedule.academic_year,
            "batch": schedule.batch
        })

    days = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
    slot_labels = ['8:30 - 9:20', '9:20 - 10:10', '10:10 - 11:00', '11:15 - 12:05', '12:05 - 12:55', '1:30 - 2:20', '2:20 - 3:10', '3:10 - 4:00']
    grid_rows = []
    for day in days:
        starts = {}
        for item in schedules:
            if item["day"] != day:
                continue
            starts.setdefault(int(item["slot_index"]), []).append(item)
        row_cells = []
        skip = 0
        for slot_index in range(8):
            if skip:
                skip -= 1
                continue
            if slot_index == 3:
                row_cells.append({"kind": "break", "label": "Short Break", "colspan": 1})
            if slot_index == 5:
                row_cells.append({"kind": "break", "label": "Lunch", "colspan": 1})
            if day == 'Saturday' and slot_index == 5:
                row_cells.append({"kind": "half", "label": "HALF DAY", "colspan": 3})
                skip = 2
                continue

            schedule_group = starts.get(slot_index)
            if schedule_group:
                schedule_group.sort(key=lambda item: (item.get("batch") or "", item["subject_code"]))
                duration = max(int(item["duration"]) for item in schedule_group)
                row_cells.append({
                    "kind": "schedule",
                    "schedules": schedule_group,
                    "colspan": duration,
                    "is_lab": any("Lab" in item["type"] for item in schedule_group)
                })
                skip = duration - 1
            else:
                row_cells.append({"kind": "empty", "label": "-", "colspan": 1})
        grid_rows.append({"day": day, "cells": row_cells})

    summary = {}
    for schedule in schedules:
        key = f"{schedule['subject_id']}::{schedule['type']}::{schedule.get('batch') or ''}"
        if key not in summary:
            summary[key] = {
                "code": schedule["subject_code"],
                "name": schedule["subject"],
                "short": schedule["subject"][:4],
            "faculty": set(),
                "batch": schedule.get("batch") or "-",
                "hours": 0
            }
        summary[key]["faculty"].add(schedule.get("faculty_display") or schedule["faculty"])
        summary[key]["hours"] += int(schedule["duration"])
    summary_rows = []
    for item in summary.values():
        summary_rows.append({
            "code": item["code"],
            "name": item["name"],
            "short": item["short"],
            "faculty": ", ".join(sorted(item["faculty"])),
            "batch": item["batch"],
            "hours": item["hours"]
        })

    department = Department.query.get(int(department_id)) if department_id else None
    return render_template(
        'timetable_print.html',
        days=days,
        slot_labels=slot_labels,
        grid_rows=grid_rows,
        summary_rows=summary_rows,
        department=department.name if department else '-',
        semester=semester or '-',
        section=section or '-',
        academic_year=academic_year,
        class_teacher=class_teacher,
        hod=hod,
        has_schedules=bool(schedules)
    )

@app.route('/api/schedules/<int:id>', methods=['PUT'])
def update_schedule(id):
    obj = Schedule.query.get(id)
    if not obj: return jsonify({'error': 'Not found'}), 404
    if not schedule_allowed(obj):
        return scoped_access_error()
    data = request.json
    if data.get('schedule_type'):
        obj.schedule_type = data['schedule_type']
    if data.get('subject_id'):
        obj.subject_id = data['subject_id']
    if data.get('faculty_id'):
        obj.faculty_id = data['faculty_id']
    if data.get('asst_faculty_id') is not None:
        obj.asst_faculty_id = int(data['asst_faculty_id']) if data.get('asst_faculty_id') else None
    if data.get('room_id'):
        obj.room_id = data['room_id']
    if data.get('day'):
        obj.day = data['day']
    if data.get('slot_index') is not None:
        obj.slot_index = int(data['slot_index'])
    if data.get('duration_slots'):
        obj.duration_slots = int(data['duration_slots'])
    elif data.get('schedule_type') == 'Theory':
        obj.duration_slots = 1
    elif data.get('schedule_type') == 'Lab':
        obj.duration_slots = 3 if obj.subject.subject_type == 'Core Lab' else 2
    if data.get('semester'):
        obj.semester = int(data['semester'])
    if data.get('section'):
        obj.section = data['section']
    if data.get('batch') is not None:
        obj.batch = data.get('batch') or None
    if data.get('academic_year'):
        obj.academic_year = data['academic_year']
    if obj.schedule_type == 'Theory':
        obj.asst_faculty_id = None
    room_ok = classroom_allowed_for_subject(obj.room_id, obj.subject_id) if obj.schedule_type == 'Theory' else room_allowed(obj.room_id)
    if not subject_allowed(obj.subject_id) or not faculty_allowed(obj.faculty_id) or (obj.asst_faculty_id and not faculty_allowed(obj.asst_faculty_id)) or not room_ok:
        db.session.rollback()
        return scoped_access_error()
    conflict = schedule_conflict_reason(
        db.session,
        obj.subject_id,
        obj.faculty_id,
        obj.room_id,
        obj.day,
        obj.slot_index,
        obj.duration_slots,
        obj.academic_year,
        obj.asst_faculty_id,
        obj.id,
        schedule_type=obj.schedule_type,
        semester=obj.semester,
        section=obj.section,
        batch=obj.batch
    )
    if conflict:
        db.session.rollback()
        return jsonify({'error': conflict}), 400
    db.session.commit()
    return jsonify({'msg': 'Timetable entry updated'})

@app.route('/api/schedules', methods=['POST'])
def add_schedule():
    data = request.json
    schedule_type = data.get('schedule_type', 'Theory')
    asst_faculty_id = None if schedule_type == 'Theory' else data.get('asst_faculty_id')
    if asst_faculty_id:
        asst_faculty_id = int(asst_faculty_id)
    room_ok = classroom_allowed_for_subject(data['room_id'], data['subject_id']) if schedule_type == 'Theory' else room_allowed(data['room_id'])
    if not subject_allowed(data['subject_id']) or not faculty_allowed(data['faculty_id']) or (asst_faculty_id and not faculty_allowed(asst_faculty_id)) or not room_ok:
        return scoped_access_error()
    conflict = schedule_conflict_reason(
        db.session,
        data['subject_id'],
        data['faculty_id'],
        data['room_id'],
        data['day'],
        int(data['slot_index']),
        int(data.get('duration_slots', 1)),
        data.get('academic_year', '2025-26'),
        asst_faculty_id,
        schedule_type=schedule_type,
        semester=data.get('semester'),
        section=data.get('section'),
        batch=data.get('batch')
    )
    if conflict:
        return jsonify({'error': conflict}), 400
    new_schedule = Schedule(
        schedule_type=schedule_type,
        subject_id=data['subject_id'],
        faculty_id=data['faculty_id'],
        asst_faculty_id=asst_faculty_id or None,
        room_id=data['room_id'],
        day=data['day'],
        slot_index=int(data['slot_index']),
        duration_slots=int(data.get('duration_slots', 1)),
        semester=int(data['semester']),
        section=data['section'],
        batch=data.get('batch') or None,
        academic_year=data.get('academic_year', '2025-26')
    )
    db.session.add(new_schedule)
    db.session.commit()
    return jsonify({'msg': 'Timetable entry added', 'id': new_schedule.id}), 201


@app.route('/api/departments/<int:id>', methods=['DELETE'])
def delete_department(id):
    try:
        blocked = require_global_admin()
        if blocked:
            return blocked
        obj = Department.query.get(id)
        if not obj: return jsonify({'error': 'Not found'}), 404
        subject_ids = [s.id for s in Subject.query.filter_by(department_id=id).all()]
        faculty_ids = [f.id for f in Faculty.query.filter_by(home_department_id=id).all()]
        room_ids = [r.id for r in Room.query.filter_by(department_id=id).all()]

        if subject_ids:
            LabRequirement.query.filter(LabRequirement.subject_id.in_(subject_ids)).delete(synchronize_session=False)
            TheoryRequirement.query.filter(TheoryRequirement.subject_id.in_(subject_ids)).delete(synchronize_session=False)
            Schedule.query.filter(Schedule.subject_id.in_(subject_ids)).delete(synchronize_session=False)
        if faculty_ids:
            User.query.filter(User.role == 'Faculty', User.faculty_id.in_(faculty_ids)).delete(synchronize_session=False)
            LabRequirement.query.filter(
                (LabRequirement.main_faculty_id.in_(faculty_ids)) |
                (LabRequirement.asst_faculty_id.in_(faculty_ids))
            ).delete(synchronize_session=False)
            TheoryRequirement.query.filter(TheoryRequirement.faculty_id.in_(faculty_ids)).delete(synchronize_session=False)
            Schedule.query.filter(
                (Schedule.faculty_id.in_(faculty_ids)) |
                (Schedule.asst_faculty_id.in_(faculty_ids))
            ).delete(synchronize_session=False)
            FacultyAvailability.query.filter(FacultyAvailability.faculty_id.in_(faculty_ids)).delete(synchronize_session=False)
            FacultyDepartmentMapping.query.filter(FacultyDepartmentMapping.faculty_id.in_(faculty_ids)).delete(synchronize_session=False)
        if room_ids:
            LabRequirement.query.filter(LabRequirement.room_id.in_(room_ids)).delete(synchronize_session=False)
            TheoryRequirement.query.filter(TheoryRequirement.room_id.in_(room_ids)).delete(synchronize_session=False)
            Schedule.query.filter(Schedule.room_id.in_(room_ids)).delete(synchronize_session=False)

        User.query.filter_by(role='Admin', department_id=id).delete(synchronize_session=False)
        User.query.filter_by(role='Faculty', department_id=id).delete(synchronize_session=False)
        FacultyDepartmentMapping.query.filter_by(department_id=id).delete(synchronize_session=False)
        Faculty.query.filter_by(home_department_id=id).delete(synchronize_session=False)
        Subject.query.filter_by(department_id=id).delete(synchronize_session=False)
        Room.query.filter_by(department_id=id).delete(synchronize_session=False)
        db.session.delete(obj)
        db.session.commit()
        return jsonify({'msg': 'Department and linked records deleted'})
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Cannot delete: currently in use.'}), 400
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({'error': 'Cannot delete department right now. Please check database write access.'}), 500

@app.route('/api/faculty/<int:id>', methods=['DELETE'])
def delete_faculty(id):
    try:
        obj = Faculty.query.get(id)
        if not obj: return jsonify({'error': 'Not found'}), 404
        if not department_allowed(obj.home_department_id):
            return scoped_access_error()
        LabRequirement.query.filter(
            (LabRequirement.main_faculty_id == id) |
            (LabRequirement.asst_faculty_id == id)
        ).delete(synchronize_session=False)
        TheoryRequirement.query.filter_by(faculty_id=id).delete(synchronize_session=False)
        Schedule.query.filter(
            (Schedule.faculty_id == id) |
            (Schedule.asst_faculty_id == id)
        ).delete(synchronize_session=False)
        User.query.filter_by(role='Faculty', faculty_id=id).delete(synchronize_session=False)
        FacultyAvailability.query.filter_by(faculty_id=id).delete(synchronize_session=False)
        FacultyDepartmentMapping.query.filter_by(faculty_id=id).delete(synchronize_session=False)
        db.session.delete(obj)
        db.session.commit()
        return jsonify({'msg': 'Faculty and linked records deleted'})
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Cannot delete: currently in use.'}), 400
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({'error': 'Cannot delete faculty right now. Please check database write access.'}), 500

@app.route('/api/rooms/<int:id>', methods=['DELETE'])
def delete_room(id):
    try:
        obj = Room.query.get(id)
        if not obj: return jsonify({'error': 'Not found'}), 404
        if not room_allowed(id):
            return scoped_access_error()
        LabRequirement.query.filter_by(room_id=id).delete(synchronize_session=False)
        TheoryRequirement.query.filter_by(room_id=id).delete(synchronize_session=False)
        Schedule.query.filter_by(room_id=id).delete(synchronize_session=False)
        db.session.delete(obj)
        db.session.commit()
        return jsonify({'msg': 'Room and linked records deleted'})
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Cannot delete: currently in use.'}), 400
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({'error': 'Cannot delete room right now. Please check database write access.'}), 500

@app.route('/api/subjects/<int:id>', methods=['DELETE'])
def delete_subject(id):
    try:
        obj = Subject.query.get(id)
        if not obj: return jsonify({'error': 'Not found'}), 404
        if not department_allowed(obj.department_id):
            return scoped_access_error()
        LabRequirement.query.filter_by(subject_id=id).delete(synchronize_session=False)
        TheoryRequirement.query.filter_by(subject_id=id).delete(synchronize_session=False)
        Schedule.query.filter_by(subject_id=id).delete(synchronize_session=False)
        db.session.delete(obj)
        db.session.commit()
        return jsonify({'msg': 'Subject and linked records deleted'})
    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Cannot delete: currently in use.'}), 400
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify({'error': 'Cannot delete subject right now. Please check database write access.'}), 500


@app.route('/public-labs')
def public_labs():
    return render_template('public_labs.html')


@app.route('/api/availability/<int:faculty_id>', methods=['GET'])
def get_availability(faculty_id):
    if not faculty_allowed(faculty_id):
        return scoped_access_error()
    unavail = FacultyAvailability.query.filter_by(faculty_id=faculty_id, is_available=False).all()
    return jsonify([{"day": u.day, "slot": int(u.time_slot)} for u in unavail])

@app.route('/api/availability/<int:faculty_id>', methods=['POST'])
def save_availability(faculty_id):
    if not faculty_allowed(faculty_id):
        return scoped_access_error()
    data = request.json # expects list of {day: "Monday", slot: 0}
    FacultyAvailability.query.filter_by(faculty_id=faculty_id).delete()
    for u in data:
        new_avail = FacultyAvailability(faculty_id=faculty_id, day=u['day'], time_slot=str(u['slot']), is_available=False)
        db.session.add(new_avail)
    db.session.commit()
    return jsonify({"msg": "Availability saved successfully!"}), 200

if __name__ == '__main__':
    with app.app_context():
        ensure_runtime_schema()
    app.run(host='0.0.0.0', debug=True, use_reloader=False, port=5000)
