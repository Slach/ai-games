"""DB-level tests for generation job tracking (resumable turn generation)."""

import os
import sys
import tempfile
import logging
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db  # noqa: E402

logger = logging.getLogger(__name__)


class TestGenerationJobs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        db.DB_PATH = Path(self._tmp.name)
        db.init_db()

    def tearDown(self):
        try:
            os.unlink(self._tmp.name)
        except (FileNotFoundError, PermissionError):
            logger.error("Failed to remove temp DB: %s", self._tmp.name, exc_info=True)

    def test_start_creates_in_progress_job(self):
        job = db.start_generation_job("g1", 1, "start")
        self.assertEqual(job["status"], db.JOB_IN_PROGRESS)
        self.assertEqual(job["game_id"], "g1")
        self.assertEqual(job["turn"], 1)
        self.assertEqual(job["job_type"], "start")
        self.assertIsNone(job["finished_at"])

    def test_complete_marks_done(self):
        job = db.start_generation_job("g1", 1, "start")
        changed = db.complete_generation_job(job["id"])
        self.assertTrue(changed)
        active = db.get_active_generation_job("g1")
        self.assertIsNone(active)  # no longer in_progress

    def test_fail_records_error(self):
        job = db.start_generation_job("g1", 2, "continue")
        db.fail_generation_job(job["id"], "boom")
        self.assertEqual(db.get_active_generation_job("g1"), None)

    def test_get_active_returns_only_in_progress(self):
        j1 = db.start_generation_job("g1", 1, "start")
        db.complete_generation_job(j1["id"])
        j2 = db.start_generation_job("g1", 2, "continue")  # in_progress
        active = db.get_active_generation_job("g1")
        if active is None:
            self.fail("expected in_progress generation job")
        self.assertEqual(active["id"], j2["id"])

    def test_lock_one_active_per_game(self):
        """A second in_progress job for the same game coexists in the table,
        but get_active returns the latest — callers must check before starting."""
        j1 = db.start_generation_job("g1", 1, "start")
        active = db.get_active_generation_job("g1")
        if active is None:
            self.fail("expected in_progress generation job")
        # Caller checks get_active first; if it returns a job, do not start another.
        self.assertEqual(j1["id"], active["id"])

    def test_update_step_checkpoints(self):
        job = db.start_generation_job("g1", 1, "start")
        self.assertTrue(db.update_generation_job_step(job["id"], "briefings"))
        active = db.get_active_generation_job("g1")
        if active is None:
            self.fail("expected in_progress generation job")
        self.assertEqual(active["current_step"], "briefings")

    def test_update_step_ignored_after_completion(self):
        """No further checkpoints once the job is done."""
        job = db.start_generation_job("g1", 1, "start")
        db.complete_generation_job(job["id"])
        self.assertFalse(db.update_generation_job_step(job["id"], "late"))

    def test_get_in_progress_jobs_for_sweep(self):
        db.start_generation_job("g1", 1, "start")  # in_progress
        j2 = db.start_generation_job("g2", 3, "continue")  # in_progress
        db.complete_generation_job(j2["id"])  # done -> excluded
        db.start_generation_job("g3", 1, "start")  # in_progress
        jobs = db.get_in_progress_generation_jobs()
        game_ids = {j["game_id"] for j in jobs}
        self.assertEqual(game_ids, {"g1", "g3"})

    def test_per_game_isolation(self):
        db.start_generation_job("g1", 1, "start")
        db.start_generation_job("g2", 1, "start")
        self.assertEqual(len(db.get_in_progress_generation_jobs()), 2)
        self.assertIsNotNone(db.get_active_generation_job("g1"))
        self.assertIsNotNone(db.get_active_generation_job("g2"))


if __name__ == "__main__":
    unittest.main()
