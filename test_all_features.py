import os
import time
import unittest
from datetime import datetime

import database as db
import scheduler as sched
import job_scraper as scraper
import job_cache as cache
from config import HABITS


class TestFullBotSuite(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Force SQLite local database for clean unit testing
        db.db = None
        db.init_db()

    def test_01_user_registration(self):
        test_chat_id = 123456789
        db.register_user_chat_id(test_chat_id)
        user_ids = db.get_all_user_chat_ids()
        self.assertIn(test_chat_id, user_ids)

    def test_02_habits_and_tasks(self):
        db.init_today(list(HABITS.keys()))
        todos_initial = db.get_today_todos()
        self.assertGreater(len(todos_initial), 0)

        # Add a custom task with a unique timestamp
        task_name = f"Test task {time.time()}"
        db.add_task(task_name)
        todos = db.get_today_todos()
        task = next((t for t in todos if t['name'] == task_name), None)
        self.assertIsNotNone(task)
        self.assertEqual(task['done'], 0)

        # Toggle task done
        res = db.toggle_todo(task['id'])
        self.assertIsNotNone(res)
        self.assertEqual(res['done'], 1)

    def test_03_streaks(self):
        # Toggle a habit to increment streak
        todos = db.get_today_todos()
        habit = next((t for t in todos if t['type'] == 'habit'), None)
        self.assertIsNotNone(habit)

        db.toggle_todo(habit['id'])
        streaks = db.get_streaks()
        self.assertIn(habit['name'].lower(), streaks)
        self.assertGreaterEqual(streaks[habit['name'].lower()], 1)

    def test_04_mood_and_notes(self):
        db.log_mood(5, "🤩")
        db.add_note("Testing automated test suite execution")

        mood, notes = db.get_today_logs()
        self.assertIsNotNone(mood)
        self.assertEqual(mood['score'], 5)
        self.assertGreater(len(notes), 0)
        self.assertEqual(notes[-1]['content'], "Testing automated test suite execution")

    def test_05_pdf_export(self):
        pdf_file = db.export_to_pdf()
        self.assertTrue(os.path.exists(pdf_file))
        self.assertTrue(pdf_file.endswith(".pdf"))
        # Cleanup test file
        if os.path.exists(pdf_file):
            os.remove(pdf_file)

    def test_06_scheduler_initialization(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            test_chat_id = 987654321
            scheduler_inst = sched.setup_scheduler(None, chat_id=test_chat_id)
            self.assertIsNotNone(scheduler_inst)
            
            # Verify user was registered
            all_users = db.get_all_user_chat_ids()
            self.assertIn(test_chat_id, all_users)

            # Check job triggers in APScheduler
            jobs = scheduler_inst.get_jobs()
            job_ids = [j.id for j in jobs]
            self.assertTrue(any(j.startswith("morning_") for j in job_ids))
            self.assertTrue(any(j.startswith("evening_") for j in job_ids))
            self.assertTrue(any(j.startswith("nudge_") for j in job_ids))
        finally:
            loop.close()
            asyncio.set_event_loop(None)

    def test_07_job_search_engine(self):
        query, loc = scraper.parse_job_query("python developer --loc=\"Remote\"")
        self.assertEqual(query, "python developer")
        self.assertEqual(loc, "Remote")

        # Test remoteok API endpoint
        remote_jobs = scraper.fetch_remoteok_jobs("python", hours=72)
        self.assertIsInstance(remote_jobs, list)

        # Test full concurrency orchestration & deduplication
        jobs, status = scraper.scrape_all_boards("python", location="Remote", hours=72, limit=5)
        self.assertIsInstance(jobs, list)
        self.assertIsInstance(status, dict)
        self.assertIn("remoteok", status)


if __name__ == '__main__':
    unittest.main()
