import json
import logging
import shutil
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

import history_feature

JOB_ID = "a" * 32


class HistoryFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.trans_dir = self.root / "transcriptions"
        self.trans_dir.mkdir()
        self.templates_dir = Path(__file__).resolve().parents[1] / "templates"
        self.server = SimpleNamespace(
            DATA_DIR=self.root,
            TRANS_DIR=self.trans_dir,
            JOBS={},
            JOBS_LOCK=threading.RLock(),
            templates=Jinja2Templates(directory=str(self.templates_dir)),
            logger=logging.getLogger("history-feature-test"),
        )
        self.server._public_job_snapshot = lambda job: deepcopy(job)
        self.server._run_job_from_queue = lambda job_id, api_key: None

    def tearDown(self):
        self.temp.cleanup()

    def _seed_job(self, text="Décision importante sur le planning."):
        source_dir = self.trans_dir / JOB_ID
        source_dir.mkdir(parents=True, exist_ok=True)
        (source_dir / "transcription_Reunion.txt").write_text(text, encoding="utf-8")
        self.server.JOBS[JOB_ID] = {
            "status": "done",
            "created_at": "2026-09-15T08:30:00+00:00",
            "finished_at": "2026-09-15T08:34:00+00:00",
            "use_api": False,
            "model": "small",
            "lang": "fr",
            "output_type": "transcription",
            "output_name": "Réunion projet",
            "files": [{
                "name": "reunion.wav",
                "size": 1234,
                "status": "done",
                "error": None,
                "transcription_available": True,
                "document_available": False,
            }],
        }
        return source_dir

    def test_archive_survives_source_retention_and_full_text_search(self):
        source_dir = self._seed_job()
        record = history_feature._archive_job(self.server, JOB_ID)
        self.assertEqual(record["title"], "Réunion projet")
        self.assertEqual(record["artifacts"][0]["kind"], "transcription")
        shutil.rmtree(source_dir)
        archive = self.root / "history" / JOB_ID / "transcription_Reunion.txt"
        self.assertTrue(archive.exists())
        matches = history_feature._list_history_records(
            self.server,
            start="2026-09-15T00:00:00+00:00",
            end="2026-09-16T00:00:00+00:00",
            query="planning",
        )
        self.assertEqual([item["id"] for item in matches], [JOB_ID])

    def test_backfill_recovers_existing_txt_without_job_memory(self):
        source_dir = self.trans_dir / JOB_ID
        source_dir.mkdir()
        (source_dir / "transcription_Ancienne_reunion.txt").write_text("Ancien transcript", encoding="utf-8")
        recovered = history_feature._backfill_existing_transcriptions(self.server)
        self.assertEqual(recovered, 1)
        record = json.loads((self.root / "history" / JOB_ID / "metadata.json").read_text(encoding="utf-8"))
        self.assertTrue(record["recovered"])
        self.assertIn("Ancienne reunion", record["title"])

    def test_artifact_lookup_rejects_unregistered_names(self):
        self._seed_job()
        history_feature._archive_job(self.server, JOB_ID)
        with self.assertRaises(HTTPException) as error:
            history_feature._artifact_path(self.server, JOB_ID, "metadata.json")
        self.assertEqual(error.exception.status_code, 404)

    def test_installed_api_lists_reads_and_downloads_history(self):
        self._seed_job("Phrase unique retrouvable dans le transcript.")
        history_feature._archive_job(self.server, JOB_ID)
        app = FastAPI()
        history_feature.install_history_feature(app, self.server)
        client = TestClient(app)
        page = client.get("/history")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Historique des transcriptions", page.text)
        response = client.get("/api/history", params={
            "start": "2026-09-15T00:00:00+00:00",
            "end": "2026-09-16T00:00:00+00:00",
            "q": "phrase unique",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["count"], 1)
        detail = client.get(f"/api/history/{JOB_ID}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["title"], "Réunion projet")
        content = client.get(f"/api/history/{JOB_ID}/content/transcription_Reunion.txt")
        self.assertEqual(content.status_code, 200)
        self.assertIn("Phrase unique", content.text)
        download = client.get(f"/api/history/{JOB_ID}/download/transcription_Reunion.txt")
        self.assertEqual(download.status_code, 200)
        self.assertIn("attachment", download.headers.get("content-disposition", ""))

    def test_queue_wrapper_archives_after_job_runner(self):
        self._seed_job()

        def original(job_id, api_key):
            self.server.JOBS[job_id]["finished_at"] = "2026-09-15T08:35:00+00:00"

        self.server._run_job_from_queue = original
        app = FastAPI()
        history_feature.install_history_feature(app, self.server)
        self.server._run_job_from_queue(JOB_ID, None)
        metadata = json.loads((self.root / "history" / JOB_ID / "metadata.json").read_text(encoding="utf-8"))
        self.assertEqual(metadata["finished_at"], "2026-09-15T08:35:00+00:00")


if __name__ == "__main__":
    unittest.main()
