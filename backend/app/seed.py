"""Seed the demo institution.

Run with `python -m app.seed`. Idempotent: refuses to run when users exist.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.demo import DEMO_ACCOUNTS
from app.core.security import hash_password, new_totp_secret, seal_secret
from app.db.session import init_db, session_scope
from app.models.enums import (
    BloomLevel,
    Difficulty,
    QuestionType,
    Role,
)
from app.models.tables import PracticeQuestion, Subject, Topic, User
from app.schemas.exam import ExamConfigCreate, ExamTopicIn, PatternItem
from app.schemas.practice import OptionIn, PracticeQuestionCreate, PracticeTestCreate
from app.services import academic_service, exam_service, generation_service, practice_service, syllabus_service

logger = logging.getLogger("praxis.seed")

DBMS_SYLLABUS = """\
DATABASE MANAGEMENT SYSTEMS (CS501) - COURSE SYLLABUS

UNIT 1: ENTITY-RELATIONSHIP MODELLING AND RELATIONAL MODEL
A database management system separates physical storage from logical data description.
The entity-relationship model represents entities, attributes and relationships. Cardinality
constraints one-to-one, one-to-many and many-to-many determine table design. Weak entities
depend on an identifying owner. Generalization and specialization model inheritance.
The relational model represents data as relations with schemas, tuples and domains.
Relational algebra operators include selection, projection, union, set difference, Cartesian
product, join and division. Natural join removes duplicate attribute columns. Outer joins
preserve unmatched tuples with nulls. Integrity constraints include primary keys, candidate
keys, foreign keys and domain constraints. Referential integrity prevents dangling references
and is enforced with cascading actions or restricted deletes.

UNIT 2: SQL AND QUERY PROCESSING
SQL has DDL for create, alter and drop, DML for insert, update and delete, and DCL for grants.
SELECT queries use WHERE, GROUP BY, HAVING and ORDER BY. Aggregate functions include COUNT,
SUM, AVG, MIN and MAX. Subqueries may be correlated or uncorrelated. EXISTS tests row presence.
Views are virtual tables and materialized views persist results. Query processing parses,
validates, optimizes and evaluates a query. The query optimizer compares access paths using
cost estimates from statistics. Indexes such as B+ trees reduce search cost for equality and
range predicates. Hash indexes support equality only. Join algorithms include nested loop,
sort-merge and hash join. Pushing selections and projections down reduces intermediate sizes.

UNIT 3: NORMALIZATION AND FUNCTIONAL DEPENDENCIES
Functional dependency X -> Y means each X value determines one Y value. Armstrong's axioms
of reflexivity, augmentation and transitivity derive further dependencies. Closure of an
attribute set determines candidate keys. A superkey uniquely identifies tuples and a candidate
key is a minimal superkey. First normal form requires atomic attribute values. Second normal
form removes partial dependencies on part of a composite key. Third normal form removes
transitive dependencies. Boyce-Codd normal form requires every determinant to be a superkey.
Multivalued dependencies cause fourth normal form violations. Join dependencies cause fifth
normal form violations. Lossless join decomposition preserves information; dependency
preservation keeps constraints checkable without joins. Denormalization trades redundancy
for read performance and must be a deliberate decision.

UNIT 4: TRANSACTIONS AND CONCURRENCY CONTROL
A transaction is a unit of work with ACID properties: atomicity, consistency, isolation and
durability. Atomicity is implemented with undo logging. Durability uses write-ahead logging
and checkpoints. Schedules are serial if they produce the same result as some serial order.
Conflict serializability is tested with a precedence graph; a cycle indicates a non-serializable
schedule. Two-phase locking has a growing phase and a shrinking phase. Strict two-phase locking
holds exclusive locks until commit. Deadlock arises from circular waiting and is handled by
prevention, detection with wait-for graphs, or timeout. Timestamp ordering and multiversion
concurrency control avoid locks. Isolation levels are read uncommitted, read committed,
repeatable read and serializable. Phantom reads occur when new rows satisfy a predicate.
Write skew appears under snapshot isolation. Recovery uses the redo and undo phases.

