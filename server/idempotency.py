"""Durable replay of mutating requests. Never store Google credentials."""
import asyncio
import hashlib
import json
import sqlite3
from fastapi.responses import JSONResponse, Response
from config import get_settings

_lock = asyncio.Lock()

async def execute(request, payload, operation):
    key = request.headers.get('Idempotency-Key', '').strip()
    if not key:
        return await operation()
    if len(key) > 200:
        return JSONResponse({'message': 'Invalid submission identifier.'}, status_code=400)
    content = payload.model_dump(exclude={'google_access_token'})
    fingerprint = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    identity = request.url.path + ':' + key
    async with _lock:
        path = get_settings().db_path.with_name('submissions.sqlite3')
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS submissions (id TEXT PRIMARY KEY, fingerprint TEXT, status INTEGER, body BLOB)')
            prior = db.execute('SELECT fingerprint,status,body FROM submissions WHERE id=?', (identity,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    return JSONResponse({'message': 'This submission identifier belongs to different content.'}, status_code=409)
                if prior[1] is not None:
                    return Response(prior[2], status_code=prior[1], media_type='application/json')
                return JSONResponse({'error': 'submission_uncertain', 'message': 'This submission may already have been processed. Refresh records before submitting again.'}, status_code=409)
            db.execute('INSERT INTO submissions VALUES (?,?,NULL,NULL)', (identity, fingerprint))
            db.commit()
            response = await operation()
            if response.status_code < 300:
                db.execute('UPDATE submissions SET status=?,body=? WHERE id=?', (response.status_code, response.body, identity))
            elif response.status_code < 500:
                db.execute('DELETE FROM submissions WHERE id=?', (identity,))
            db.commit()
            return response
