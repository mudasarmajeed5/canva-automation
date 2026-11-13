"""Offline regression tests for safe retry after a partial delivery."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from jobs import JobStore
from models import RowInput
import pipeline


class RetryStoreTests(unittest.TestCase):
    def test_retry_preserves_completed_rows_and_upload_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / 'jobs.db')
            rows = [RowInput(str(i), 'A', 'T', 'Description', 'Not Started', 'https://canva.com/design/test') for i in range(2)]
            store.create_job('job', '13-Nov-2026', None, rows, {'0': 2, '1': 3})
            store.set_row_state('job', '0', 'done')
            store.set_row_state('job', '1', 'failed', drive_url='https://drive.google.com/file/d/test/view', error='Sheet failed')
            store.set_job_state('job', 'done_with_errors')
            self.assertEqual(store.retry_failed_job('job'), 1)
            job = store.get_job('job')
            self.assertEqual(job['state'], 'queued')
            self.assertEqual(job['rows'][0]['state'], 'done')
            self.assertEqual(job['rows'][1]['state'], 'pending')
            self.assertEqual(job['rows'][1]['drive_url'], 'https://drive.google.com/file/d/test/view')
            self.assertIsNone(job['rows'][1]['error'])
            with self.assertRaises(ValueError):
                store.retry_failed_job('job')
            store.close()

    def test_missing_job(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JobStore(Path(directory) / 'jobs.db')
            with self.assertRaises(KeyError):
                store.retry_failed_job('missing')
            store.close()


class PipelineResumeTests(unittest.TestCase):
    def test_existing_upload_only_retries_sheet_even_for_replace(self):
        for replace in [False, True]:
            with self.subTest(replace=replace):
                row = {'row_id': 'row', 'replace': replace, 'drive_url': 'https://drive.google.com/file/d/test/view', 'sheet_row': 5}
                store = Mock()
                with patch.object(pipeline.canva_bot, 'download_design') as download, patch.object(pipeline.drive, 'upload_and_share') as upload, patch.object(pipeline.drive, 'replace_file') as replacement, patch.object(pipeline.sheets, 'set_drive_link') as link, patch.object(pipeline.sheets, 'set_status') as status:
                    asyncio.run(pipeline._process_row('job', row, 'fresh-token', Path('/tmp'), store, [], 'sheet'))
                    download.assert_not_called()
                    upload.assert_not_called()
                    replacement.assert_not_called()
                    link.assert_called_once_with('fresh-token', 5, row['drive_url'], 'sheet')
                    status.assert_called_once_with('fresh-token', 5, 'Done', 'sheet')
                    self.assertEqual(store.set_row_state.call_args.args[2], 'done')


if __name__ == '__main__':
    unittest.main()
