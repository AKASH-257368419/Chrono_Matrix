// Utility functions for UI
function showToast(message, type = 'success') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;
    toast.innerText = message;
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.style.animation = 'slideIn 0.3s reverse forwards';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Global API Wrapper with JWT
async function apiCall(endpoint, method = 'GET', body = null) {
    const token = localStorage.getItem('token');
    const headers = {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${token}`
    };

    const options = { method, headers };
    if (body) options.body = JSON.stringify(body);

    const res = await fetch(endpoint, options);
    if (!res.ok) {
        if (res.status === 401) {
            localStorage.clear();
            window.location.href = '/';
        }
        let errMsg = 'API Error';
        try {
            const data = await res.json();
            if (data.error) errMsg = data.error;
            if (data.msg) errMsg = data.msg;
        } catch(e) {}
        throw new Error(errMsg);
    }
    return await res.json();
}

function facultyBelongsToDepartment(faculty, deptId) {
    const teachingIds = Array.isArray(faculty.teaching_department_ids)
        ? faculty.teaching_department_ids.map(String)
        : [];
    return String(faculty.department_id) === String(deptId) || teachingIds.includes(String(deptId));
}

// Setup Navigation based on auth
document.addEventListener('DOMContentLoaded', () => {
    installModalCloseButtons();
    configureBackButton();

    const navLinks = document.getElementById('nav-links');
    const navUser = document.getElementById('nav-user');
    const token = localStorage.getItem('token');
    
    if (token) {
        const role = localStorage.getItem('role');
        const isGlobalAdmin = localStorage.getItem('is_global_admin') === 'true';
        navLinks.innerHTML = '';
        if (role === 'Student') {
            const path = window.location.pathname;
            if(path.includes('dashboard') || path.includes('lobby') || path.includes('faculty') || path.includes('super-admin') || path.includes('department-admin-access')) {
                window.location.href = '/timetable';
            }
        } else if (role === 'Faculty') {
            const path = window.location.pathname;
            if(path.includes('dashboard') || path.includes('lobby') || path.includes('super-admin') || path.includes('department-admin-access')) {
                window.location.href = '/faculty-availability';
            }
        }

        installDepartmentAdminUiGuards(role);
        installGlobalAdminUi(isGlobalAdmin);
        
        const departmentName = localStorage.getItem('department_name');
        const displayRole = isGlobalAdmin ? 'S Admin' : localStorage.getItem('role');
        const userLabel = departmentName
            ? `${localStorage.getItem('username')} (${displayRole} - ${departmentName})`
            : `${localStorage.getItem('username')} (${displayRole})`;
        navUser.innerHTML = `
            <span style="margin-right: 1rem; color: var(--text-muted);">${userLabel}</span>
            <button class="btn btn-secondary" onclick="logout()" style="padding: 0.4rem 1rem;">Logout</button>
        `;
    }
});

function configureBackButton() {
    const button = document.querySelector('.nav-back-btn');
    if (!button) return;
    const path = window.location.pathname;
    button.style.display = (path === '/' || path === '/super-admin') ? 'none' : 'inline-block';
}

function installDepartmentAdminUiGuards(role) {
    if (role !== 'Admin' || !localStorage.getItem('department_id')) return;
    document.body.classList.add('department-admin');
    document.querySelectorAll('button[onclick]').forEach(button => {
        const action = button.getAttribute('onclick') || '';
        if (action.includes('addDepartment') || action.includes("openDeleteModal('dept')")) {
            button.style.display = 'none';
        }
    });
}

function installGlobalAdminUi(isGlobalAdmin) {
    if (!isGlobalAdmin) return;
    document.querySelectorAll('.global-admin-only').forEach(element => {
        element.style.display = '';
    });
}

function goBack() {
    const fallback = getBackFallback();
    try {
        const referrer = document.referrer ? new URL(document.referrer) : null;
        if (window.history.length > 1 && referrer && referrer.origin === window.location.origin) {
            window.history.back();
            return;
        }
    } catch (e) {}
    window.location.href = fallback;
}

function getBackFallback() {
    const token = localStorage.getItem('token');
    if (!token) return '/';
    const role = localStorage.getItem('role');
    if (role === 'Student') return '/timetable';
    if (role === 'Faculty') return '/faculty-availability';
    if (localStorage.getItem('is_global_admin') === 'true') return '/super-admin';
    return '/dashboard';
}

function logout() {
    localStorage.clear();
    window.location.href = '/';
}

function openModal(id) {
    document.getElementById(id).classList.add('active');
}

function closeModal(id) {
    document.getElementById(id).classList.remove('active');
}

function installModalCloseButtons() {
    document.querySelectorAll('.modal-overlay').forEach(modal => {
        const content = modal.querySelector('.modal-content');
        if (!content || content.querySelector('.modal-close-btn')) return;

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'modal-close-btn';
        button.textContent = 'X';
        button.setAttribute('aria-label', 'Close dialog');
        button.title = 'Close';
        button.addEventListener('click', () => closeModal(modal.id));
        content.insertBefore(button, content.firstChild);
    });
}

async function globalGenerateTimetable(mode = 'full') {
    const yearInput = document.getElementById('tr-year') || document.getElementById('lr-year') || document.getElementById('tt-year');
    const year = yearInput ? yearInput.value : prompt("Enter Academic Year to generate timetable for:", "2025-26");
    if (!year) return;
    
    const labels = {
        full: 'timetable',
        lab: 'lab timetable',
        theory: 'theory timetable'
    };

    try {
        showToast(`Generating ${labels[mode] || 'timetable'}... Please wait.`);
        const payload = { academic_year: year, mode };
        const departmentId = localStorage.getItem('department_id');
        if (departmentId) payload.department_id = departmentId;
        const res = await apiCall('/api/generate', 'POST', payload);
        if (res) {
            const details = typeof res.labs === 'number' ? ` Labs: ${res.labs}, Theory: ${res.theory}.` : '';
            showToast(`${res.msg}${details} Redirecting...`);
            setTimeout(() => window.location.href = '/timetable', 1500);
        }
    } catch (err) {
        showToast(err.message || 'Failed to generate timetable', 'error');
    }
}

async function openDeleteModal(type) {
    document.getElementById('del-type').value = type;
    const title = document.getElementById('del-title');
    const select = document.getElementById('del-select');
    
    let endpoint = '';
    if(type === 'dept') { title.innerText = 'Delete Department'; endpoint = '/api/departments'; }
    if(type === 'faculty') { title.innerText = 'Delete Faculty'; endpoint = '/api/faculty'; }
    if(type === 'room') { title.innerText = 'Delete Room'; endpoint = '/api/rooms'; }
    if(type === 'sub') { title.innerText = 'Delete Subject'; endpoint = '/api/subjects'; }
    
    try {
        const data = await apiCall(endpoint);
        let options = '';
        if(type === 'dept') options = data.map(d => `<option value="${d.id}">${d.name}</option>`).join('');
        if(type === 'faculty') options = data.map(d => `<option value="${d.id}">${d.name}</option>`).join('');
        if(type === 'room') options = data.map(d => `<option value="${d.id}">${d.name}</option>`).join('');
        if(type === 'sub') options = data.map(d => `<option value="${d.id}">${d.code} - ${d.name}</option>`).join('');
        
        select.innerHTML = options || '<option value="">No records available</option>';
        openModal('deleteModal');
    } catch(e) {
        showToast(e.message || 'Error loading data', 'error');
    }
}

let alterItems = [];
let alterDepartments = [];
let alterFaculties = [];
let alterSubjects = [];
let alterRooms = [];
let alterAvailability = [];

const ALTER_DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const ALTER_SLOTS = [
    '8:30 - 9:20',
    '9:20 - 10:10',
    '10:10 - 11:00',
    '11:15 - 12:05',
    '12:05 - 12:55',
    '1:30 - 2:20',
    '2:20 - 3:10',
    '3:10 - 4:00'
];
const ALTER_SLOT_STARTS = ['8:30', '9:20', '10:10', '11:15', '12:05', '1:30', '2:20', '3:10'];
const ALTER_SLOT_ENDS = ['9:20', '10:10', '11:00', '12:05', '12:55', '2:20', '3:10', '4:00'];

function getTableConfig(type) {
    const configs = {
        'faculty-name': { title: 'Faculty', endpoint: '/api/faculty', label: 'Select Faculty' },
        'faculty-availability': { title: 'Faculty Availability', endpoint: '/api/faculty', label: 'Select Faculty' },
        'subject-details': { title: 'Subject Details', endpoint: '/api/subjects', label: 'Select Subject' },
        'subject-timing': { title: 'Subject Day / Time', endpoint: '/api/timetable?academic_year=2025-26', label: 'Select Scheduled Subject' },
        room: { title: 'Room', endpoint: '/api/rooms' },
        sub: { title: 'Subject', endpoint: '/api/subjects' }
    };
    return configs[type];
}

function getItemLabel(type, item) {
    if (type === 'subject-details') return `${item.code} - ${item.name}`;
    if (type === 'subject-timing') return `${item.subject_code} - ${item.subject} (${item.type}, ${item.day}, ${slotRangeLabel(item.slot_index, item.duration)})`;
    return item.name;
}

function fieldHtml(label, controlHtml) {
    return `<div class="form-group alter-field"><label>${label}</label>${controlHtml}</div>`;
}

async function openAlterModal() {
    openModal('alterModal');
    await loadAlterDepartments();
    await loadAlterItems();
}

async function loadAlterDepartments() {
    const deptSelect = document.getElementById('alter-department');
    alterDepartments = await apiCall('/api/departments');
    deptSelect.innerHTML = '<option value="">Select Department</option>' + alterDepartments.map(d => `<option value="${d.id}">${d.name}</option>`).join('');
    const savedDept = localStorage.getItem('savedDept');
    if (savedDept && Array.from(deptSelect.options).some(o => o.value === savedDept)) {
        deptSelect.value = savedDept;
    }
}

async function loadAlterItems() {
    const type = document.getElementById('alter-type').value;
    const deptId = document.getElementById('alter-department').value;
    const config = getTableConfig(type);
    const select = document.getElementById('alter-select');
    const label = document.getElementById('alter-select-label');
    document.getElementById('alter-fields').innerHTML = '';

    if (label) label.innerText = config.label || `Select ${config.title}`;
    if (!deptId) {
        select.innerHTML = '<option value="">Select Department First</option>';
        return;
    }

    try {
        if (type === 'subject-timing') {
            alterItems = await apiCall(`${config.endpoint}&department_id=${deptId}`);
        } else {
            alterItems = await apiCall(config.endpoint);
        }

        alterFaculties = await apiCall('/api/faculty');
        alterSubjects = await apiCall('/api/subjects');
        alterRooms = await apiCall('/api/rooms');

        if (type === 'faculty-name' || type === 'faculty-availability') {
            alterItems = alterFaculties.filter(item => facultyBelongsToDepartment(item, deptId));
        }
        if (type === 'subject-details') {
            alterItems = alterSubjects.filter(item => String(item.department_id) === String(deptId));
        }
        if (type === 'room') {
            alterItems = alterRooms.filter(item => !item.department_id || String(item.department_id) === String(deptId));
        }

        select.innerHTML = alterItems.map(item => `<option value="${item.id}">${getItemLabel(type, item)}</option>`).join('') || '<option value="">No records available</option>';
        renderAlterFields();
    } catch (e) {
        showToast(e.message || 'Error loading records', 'error');
    }
}

function departmentOptions(selectedId) {
    return alterDepartments.map(d => `<option value="${d.id}" ${String(d.id) === String(selectedId) ? 'selected' : ''}>${d.name}</option>`).join('');
}

function optionsHtml(items, labelFn, selectedId) {
    return items.map(item => `<option value="${item.id}" ${String(item.id) === String(selectedId) ? 'selected' : ''}>${labelFn(item)}</option>`).join('');
}

function dayOptions(selectedDay) {
    return ALTER_DAYS.map(day => `<option value="${day}" ${day === selectedDay ? 'selected' : ''}>${day}</option>`).join('');
}

function slotOptions(selectedSlot) {
    return ALTER_SLOTS.map((slot, index) => `<option value="${index}" ${Number(selectedSlot) === index ? 'selected' : ''}>${slot}</option>`).join('');
}

function durationOptions(selectedDuration) {
    return [1, 2, 3].map(duration => `<option value="${duration}" ${Number(selectedDuration) === duration ? 'selected' : ''}>${duration} slot${duration > 1 ? 's' : ''}</option>`).join('');
}

function slotRangeLabel(startSlot, duration) {
    const start = Number(startSlot);
    const slots = Math.max(1, Number(duration) || 1);
    const end = Math.min(start + slots - 1, ALTER_SLOT_ENDS.length - 1);
    if (!ALTER_SLOT_STARTS[start]) return `Slot ${start}`;
    return `${ALTER_SLOT_STARTS[start]} - ${ALTER_SLOT_ENDS[end]}`;
}

function slotIndexes(startSlot, duration) {
    const start = Number(startSlot);
    const slots = Math.max(1, Number(duration) || 1);
    const end = Math.min(start + slots, ALTER_SLOTS.length);
    const indexes = [];
    for (let slot = start; slot < end; slot++) {
        indexes.push(slot);
    }
    return indexes;
}

function updateAlterSlotPreview() {
    const slotInput = document.getElementById('alter-slot');
    const durationInput = document.getElementById('alter-duration');
    const preview = document.getElementById('alter-slot-preview');
    if (!slotInput || !durationInput || !preview) return;
    preview.innerText = `This will occupy ${slotRangeLabel(slotInput.value, durationInput.value)}.`;
}

function updateAlterAvailabilityState() {
    const dayInput = document.getElementById('alter-day');
    const slotInput = document.getElementById('alter-slot');
    const durationInput = document.getElementById('alter-duration');
    const availableInput = document.getElementById('alter-available');
    const preview = document.getElementById('alter-slot-preview');
    if (!dayInput || !slotInput || !availableInput) return;
    const selectedSlots = slotIndexes(slotInput.value, durationInput ? durationInput.value : 1);
    const isUnavailable = selectedSlots.some(slot => alterAvailability.some(u => u.day === dayInput.value && Number(u.slot) === slot));
    availableInput.value = isUnavailable ? 'false' : 'true';
    if (preview) {
        preview.innerText = `This will update ${slotRangeLabel(slotInput.value, durationInput ? durationInput.value : 1)}.`;
    }
}

function renderAlterAvailabilityGrid() {
    const tbody = document.getElementById('alter-fa-body');
    if (!tbody) return;

    let html = '';
    ALTER_DAYS.forEach(day => {
        html += `<tr><td>${day}</td>`;
        ALTER_SLOTS.forEach((slot, index) => {
            const isUnavailable = alterAvailability.some(u => u.day === day && Number(u.slot) === index);
            const checked = isUnavailable ? '' : 'checked';
            html += `<td><input type="checkbox" id="alter-fa-${day}-${index}" ${checked}></td>`;
        });
        html += '</tr>';
    });
    tbody.innerHTML = html;
}

function openFacultyAvailabilityForSelected() {
    const deptId = document.getElementById('alter-department').value;
    const facultyId = document.getElementById('alter-select').value;
    if (deptId) localStorage.setItem('savedDept', deptId);
    if (facultyId) localStorage.setItem('savedFaculty', facultyId);
    const query = new URLSearchParams();
    if (deptId) query.set('department_id', deptId);
    if (facultyId) query.set('faculty_id', facultyId);
    window.location.href = `/faculty-availability?${query.toString()}`;
}

async function renderAlterFields() {
    const type = document.getElementById('alter-type').value;
    const deptId = document.getElementById('alter-department').value;
    const id = document.getElementById('alter-select').value;
    const item = alterItems.find(record => String(record.id) === String(id));
    const fields = document.getElementById('alter-fields');

    if (!item) {
        fields.innerHTML = '';
        return;
    }

    if (type === 'faculty-name') {
        fields.innerHTML = `
            ${fieldHtml('Faculty Name', `<input type="text" id="alter-name" value="${item.name}" placeholder="Faculty Name" required>`)}
        `;
    }

    if (type === 'faculty-availability') {
        alterAvailability = await apiCall(`/api/availability/${id}`);
        fields.innerHTML = `
            <div class="timetable-wrapper alter-availability-wrapper">
                <table class="timetable alter-availability-table">
                    <thead>
                        <tr>
                            <th>Day</th>
                            ${ALTER_SLOTS.map(slot => `<th>${slot}</th>`).join('')}
                        </tr>
                    </thead>
                    <tbody id="alter-fa-body"></tbody>
                </table>
            </div>
            <p style="color: var(--text-muted); margin-top: 0.75rem;">Checked means the faculty is available.</p>
        `;
        renderAlterAvailabilityGrid();
    }

    if (type === 'room') {
        fields.innerHTML = `
            ${fieldHtml('Room Name', `<input type="text" id="alter-name" value="${item.name}" placeholder="Room Name" required>`)}
            ${fieldHtml('Room Type', `<select id="alter-room-type">
                <option value="Lab" ${item.type === 'Lab' ? 'selected' : ''}>Lab</option>
                <option value="Theory" ${item.type === 'Theory' ? 'selected' : ''}>Theory</option>
            </select>`)}
        `;
    }

    if (type === 'subject-details') {
        fields.innerHTML = `
            ${fieldHtml('Subject Code', `<input type="text" id="alter-code" value="${item.code}" placeholder="Subject Code" required>`)}
            ${fieldHtml('Subject Name', `<input type="text" id="alter-name" value="${item.name}" placeholder="Subject Name" required>`)}
            ${fieldHtml('Subject Type', `<select id="alter-sub-type">
                <option value="Core Lab" ${item.type === 'Core Lab' ? 'selected' : ''}>Core Lab</option>
                <option value="Integrated Lab" ${item.type === 'Integrated Lab' ? 'selected' : ''}>Integrated Lab</option>
                <option value="Theory" ${item.type === 'Theory' ? 'selected' : ''}>Theory</option>
            </select>`)}
        `;
    }

    if (type === 'subject-timing') {
        const deptSubjects = alterSubjects.filter(subject => String(subject.department_id) === String(deptId));
        const deptFaculties = alterFaculties.filter(faculty => facultyBelongsToDepartment(faculty, deptId));
        const validRooms = alterRooms.filter(room => !room.department_id || String(room.department_id) === String(deptId));
        fields.innerHTML = `
            ${fieldHtml('Subject', `<select id="alter-subject">${optionsHtml(deptSubjects, s => `${s.code} - ${s.name}`, item.subject_id)}</select>`)}
            ${fieldHtml('Faculty', `<select id="alter-faculty">${optionsHtml(deptFaculties, f => f.name, item.faculty_id)}</select>`)}
            ${fieldHtml('Room', `<select id="alter-room">${optionsHtml(validRooms, r => `${r.name} (${r.type})`, item.room_id)}</select>`)}
            ${fieldHtml('Class Type', `<select id="alter-schedule-type"><option value="Lab" ${item.type === 'Lab' ? 'selected' : ''}>Lab</option><option value="Theory" ${item.type === 'Theory' ? 'selected' : ''}>Theory</option></select>`)}
            ${fieldHtml('Day', `<select id="alter-day">${dayOptions(item.day)}</select>`)}
            ${fieldHtml('Start Time Slot', `<select id="alter-slot" onchange="updateAlterSlotPreview()">${slotOptions(item.slot_index)}</select>`)}
            ${fieldHtml('Slot Duration', `<select id="alter-duration" onchange="updateAlterSlotPreview()">${durationOptions(item.duration)}</select>`)}
            <p id="alter-slot-preview" style="color: var(--text-muted); margin-top: -0.5rem;">This will occupy ${slotRangeLabel(item.slot_index, item.duration)}.</p>
        `;
    }
}

async function executeAlter() {
    const type = document.getElementById('alter-type').value;
    const id = document.getElementById('alter-select').value;
    const config = getTableConfig(type);
    if (!id) return;

    let payload = {};
    if (type === 'faculty-name') {
        payload = {
            name: document.getElementById('alter-name').value,
            home_department_id: document.getElementById('alter-department').value
        };
    }
    if (type === 'room') {
        payload = {
            name: document.getElementById('alter-name').value,
            room_type: document.getElementById('alter-room-type').value
        };
    }
    if (type === 'subject-details') {
        payload = {
            code: document.getElementById('alter-code').value,
            name: document.getElementById('alter-name').value,
            subject_type: document.getElementById('alter-sub-type').value,
            department_id: document.getElementById('alter-department').value
        };
    }
    if (type === 'faculty-availability') {
        const nextUnavailable = [];
        ALTER_DAYS.forEach(day => {
            ALTER_SLOTS.forEach((slot, index) => {
                const checkbox = document.getElementById(`alter-fa-${day}-${index}`);
                if (checkbox && !checkbox.checked) {
                    nextUnavailable.push({ day, slot: index });
                }
            });
        });
        const res = await apiCall(`/api/availability/${id}`, 'POST', nextUnavailable);
        showToast(res.msg || 'Availability updated');
        closeModal('alterModal');
        return;
    }
    if (type === 'subject-timing') {
        const scheduleType = document.getElementById('alter-schedule-type').value;
        const selectedSubjectId = document.getElementById('alter-subject').value;
        const selectedSubject = alterSubjects.find(subject => String(subject.id) === String(selectedSubjectId));
        payload = {
            subject_id: selectedSubjectId,
            faculty_id: document.getElementById('alter-faculty').value,
            room_id: document.getElementById('alter-room').value,
            schedule_type: scheduleType,
            day: document.getElementById('alter-day').value,
            slot_index: document.getElementById('alter-slot').value,
            duration_slots: document.getElementById('alter-duration').value
        };
    }

    try {
        const endpoint = type === 'subject-timing' ? `/api/schedules/${id}` : `${config.endpoint}/${id}`;
        const res = await apiCall(endpoint, 'PUT', payload);
        showToast(res.msg || 'Updated successfully');
        closeModal('alterModal');
        if (typeof loadDropdowns === 'function') {
            loadDropdowns();
        }
    } catch (e) {
        showToast(e.message || 'Error updating record', 'error');
    }
}

async function executeDelete() {
    const type = document.getElementById('del-type').value;
    const id = document.getElementById('del-select').value;
    
    if(!id) return;
    
    let endpoint = '';
    if(type === 'dept') endpoint = `/api/departments/${id}`;
    if(type === 'faculty') endpoint = `/api/faculty/${id}`;
    if(type === 'room') endpoint = `/api/rooms/${id}`;
    if(type === 'sub') endpoint = `/api/subjects/${id}`;
    
    try {
        await apiCall(endpoint, 'DELETE');
        showToast('Deleted successfully');
        closeModal('deleteModal');
        if (typeof loadDropdowns === 'function') {
            loadDropdowns(); // refresh dropdowns on page
        }
    } catch(e) {
        showToast(e.message || 'Error deleting item', 'error');
    }
}