UNIT 5: STORAGE, INDEXING AND DISTRIBUTED DATA
Storage hierarchy spans cache, main memory, flash and disk. Disk pages are the unit of transfer.
B+ tree indexes store keys in internal nodes and records in leaves; leaves are linked for range
scans. A clustered index determines physical row order. Secondary indexes may require lookups.
Bitmap indexes suit low-cardinality columns. Buffer managers replace pages using LRU, clock or
LRU-K policies. RAID levels trade redundancy for performance. Sharding partitions data across
nodes by range, hash or directory. Replication improves availability; quorum reads and writes
balance consistency. The CAP theorem states a distributed store cannot guarantee consistency,
availability and partition tolerance simultaneously. Two-phase commit coordinates distributed
transactions but blocks on coordinator failure. NoSQL stores include document, key-value,
column-family and graph models. NewSQL systems retain SQL with distributed transactions.
"""

OS_SYLLABUS = """\
OPERATING SYSTEMS (CS502) - COURSE SYLLABUS

UNIT 1: PROCESSES, THREADS AND SCHEDULING
A process is a program in execution with code, data, heap and stack. The process control block
stores registers, state and scheduling information. Process states include new, ready, running,
waiting and terminated. Context switching saves and restores processor state and costs time.
Threads share an address space but have separate stacks and registers. User-level threads are
fast but a blocking call stalls the process; kernel threads allow true parallelism.
Scheduling criteria include CPU utilization, throughput, turnaround time, waiting time and
response time. First come first served is simple but suffers the convoy effect. Shortest job
first minimizes average waiting time but requires burst estimates. Round robin uses a time
quantum and provides fairness. Priority scheduling can starve low-priority tasks; aging fixes
starvation. Multilevel feedback queues adapt to observed behavior. Real-time schedulers use
rate monotonic and earliest deadline first policies.

UNIT 2: SYNCHRONIZATION AND DEADLOCKS
A race condition occurs when concurrent access produces order-dependent results. Critical
sections require mutual exclusion, progress and bounded waiting. Peterson's algorithm is a
software solution for two processes. Test-and-set and compare-and-swap are hardware primitives.
Semaphores support counting and binary synchronization. Monitors encapsulate shared data with
condition variables. The producer-consumer problem uses a bounded buffer. The readers-writers
problem allows concurrent readers or one writer. The dining philosophers problem illustrates
deadlock and starvation. Deadlock requires mutual exclusion, hold and wait, no preemption and
circular wait. Prevention removes one necessary condition. Avoidance uses the Banker's
algorithm with safe states. Detection builds a wait-for graph and searches for cycles.
Recovery may terminate processes or roll back checkpoints.

UNIT 3: MEMORY MANAGEMENT
Logical addresses are translated to physical addresses by the memory management unit.
Contiguous allocation suffers external fragmentation. Paging divides memory into fixed frames
and replaces external fragmentation with internal fragmentation. A page table maps pages to
frames; multi-level page tables reduce table size. A translation lookaside buffer caches
recent translations. Segmentation uses variable-size logical segments. Demand paging loads
pages on access. A page fault traps to the kernel, which loads the page and restarts the
instruction. Page replacement algorithms include FIFO, optimal and least recently used.
Belady's anomaly affects FIFO. The working set model estimates locality. Thrashing occurs
when the resident set is too small and the system spends time paging. Copy-on-write shares
pages after fork until a write occurs.

