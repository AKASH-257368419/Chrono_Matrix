BEGIN TRANSACTION;
CREATE TABLE departments (
	id INTEGER NOT NULL, 
	name VARCHAR(50) NOT NULL, 
	priority INTEGER, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
INSERT INTO "departments" VALUES(1,'data science',1);
INSERT INTO "departments" VALUES(2,'IS ',1);
CREATE TABLE faculty (
	id INTEGER NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	home_department_id INTEGER NOT NULL, 
	max_hours_per_day INTEGER, 
	capability VARCHAR(20), 
	PRIMARY KEY (id), 
	FOREIGN KEY(home_department_id) REFERENCES departments (id)
);
INSERT INTO "faculty" VALUES(1,'is faculty 1',2,6,'Both');
INSERT INTO "faculty" VALUES(2,'faculty 1',2,6,'Both');
INSERT INTO "faculty" VALUES(3,'faculty 1',1,6,'Both');
CREATE TABLE faculty_availability (
	id INTEGER NOT NULL, 
	faculty_id INTEGER NOT NULL, 
	day VARCHAR(20) NOT NULL, 
	time_slot VARCHAR(50) NOT NULL, 
	is_available BOOLEAN, 
	PRIMARY KEY (id), 
	FOREIGN KEY(faculty_id) REFERENCES faculty (id)
);
CREATE TABLE faculty_department_mapping (
	id INTEGER NOT NULL, 
	faculty_id INTEGER NOT NULL, 
	department_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(faculty_id) REFERENCES faculty (id), 
	FOREIGN KEY(department_id) REFERENCES departments (id)
);
CREATE TABLE lab_requirements (
	id INTEGER NOT NULL, 
	subject_id INTEGER NOT NULL, 
	main_faculty_id INTEGER NOT NULL, 
	asst_faculty_id INTEGER, 
	room_id INTEGER NOT NULL, 
	semester INTEGER NOT NULL, 
	section VARCHAR(10) NOT NULL, 
	batch VARCHAR(10) NOT NULL, 
	weekly_hours INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(subject_id) REFERENCES subjects (id), 
	FOREIGN KEY(main_faculty_id) REFERENCES faculty (id), 
	FOREIGN KEY(asst_faculty_id) REFERENCES faculty (id), 
	FOREIGN KEY(room_id) REFERENCES rooms (id)
);
INSERT INTO "lab_requirements" VALUES(1,1,1,NULL,1,1,'a','2023',2);
CREATE TABLE rooms (
	id INTEGER NOT NULL, 
	name VARCHAR(50) NOT NULL, 
	room_type VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (name)
);
INSERT INTO "rooms" VALUES(1,'lab 1','Lab');
INSERT INTO "rooms" VALUES(2,'room1','Theory');
CREATE TABLE schedules (
	id INTEGER NOT NULL, 
	schedule_type VARCHAR(10) NOT NULL, 
	subject_id INTEGER NOT NULL, 
	faculty_id INTEGER NOT NULL, 
	asst_faculty_id INTEGER, 
	room_id INTEGER NOT NULL, 
	day VARCHAR(20) NOT NULL, 
	slot_index INTEGER NOT NULL, 
	duration_slots INTEGER, 
	semester INTEGER NOT NULL, 
	section VARCHAR(10) NOT NULL, 
	batch VARCHAR(10), 
	PRIMARY KEY (id), 
	FOREIGN KEY(subject_id) REFERENCES subjects (id), 
	FOREIGN KEY(faculty_id) REFERENCES faculty (id), 
	FOREIGN KEY(asst_faculty_id) REFERENCES faculty (id), 
	FOREIGN KEY(room_id) REFERENCES rooms (id)
);
INSERT INTO "schedules" VALUES(1,'Lab',1,1,NULL,1,'Monday',0,3,1,'a','2023');
INSERT INTO "schedules" VALUES(2,'Theory',3,1,NULL,2,'Monday',3,1,1,'1',NULL);
CREATE TABLE subjects (
	id INTEGER NOT NULL, 
	code VARCHAR(20) NOT NULL, 
	name VARCHAR(100) NOT NULL, 
	subject_type VARCHAR(20) NOT NULL, 
	department_id INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (code), 
	FOREIGN KEY(department_id) REFERENCES departments (id)
);
INSERT INTO "subjects" VALUES(1,'dbms123','dbms','Core Lab',1);
INSERT INTO "subjects" VALUES(2,'','','Core Lab',1);
INSERT INTO "subjects" VALUES(3,'123','sdds','Theory',1);
INSERT INTO "subjects" VALUES(4,'1233','sddsdaf','Theory',1);
CREATE TABLE theory_requirements (
	id INTEGER NOT NULL, 
	subject_id INTEGER NOT NULL, 
	faculty_id INTEGER NOT NULL, 
	room_id INTEGER NOT NULL, 
	semester INTEGER NOT NULL, 
	section VARCHAR(10) NOT NULL, 
	weekly_hours INTEGER NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(subject_id) REFERENCES subjects (id), 
	FOREIGN KEY(faculty_id) REFERENCES faculty (id), 
	FOREIGN KEY(room_id) REFERENCES rooms (id)
);
INSERT INTO "theory_requirements" VALUES(1,3,1,2,1,'1',1);
CREATE TABLE users (
	id INTEGER NOT NULL, 
	username VARCHAR(80) NOT NULL, 
	password_hash VARCHAR(256) NOT NULL, 
	role VARCHAR(20) NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (username)
);
INSERT INTO "users" VALUES(1,'admin','scrypt:32768:8:1$0K9qh9WGGJjpGcJu$136013e1087149290e61df8e38e6219f1fbb17f33718583ec7fd2af5232d2d02fb92887570c1608da07dba82d7d7dd00c154aacf48e5201e680b035ec2258dd9','Admin');
COMMIT;
