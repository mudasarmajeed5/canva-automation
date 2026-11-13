"""No-network durable submission replay checks."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import idempotency

class Payload(BaseModel):
    value: str
    google_access_token: str

class ReplayTests(unittest.TestCase):
    def test_replay_and_conflict(self):
        async def run():
            with tempfile.TemporaryDirectory() as directory:
                with patch.object(idempotency, 'get_settings', return_value=SimpleNamespace(db_path=Path(directory)/'jobs.db')):
                    request=SimpleNamespace(headers={'Idempotency-Key':'one'},url=SimpleNamespace(path='/api/download'))
                    calls=[]
                    async def operation():
                        calls.append(1)
                        return JSONResponse({'job_id':'same'},status_code=202)
                    a=await idempotency.execute(request,Payload(value='a',google_access_token='old'),operation)
                    b=await idempotency.execute(request,Payload(value='a',google_access_token='new'),operation)
                    c=await idempotency.execute(request,Payload(value='b',google_access_token='new'),operation)
                    self.assertEqual(a.body,b.body)
                    self.assertEqual(c.status_code,409)
                    self.assertEqual(len(calls),1)
        asyncio.run(run())

if __name__=='__main__': unittest.main()
