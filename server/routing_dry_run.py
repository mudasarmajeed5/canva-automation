"""Read-only replay of saved jobs against current Drive folders. Never uploads."""
from __future__ import annotations

import argparse
import getpass
import json
import sqlite3
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from folder_resolver import resolve_target
from models import AutomationError


class ReadOnlyDrive:
    """Expose only metadata get/list, making accidental writes impossible here."""
    def __init__(self, service): self.service = service
    def files(self): return self
    def get(self, **kwargs): return self.service.files().get(**kwargs)
    def list(self, **kwargs): return self.service.files().list(**kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job_id')
    parser.add_argument('--db', type=Path, default=Path(__file__).parent/'data/jobs.db')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    token = getpass.getpass('Google access token (hidden; never saved): ')
    service = ReadOnlyDrive(build('drive','v3', credentials=Credentials(token=token),cache_discovery=False))
    with sqlite3.connect(args.db.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.row_factory = sqlite3.Row
        job = db.execute('SELECT date,drive_folder_id FROM jobs WHERE job_id=?',(args.job_id,)).fetchone()
        if job is None: parser.error('Job not found')
        rows = db.execute('SELECT file_name,account,task_type FROM job_rows WHERE job_id=? ORDER BY position',(args.job_id,)).fetchall()
    from config import get_settings
    root = job['drive_folder_id'] or get_settings().root_folder_id
    results = []
    for row in rows:
        result = dict(row)
        try:
            id, path, warnings = resolve_target(service,root,row['file_name'] or '',row['account'],row['task_type'],job['date'])
            result.update(status='READY',folder_id=id,path=path)
        except AutomationError as exc:
            result.update(status='BLOCKED',code=exc.code,reason=exc.message)
        results.append(result)
    report = json.dumps(dict(job_id=args.job_id,mode='read-only; no uploads',results=results),indent=2)
    print(report)
    if args.output: args.output.write_text(report+'\n')


if __name__ == '__main__': main()