UNIT 4: FILE SYSTEMS AND I/O
A file is a named collection of persistent data. Access methods include sequential and direct.
Directory structures include single-level, two-level, tree and graph. Allocation methods are
contiguous, linked and indexed. Inodes store metadata and block pointers. Journaling writes
intent records before metadata updates so crash recovery can replay or discard. Log-structured
file systems append updates sequentially. Disk scheduling algorithms include FCFS, SSTF, SCAN,
C-SCAN and LOOK. RAID 0 stripes without redundancy, RAID 1 mirrors, RAID 5 distributes parity
and RAID 6 adds dual parity. Caching and buffering hide device latency. Direct memory access
moves data without per-byte CPU involvement. Device drivers present a uniform interface.
"""


def _practice_questions() -> dict[str, list[dict]]:
    dbms = [
        {
            "question_text": "Which normal form eliminates transitive dependencies on non-key attributes?",
            "options": [
                {"id": "a", "text": "First normal form"},
                {"id": "b", "text": "Second normal form"},
                {"id": "c", "text": "Third normal form"},
                {"id": "d", "text": "Boyce-Codd normal form"},
            ],
            "correct": ["c"],
            "explanation": "Third normal form removes transitive dependencies of non-prime attributes on the key.",
            "topic": "Normalization & Functional Dependencies",
            "difficulty": Difficulty.MEDIUM,
            "bloom": BloomLevel.UNDERSTAND,
        },
        {
            "question_text": "A relation is in BCNF when every determinant is a:",
            "options": [
                {"id": "a", "text": "Candidate key"},
                {"id": "b", "text": "Superkey"},
                {"id": "c", "text": "Foreign key"},
                {"id": "d", "text": "Prime attribute"},
            ],
            "correct": ["b"],
            "explanation": "BCNF requires the left side of every non-trivial functional dependency to be a superkey.",
            "topic": "Normalization & Functional Dependencies",
            "difficulty": Difficulty.MEDIUM,
            "bloom": BloomLevel.REMEMBER,
        },
        {
            "question_text": "Which join preserves unmatched rows from the left relation?",
            "options": [
                {"id": "a", "text": "Inner join"},
                {"id": "b", "text": "Left outer join"},
                {"id": "c", "text": "Semi join"},
                {"id": "d", "text": "Cross join"},
            ],
            "correct": ["b"],
            "explanation": "A left outer join keeps every left tuple and fills missing right attributes with nulls.",
            "topic": "SQL & Query Processing",
            "difficulty": Difficulty.EASY,
            "bloom": BloomLevel.APPLY,
        },
        {
            "question_text": "A schedule is conflict serializable if its precedence graph is:",
            "options": [
                {"id": "a", "text": "Acyclic"},
                {"id": "b", "text": "Complete"},
                {"id": "c", "text": "Bipartite"},
                {"id": "d", "text": "Strongly connected"},
            ],
            "correct": ["a"],
            "explanation": "A cycle means conflicting operations force contradictory ordering; an acyclic graph implies an equivalent serial order.",
            "topic": "Transactions & Concurrency Control",
            "difficulty": Difficulty.MEDIUM,
            "bloom": BloomLevel.ANALYZE,
        },
        {
            "question_text": "Which property guarantees that a committed transaction survives a crash?",
            "options": [
                {"id": "a", "text": "Atomicity"},
                {"id": "b", "text": "Consistency"},
                {"id": "c", "text": "Isolation"},
                {"id": "d", "text": "Durability"},
            ],
            "correct": ["d"],
            "explanation": "Durability is enforced by write-ahead logging and stable storage before commit is acknowledged.",
            "topic": "Transactions & Concurrency Control",
            "difficulty": Difficulty.EASY,
            "bloom": BloomLevel.REMEMBER,
        },
        {
            "question_text": "A B+ tree index primarily improves which operation compared with a full table scan?",
            "options": [
                {"id": "a", "text": "Range and equality lookups"},
                {"id": "b", "text": "Schema migration"},
                {"id": "c", "text": "Transaction rollback"},
                {"id": "d", "text": "Deadlock detection"},
            ],
            "correct": ["a"],
            "explanation": "Linked leaf pages support efficient range scans while the balanced tree gives logarithmic equality lookups.",
            "topic": "Indexing & Storage",
            "difficulty": Difficulty.EASY,
            "bloom": BloomLevel.APPLY,
        },
    ]
    os_questions = [
        {
            "question_text": "Which scheduling policy can starve low-priority processes without aging?",
            "options": [
                {"id": "a", "text": "Round robin"},
                {"id": "b", "text": "Priority scheduling"},
                {"id": "c", "text": "First come first served"},
                {"id": "d", "text": "Multilevel queue with aging"},
            ],
            "correct": ["b"],
            "explanation": "Fixed priority scheduling can indefinitely postpone low-priority work unless aging raises priority over time.",
            "topic": "Processes, Threads & Scheduling",
            "difficulty": Difficulty.EASY,
            "bloom": BloomLevel.UNDERSTAND,
        },
        {
            "question_text": "Which condition is NOT required for a deadlock?",
            "options": [
                {"id": "a", "text": "Mutual exclusion"},
                {"id": "b", "text": "Hold and wait"},
                {"id": "c", "text": "Preemption of held resources"},
                {"id": "d", "text": "Circular wait"},
            ],
            "correct": ["c"],
            "explanation": "Deadlock requires no preemption; forced preemption breaks one of the four Coffman conditions.",
            "topic": "Synchronization & Deadlocks",
            "difficulty": Difficulty.MEDIUM,
            "bloom": BloomLevel.ANALYZE,
        },
        {
            "question_text": "A page fault occurs when:",
            "options": [
                {"id": "a", "text": "A page is present but dirty"},
                {"id": "b", "text": "The referenced page is not resident in memory"},
                {"id": "c", "text": "The TLB hits"},
                {"id": "d", "text": "A segment exceeds its limit"},
            ],
            "correct": ["b"],
            "explanation": "The MMU raises a fault when the present bit is clear, and the kernel loads the page from backing store.",
            "topic": "Memory Management",
            "difficulty": Difficulty.EASY,
            "bloom": BloomLevel.REMEMBER,
        },
        {
            "question_text": "Thrashing is most directly caused by:",
            "options": [
                {"id": "a", "text": "Too many page frames per process"},
                {"id": "b", "text": "Excessive paging due to an undersized resident set"},
                {"id": "c", "text": "Journaling file systems"},
                {"id": "d", "text": "DMA transfers"},
            ],
            "correct": ["b"],
            "explanation": "When the working set does not fit, the system spends most time servicing page faults instead of executing instructions.",
            "topic": "Memory Management",
            "difficulty": Difficulty.MEDIUM,
            "bloom": BloomLevel.ANALYZE,
        },
        {
            "question_text": "Journaling in a file system mainly improves:",
            "options": [
                {"id": "a", "text": "Crash consistency of metadata"},
                {"id": "b", "text": "Disk rotational speed"},
                {"id": "c", "text": "Page replacement accuracy"},
                {"id": "d", "text": "CPU scheduling fairness"},
            ],
            "correct": ["a"],
            "explanation": "Intent records let recovery replay or discard incomplete metadata updates after a crash.",
            "topic": "File Systems & I/O",
            "difficulty": Difficulty.MEDIUM,
            "bloom": BloomLevel.UNDERSTAND,
        },
    ]
    return {"dbms": dbms, "os": os_questions}


def _make_test(db, teacher, subject, topics_by_name, title, description, specs) -> str:
    topic_ids = {topic.name: topic.id for topic in topics_by_name.values()}
    questions = []
    for position, spec in enumerate(specs):
        questions.append(
            PracticeQuestionCreate(
                topic_id=topic_ids[spec["topic"]],
                question_text=spec["question_text"],
                question_type=QuestionType.SINGLE_CHOICE,
                options=[OptionIn(**option) for option in spec["options"]],
                correct_answers=spec["correct"],
                explanation=spec["explanation"],
                marks=1,
                difficulty=spec["difficulty"],
                bloom_level=spec["bloom"],
            )
        )
    topic_ids = list(dict.fromkeys(topics_by_name[spec["topic"]].id for spec in specs))
    payload = PracticeTestCreate(
        subject_id=subject.id,
        title=title,
        description=description,
        duration_minutes=30,
        difficulty=Difficulty.MEDIUM,
        topic_ids=topic_ids,
        questions=questions,
    )
    result = practice_service.create_test(db, teacher, payload)
    test_id = result["test_id"]
    db.flush()
    practice_service.set_status(db, teacher, test_id, "PUBLISHED")
    db.commit()
    return test_id


def _attempt(db, test_id: str, student: User, wrong_topics: set[str], offset: int = 0) -> None:
    attempt = practice_service.start_attempt(db, student, test_id)
    db.flush()
    questions = list(
        db.execute(
            select(PracticeQuestion)
            .where(PracticeQuestion.test_id == test_id)
            .order_by(PracticeQuestion.position)
        )
        .scalars()
        .all()
    )
    answers = []
    for index, question in enumerate(questions):
        topic_name = question.topic.name if question.topic else ""
        correct = list(question.correct_answers or [])
        if topic_name in wrong_topics or index % 5 == 4:
            chosen = [option["id"] for option in question.options if option["id"] not in correct][:1]
        else:
            chosen = correct
        answers.append(
            {
                "question_id": question.id,
                "selected": chosen,
                "response_time_seconds": 18 + ((index * 7 + offset * 5) % 40),
            }
        )
    practice_service.submit_attempt(db, student, attempt["id"], answers)
    db.commit()


def seed_demo() -> dict[str, int]:
    """Create the demo institution. Returns a summary of seeded rows."""
    from app.core.config import settings

    if not settings.DEMO_MODE:
        raise RuntimeError("Demo seeding requires DEMO_MODE=true; production must use real accounts.")
    demo = {account["email"]: account for account in DEMO_ACCOUNTS}
    with session_scope() as db:
        users: dict[str, User] = {}
        extra = [
            ("kavya@praxis.demo", "Prof. Kavya Iyer", Role.TEACHER),
            ("aman@praxis.demo", "Aman Verma", Role.STUDENT),
            ("sara@praxis.demo", "Sara Khan", Role.STUDENT),
        ]
        for email, name, role in extra:
            account = demo.get(email)
            users[email] = User(
                email=email,
                full_name=name,
                role=role,
                department="Computer Science",
                password_hash=hash_password(account["password"] if account else "Praxis@2026#Demo"),
            )
        for account in DEMO_ACCOUNTS:
            user = User(
                email=account["email"],
                full_name=account["full_name"],
                role=Role(account["role"]),
                department="Computer Science",
                password_hash=hash_password(account["password"]),
            )
            if account["role"] == "HOD":
                user.mfa_enabled = True
                user.mfa_secret_enc = seal_secret(new_totp_secret())
            users[account["email"]] = user
        db.add_all(users.values())
        db.flush()

        hod = users["hod@praxis.demo"]
        teacher = users["teacher@praxis.demo"]
        teacher2 = users["kavya@praxis.demo"]
        student = users["student@praxis.demo"]
        student2 = users["aman@praxis.demo"]

        dbms = Subject(
            code="CS501",
            name="Database Management Systems",
            description="Relational modelling, SQL, normalization, transactions and storage.",
            department="Computer Science",
            created_by=hod.id,
        )
        os_subject = Subject(
            code="CS502",
            name="Operating Systems",
            description="Processes, concurrency, memory, file systems and I/O.",
            department="Computer Science",
            created_by=hod.id,
        )
        db.add_all([dbms, os_subject])
        db.flush()

        dbms_topic_names = [
            ("ER Modelling & Relational Algebra", "Unit 1"),
            ("SQL & Query Processing", "Unit 2"),
            ("Normalization & Functional Dependencies", "Unit 3"),
            ("Transactions & Concurrency Control", "Unit 4"),
            ("Indexing & Storage", "Unit 5"),
        ]
        os_topic_names = [
            ("Processes, Threads & Scheduling", "Unit 1"),
            ("Synchronization & Deadlocks", "Unit 2"),
            ("Memory Management", "Unit 3"),
            ("File Systems & I/O", "Unit 4"),
        ]
        for name, unit in dbms_topic_names:
            db.add(Topic(subject_id=dbms.id, name=name, unit=unit, description=f"Core {name.lower()} concepts."))
        for name, unit in os_topic_names:
            db.add(Topic(subject_id=os_subject.id, name=name, unit=unit, description=f"Core {name.lower()} concepts."))
        db.flush()
        for subject, teacher_user in ((dbms, teacher), (os_subject, teacher), (os_subject, teacher2)):
            academic_service.assign_teacher(db, subject.id, teacher_user.id)
        db.commit()

        dbms_topics = {topic.name: topic for topic in academic_service.topics_for_subject(db, dbms.id)}
        os_topics = {topic.name: topic for topic in academic_service.topics_for_subject(db, os_subject.id)}

    # Upload syllabus documents outside the transaction so ingestion commits cleanly.
    with session_scope() as db:
        teacher = db.get(User, users["teacher@praxis.demo"].id)
        dbms = db.get(Subject, dbms.id)
        os_subject = db.get(Subject, os_subject.id)
        syllabus_service.upload(
            db,
            actor=teacher,
            subject_id=dbms.id,
            filename="cs501-dbms-syllabus.txt",
            content_type="text/plain",
            data=DBMS_SYLLABUS.encode(),
            ip="127.0.0.1",
        )
        syllabus_service.upload(
            db,
            actor=teacher,
            subject_id=os_subject.id,
            filename="cs502-operating-systems-syllabus.txt",
            content_type="text/plain",
            data=OS_SYLLABUS.encode(),
            ip="127.0.0.1",
        )

    specs = _practice_questions()
    with session_scope() as db:
        teacher = db.get(User, users["teacher@praxis.demo"].id)
        dbms = db.get(Subject, dbms.id)
        os_subject = db.get(Subject, os_subject.id)
        dbms_topics = {topic.name: topic for topic in academic_service.topics_for_subject(db, dbms.id)}
        os_topics = {topic.name: topic for topic in academic_service.topics_for_subject(db, os_subject.id)}
        dbms_test_id = _make_test(
            db,
            teacher,
            dbms,
            dbms_topics,
            "DBMS Units 1-4 Practice",
            "Mixed practice across normalization, SQL, transactions and storage.",
            specs["dbms"],
        )
        os_test_id = _make_test(
            db,
            teacher,
            os_subject,
            os_topics,
            "OS Scheduling, Memory & File Systems",
            "Check your understanding of processes, paging and journaling.",
            specs["os"],
        )

    with session_scope() as db:
        student = db.get(User, users["student@praxis.demo"].id)
        student2 = db.get(User, users["aman@praxis.demo"].id)
        _attempt(db, dbms_test_id, student, {"Transactions & Concurrency Control"}, offset=1)
        _attempt(db, dbms_test_id, student2, {"Normalization & Functional Dependencies"}, offset=2)
        _attempt(db, os_test_id, student, {"Memory Management"}, offset=3)

    now = datetime.now(timezone.utc)
    with session_scope() as db:
        teacher = db.get(User, users["teacher@praxis.demo"].id)
        dbms = db.get(Subject, dbms.id)
        os_subject = db.get(Subject, os_subject.id)
        dbms_topics = {topic.name: topic.id for topic in academic_service.topics_for_subject(db, dbms.id)}
        os_topics = {topic.name: topic.id for topic in academic_service.topics_for_subject(db, os_subject.id)}

        release_exam = exam_service.create_exam(
            db,
            teacher,
            ExamConfigCreate(
                subject_id=dbms.id,
                title="DBMS Internal Assessment 1",
                exam_type="Internal Assessment 1",
                exam_date=now + timedelta(minutes=45),
                duration_minutes=90,
                venue="Examination Hall A",
                instructions="Answer all questions. Calculators are not permitted.",
                total_marks=30,
                pattern=[
                    PatternItem(marks=2, count=5, question_type=QuestionType.SHORT_ANSWER, label="Section A"),
                    PatternItem(marks=10, count=2, question_type=QuestionType.LONG_ANSWER, label="Section B"),
                ],
                topics=[ExamTopicIn(topic_id=topic_id, weight_percent=20) for topic_id in dbms_topics.values()],
                difficulty_distribution={
                    Difficulty.EASY: 30,
                    Difficulty.MEDIUM: 50,
                    Difficulty.HARD: 20,
                },
                bloom_distribution={
                    BloomLevel.REMEMBER: 20,
                    BloomLevel.UNDERSTAND: 30,
                    BloomLevel.APPLY: 30,
                    BloomLevel.ANALYZE: 20,
                },
                retry_limit=3,
            ),
        )
        db.commit()
        exam_service.submit_exam(db, teacher, release_exam.id, "127.0.0.1")
        db.commit()
        request = exam_service.queue_generation(db, teacher, release_exam.id, "127.0.0.1")
        db.commit()
        request_id = request.id

        upcoming = exam_service.create_exam(
            db,
            teacher,
            ExamConfigCreate(
                subject_id=dbms.id,
                title="DBMS Internal Assessment 2",
                exam_type="Internal Assessment 2",
                exam_date=now + timedelta(days=14),
                duration_minutes=90,
                venue="Examination Hall B",
                instructions="Answer all questions. Show all working.",
                total_marks=30,
                pattern=[
                    PatternItem(marks=2, count=5, question_type=QuestionType.SHORT_ANSWER, label="Section A"),
                    PatternItem(marks=10, count=2, question_type=QuestionType.LONG_ANSWER, label="Section B"),
                ],
                topics=[ExamTopicIn(topic_id=topic_id, weight_percent=20) for topic_id in dbms_topics.values()],
                difficulty_distribution={
                    Difficulty.EASY: 30,
                    Difficulty.MEDIUM: 50,
                    Difficulty.HARD: 20,
                },
                bloom_distribution={
                    BloomLevel.REMEMBER: 20,
                    BloomLevel.UNDERSTAND: 30,
                    BloomLevel.APPLY: 30,
                    BloomLevel.ANALYZE: 20,
                },
                retry_limit=3,
            ),
        )
        db.commit()

        os_draft = exam_service.create_exam(
            db,
            db.get(User, users["kavya@praxis.demo"].id),
            ExamConfigCreate(
                subject_id=os_subject.id,
                title="OS Internal Assessment 1",
                exam_type="Internal Assessment 1",
                exam_date=now + timedelta(days=10),
                duration_minutes=60,
                venue="Examination Hall C",
                instructions="Answer all questions.",
                total_marks=20,
                pattern=[
                    PatternItem(marks=2, count=5, question_type=QuestionType.SHORT_ANSWER, label="Section A"),
                    PatternItem(marks=10, count=1, question_type=QuestionType.LONG_ANSWER, label="Section B"),
                ],
                topics=[ExamTopicIn(topic_id=topic_id, weight_percent=25) for topic_id in os_topics.values()],
                difficulty_distribution={
                    Difficulty.EASY: 40,
                    Difficulty.MEDIUM: 40,
                    Difficulty.HARD: 20,
                },
                bloom_distribution={
                    BloomLevel.REMEMBER: 30,
                    BloomLevel.UNDERSTAND: 40,
                    BloomLevel.APPLY: 30,
                    BloomLevel.ANALYZE: 0,
                },
                retry_limit=2,
            ),
        )
        db.commit()
        upcoming_id, os_draft_id = upcoming.id, os_draft.id

    generation_service.run_generation(request_id)

    with session_scope() as db:
        from app.models.tables import QuestionPaper, GenerationRequest

        paper = (
            db.execute(select(QuestionPaper).where(QuestionPaper.exam_id == release_exam.id))
            .scalars()
            .first()
        )
        request = db.get(GenerationRequest, request_id)
        summary = {
            "users": int(db.execute(select(func.count(User.id))).scalar_one()),
            "subjects": int(db.execute(select(func.count(Subject.id))).scalar_one()),
            "papers": int(db.execute(select(func.count(QuestionPaper.id))).scalar_one()),
            "release_exam_id": release_exam.id,
            "upcoming_exam_id": upcoming_id,
            "os_draft_exam_id": os_draft_id,
            "paper_state": paper.state if paper else "MISSING",
            "generation_status": request.status if request else "MISSING",
        }
    logger.info("demo data seeded", extra={"event": "seed_complete", **summary})
    return summary


def seed_if_empty() -> bool:
    init_db()
    with session_scope() as db:
        existing = int(db.execute(select(func.count(User.id))).scalar_one())
    if existing:
        logger.info("seed skipped: database already has users", extra={"event": "seed_skipped"})
        return False
    seed_demo()
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(seed_if_empty() or "seed skipped (database not empty)")
