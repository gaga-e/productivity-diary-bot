import unittest
import time
from datetime import datetime, timezone

import config as cfg
import job_cache as cache
from job_scraper import parse_job_query, normalize_job, job_hash, dedupe_jobs, scrape_all_boards
from handlers_job import _status_summary, _format_job, _build_page_text, _build_keyboard


class TestJobSearchFeature(unittest.TestCase):

    def test_parse_job_query(self):
        # Default query
        kw, loc = parse_job_query("python backend")
        self.assertEqual(kw, "python backend")
        self.assertEqual(loc, "Remote")

        # Exact phrase in quotes
        kw, loc = parse_job_query('"senior backend engineer"')
        self.assertEqual(kw, "senior backend engineer")
        self.assertEqual(loc, "Remote")

        # Custom location flag
        kw, loc = parse_job_query('python backend --loc="United Kingdom"')
        self.assertEqual(kw, "python backend")
        self.assertEqual(loc, "United Kingdom")

        # Empty args
        kw, loc = parse_job_query("")
        self.assertIsNone(kw)
        self.assertEqual(loc, "Remote")

    def test_job_normalization(self):
        # LinkedIn / Indeed raw mock
        raw_linkedin = {
            "title": "Backend Engineer",
            "company": "Tech Corp",
            "location": "Remote",
            "job_url": "https://linkedin.com/jobs/view/123?tracking=xyz",
            "site": "linkedin",
            "date_posted": "2026-08-14"
        }
        norm = normalize_job(raw_linkedin, "linkedin")
        self.assertEqual(norm["title"], "Backend Engineer")
        self.assertEqual(norm["source"], "linkedin")
        self.assertEqual(norm["link"], "https://linkedin.com/jobs/view/123?tracking=xyz")

        # RemoteOK raw mock
        raw_remoteok = {
            "position": "Python Developer",
            "company": "StartupX",
            "location": "Worldwide",
            "url": "https://remoteok.com/remote-jobs/999",
            "epoch": 1700000000
        }
        norm_remoteok = normalize_job(raw_remoteok, "remoteok")
        self.assertEqual(norm_remoteok["title"], "Python Developer")
        self.assertEqual(norm_remoteok["source"], "remoteok")

    def test_deduplication(self):
        # Two jobs with identical title, company, location but different tracking URLs
        job1 = {
            "title": "Python Developer",
            "company": "Acme Inc",
            "location": "Remote",
            "link": "https://example.com/apply?utm_source=board1",
            "source": "adzuna",
            "date_posted": "2026-08-14T10:00:00"
        }
        job2 = {
            "title": "Python Developer ",  # Notice trailing space
            "company": "ACME INC",        # Uppercase
            "location": "remote",         # Lowercase
            "link": "https://example.com/apply?utm_source=board2",
            "source": "jooble",
            "date_posted": "2026-08-14T09:00:00"
        }
        job3 = {
            "title": "Frontend Engineer",
            "company": "Acme Inc",
            "location": "Remote",
            "link": "https://example.com/apply2",
            "source": "linkedin",
            "date_posted": "2026-08-14T11:00:00"
        }

        self.assertEqual(job_hash(job1), job_hash(job2))
        self.assertNotEqual(job_hash(job1), job_hash(job3))

        deduped = dedupe_jobs([job1, job2, job3])
        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0]["title"], "Python Developer")
        self.assertEqual(deduped[1]["title"], "Frontend Engineer")

    def test_job_cache_and_cooldown(self):
        test_chat_id = 999888777

        # Cooldown check initially 0
        self.assertEqual(cache.seconds_until_next_allowed(test_chat_id), 0)

        # Mark search started -> cooldown active
        cache.mark_search_started(test_chat_id)
        cooldown = cache.seconds_until_next_allowed(test_chat_id)
        self.assertGreater(cooldown, 0)
        self.assertLessEqual(cooldown, cfg.JOB_SEARCH_COOLDOWN_SECONDS)

        # Test search caching
        mock_jobs = [{"title": "Dev", "company": "Co", "location": "Remote", "link": "", "source": "test"}]
        mock_status = {"test": "ok"}
        cache.cache_search_results("python backend", "Remote", mock_jobs, mock_status)

        cached_res = cache.get_cached_search("python backend", "Remote")
        self.assertIsNotNone(cached_res)
        jobs, status = cached_res
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["title"], "Dev")

    def test_html_formatting_and_escaping(self):
        # Job with dangerous HTML/Markdown characters
        tricky_job = {
            "title": "Senior C++ / C# Developer <Remote> & [Lead]",
            "company": "R&D Corp *Specialists*",
            "location": "US / UK",
            "link": "https://example.com/job?id=123&ref=456",
            "source": "adzuna"
        }

        formatted = _format_job(tricky_job)
        # Verify < and > and & are escaped properly
        self.assertIn("Senior C++ / C# Developer &lt;Remote&gt; &amp; [Lead]", formatted)
        self.assertIn("R&amp;D Corp *Specialists*", formatted)
        self.assertIn("https://example.com/job?id=123&amp;ref=456", formatted)

    def test_pagination_builder(self):
        jobs = [
            {"title": f"Job {i}", "company": f"Company {i}", "location": "Remote", "link": f"http://job{i}.com", "source": "test"}
            for i in range(25)
        ]
        
        # Page 0 (10 items)
        page0_text = _build_page_text(jobs, 0, 10, header="Header Text")
        self.assertIn("Header Text", page0_text)
        self.assertIn("Job 0", page0_text)
        self.assertIn("Job 9", page0_text)
        self.assertNotIn("Job 10", page0_text)

        # Keyboard for page 0
        kb = _build_keyboard("search123", 0, 3)
        self.assertIsNotNone(kb)
        self.assertEqual(len(kb.inline_keyboard[0]), 1)
        self.assertEqual(kb.inline_keyboard[0][0].text, "Next ➡️")

        # Keyboard for page 1 (middle page)
        kb_mid = _build_keyboard("search123", 1, 3)
        self.assertEqual(len(kb_mid.inline_keyboard[0]), 2)
        self.assertEqual(kb_mid.inline_keyboard[0][0].text, "⬅️ Prev")
        self.assertEqual(kb_mid.inline_keyboard[0][1].text, "Next ➡️")


if __name__ == '__main__':
    unittest.main()
