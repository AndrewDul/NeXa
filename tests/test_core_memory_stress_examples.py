"""Domain stress tests (R0073 §13/§15): prove the schema represents
NeXa Teacher, LiFeOS, and Projects shapes end-to-end through the real
service, without any Teacher/LiFeOS/Projects code existing. Not a claim
that those domains are implemented -- only that the Memory Platform
foundation does not need a schema change to support them.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from nexa.core.memory.models import (  # noqa: E402
    MemoryCategory,
    MemoryProvenance,
    MemoryWriteTrigger,
)
from nexa.core.memory.repository import MemoryRepository  # noqa: E402
from nexa.core.memory.service import MemoryService  # noqa: E402
from nexa.core.privacy import CloudEligibility  # noqa: E402
from nexa.core.storage.sqlite import connect  # noqa: E402


class _StressTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self._tmp.name) / "core.sqlite3")
        self.service = MemoryService(MemoryRepository(self.conn))

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()


class TestTeacherStressExample(_StressTestCase):
    def test_teacher_shapes_and_relations(self) -> None:
        concept = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="teacher.python", category=MemoryCategory.STATE, record_type="concept",
            scope_type="concept", scope_id="recursion", content="Concept: recursion (Python)",
            provenance=MemoryProvenance.SYSTEM_OBSERVATION,
        )
        mastery = self.service.remember(
            MemoryWriteTrigger.DURABLE_CANDIDATE,
            namespace="teacher.python", category=MemoryCategory.STATE, record_type="skill_mastery",
            scope_type="skill", scope_id="recursion", content="Recursion mastery: 55% (8 attempts)",
            payload={"skill": "recursion", "mastery": 0.55, "attempt_count": 8}, payload_version=1,
            provenance=MemoryProvenance.SYSTEM_OBSERVATION, source_kind="teacher_exercise_attempt",
            confidence=0.9,
        )
        lesson_event = self.service.remember(
            MemoryWriteTrigger.DURABLE_CANDIDATE,
            namespace="teacher.python", category=MemoryCategory.EVENT, record_type="lesson_event",
            scope_type="lesson", scope_id="python_recursion_101",
            content="Completed lesson: Python Recursion 101",
            provenance=MemoryProvenance.SYSTEM_OBSERVATION, source_kind="lesson_engine",
            confidence=1.0,
        )
        mistake = self.service.remember(
            MemoryWriteTrigger.DURABLE_CANDIDATE,
            namespace="teacher.python", category=MemoryCategory.EPISODE, record_type="mistake",
            content="Off-by-one error in recursive base case", payload={"exercise": "fib_sequence"},
            provenance=MemoryProvenance.CONVERSATION_DERIVED, confidence=0.85,
        )

        self.service.relate(lesson_event.memory_id, "teaches", concept.memory_id)
        self.service.relate(mistake.memory_id, "concerns", concept.memory_id)

        related = self.service.related_to(concept.memory_id, limit=10)
        self.assertEqual({r.relation_type for r in related}, {"teaches", "concerns"})

        mastery_record = self.service.by_id(mastery.memory_id)
        self.assertEqual(mastery_record.payload["mastery"], 0.55)
        self.assertEqual(mastery_record.category, MemoryCategory.STATE)

        mistake_record = self.service.by_id(mistake.memory_id)
        self.assertEqual(mistake_record.category, MemoryCategory.EPISODE)

        event_record = self.service.by_id(lesson_event.memory_id)
        self.assertEqual(event_record.category, MemoryCategory.EVENT)

    def test_category_independent_of_provenance(self) -> None:
        """The same real-world occurrence must classify the same way
        whether the user reported it or a system observed it (R0073 final
        review §3)."""
        via_user = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="teacher.python", category=MemoryCategory.EVENT, record_type="lesson_event",
            content="Completed lesson: Loops 101",
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        via_system = self.service.remember(
            MemoryWriteTrigger.DURABLE_CANDIDATE,
            namespace="teacher.python", category=MemoryCategory.EVENT, record_type="lesson_event",
            content="Completed lesson: Recursion 101",
            provenance=MemoryProvenance.SYSTEM_OBSERVATION,
            confidence=1.0,
        )
        self.assertEqual(
            self.service.by_id(via_user.memory_id).category,
            self.service.by_id(via_system.memory_id).category,
        )


class TestLiFeOSStressExample(_StressTestCase):
    def test_lifeos_shapes(self) -> None:
        routine = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="lifeos.routines", category=MemoryCategory.STATE, record_type="routine",
            scope_type="routine", scope_id="exercise", content="Exercise routine: 3x/week",
            payload={"activity": "exercise", "frequency": "3_per_week"},
            valid_from=datetime(2026, 1, 1, tzinfo=UTC),
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.assertIsNotNone(self.service.by_id(routine.memory_id))

        goal = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="lifeos.goals", category=MemoryCategory.STATE, record_type="goal",
            scope_type="goal", scope_id="run_5k",
            content="Goal: run a 5k by end of year",
            payload={"target_date": "2026-12-31", "status": "in_progress"},
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        project = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="lifeos.projects", category=MemoryCategory.STATE, record_type="project",
            scope_type="project", scope_id="garden_rebuild", content="Project: rebuild the garden",
            payload={"status": "active"}, provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.service.relate(goal.memory_id, "belongs_to", project.memory_id)
        self.assertEqual(len(self.service.related_to(project.memory_id, limit=10)), 1)

        life_event = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="lifeos.events", category=MemoryCategory.EPISODE, record_type="life_event",
            content="Moved to a new apartment", valid_from=datetime(2026, 6, 1, tzinfo=UTC),
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.assertEqual(self.service.by_id(life_event.memory_id).category, MemoryCategory.EPISODE)

        preference = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="user", category=MemoryCategory.PREFERENCE, record_type="preference",
            content="Prefers morning workout reminders",
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        preference_record = self.service.by_id(preference.memory_id)
        self.assertEqual(preference_record.category, MemoryCategory.PREFERENCE)

        health = self.service.remember(
            MemoryWriteTrigger.DURABLE_CANDIDATE,
            namespace="lifeos.health", category=MemoryCategory.EVENT,
            record_type="health_observation",
            content="Resting heart rate 58 bpm",
            payload={"metric": "resting_hr", "value": 58, "unit": "bpm"},
            provenance=MemoryProvenance.SYSTEM_OBSERVATION, source_kind="device_sensor",
            confidence=0.95, cloud_eligibility=CloudEligibility.LOCAL_ONLY,
        )
        health_record = self.service.by_id(health.memory_id)
        self.assertEqual(health_record.cloud_eligibility, CloudEligibility.LOCAL_ONLY)

        finance = self.service.remember(
            MemoryWriteTrigger.DURABLE_CANDIDATE,
            namespace="lifeos.finance", category=MemoryCategory.EVENT,
            record_type="financial_event",
            content="Grocery purchase: £42.10", payload={"amount": 42.10, "currency": "GBP"},
            provenance=MemoryProvenance.IMPORTED, source_kind="import_file",
            source_ref="bank_export_2026_09.csv:row_118", confidence=1.0,
            cloud_eligibility=CloudEligibility.LOCAL_ONLY,
        )
        finance_record = self.service.by_id(finance.memory_id)
        self.assertEqual(finance_record.cloud_eligibility, CloudEligibility.LOCAL_ONLY)


class TestProjectsAndDeviceStressExample(_StressTestCase):
    def test_projects_decision_and_milestone(self) -> None:
        decision = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="projects.nexa", category=MemoryCategory.EPISODE, record_type="decision",
            content="Decided: single MemoryService authority, no per-domain databases",
            valid_from=datetime(2026, 9, 16, tzinfo=UTC),
            provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.assertEqual(self.service.by_id(decision.memory_id).category, MemoryCategory.EPISODE)

        milestone = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="projects.nexa", category=MemoryCategory.STATE, record_type="milestone",
            scope_type="project", scope_id="nexa_ikigai", content="Milestone: M3.2 design approved",
            payload={"status": "done"}, provenance=MemoryProvenance.EXPLICIT_USER_STATEMENT,
        )
        self.assertEqual(self.service.by_id(milestone.memory_id).category, MemoryCategory.STATE)

    def test_device_firmware_fact_and_battery_state(self) -> None:
        firmware = self.service.remember(
            MemoryWriteTrigger.EXPLICIT_REMEMBER_REQUEST,
            namespace="devices.respeaker", category=MemoryCategory.FACT,
            record_type="firmware_version",
            content="reSpeaker XVF3800 firmware: 1.3",
            valid_from=datetime(2026, 9, 16, tzinfo=UTC),
            provenance=MemoryProvenance.SYSTEM_OBSERVATION,
        )
        firmware_record = self.service.by_id(firmware.memory_id)
        self.assertEqual(firmware_record.category, MemoryCategory.FACT)

        battery = self.service.remember(
            MemoryWriteTrigger.DURABLE_CANDIDATE,
            namespace="devices.respeaker", category=MemoryCategory.STATE,
            record_type="battery_level",
            content="Battery: 82%", payload={"percent": 82},
            provenance=MemoryProvenance.SYSTEM_OBSERVATION, source_kind="device_sensor",
            confidence=1.0,
        )
        battery_record = self.service.by_id(battery.memory_id)
        self.assertEqual(battery_record.category, MemoryCategory.STATE)


if __name__ == "__main__":
    unittest.main()
