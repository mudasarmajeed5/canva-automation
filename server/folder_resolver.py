"""Deterministic Drive routing. All discovery is read-only; ambiguity stops delivery."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from models import AutomationError

FOLDER_MIME = 'application/vnd.google-apps.folder'
BUSINESSES = {
    'Excel Executive Cars': ('excel', 'excel car', 'excel executive cars'),
    'Platinum Exetuive Car': ('platinum', 'platinum executive cars', 'platinum exetuive car'),
    'Reading Chauffers': ('reading', 'reading chauffeurs', 'reading chauffers'),
    'First Class Cars': ('first class', 'first class cars'),
}
CONTENT = {
    'stories': ('stories', 'story', 'instagram stories', 'insta stories'),
    'posts': ('static posts', 'posts', 'static post'),
    'carousel': ('carousel', 'carousels'),
    'reels': ('reels', 'reel', 'videos', 'video reels'),
    'memes': ('memes', 'meme'),
    'facebook': ('facebook assets', 'fb assets'),
}
MONTHS = ('January February March April May June July August September October November December').split()


def normalize(value):
    return ' '.join(re.findall(r'[a-z0-9]+', value.casefold()))


def fail(code, message):
    raise AutomationError(code, message)


def classify(file_name, account, task_type, job_date=''):
    if account not in BUSINESSES:
        fail('DRIVE_ROUTE_UNKNOWN_BUSINESS', f'Unknown business account: {account}.')
    text = ' ' + normalize(file_name) + ' '
    found = {business for business, aliases in BUSINESSES.items()
             if any(' ' + normalize(alias) + ' ' in text for alias in aliases)}
    if found and found != {account}:
        fail('DRIVE_ROUTE_BUSINESS_CONFLICT',
             f'Filename {file_name!r} identifies {", ".join(sorted(found))}, but the Sheet account is {account}. Correct the account or Canva link.')
    types = set()
    if re.search(r'\b(story|stories|highlight|highlights)\b', text): types.add('stories')
    if re.search(r'\b(carousel|carousels)\b', text): types.add('carousel')
    if re.search(r'\b(reel|reels)\b', text) or file_name.lower().endswith('.mp4'): types.add('reels')
    if re.search(r'\b(meme|memes)\b', text): types.add('memes')
    if re.search(r'\b(post|posts|static)\b', text): types.add('posts')
    if re.search(r'\b(fb|facebook)\b', text) and re.search(r'\b(cover|coverpage|assets|asset)\b', text): types.add('facebook')
    if not types:
        types = {'reels'} if task_type == 'Video/Reel Editing' else set()
    if len(types) != 1:
        fail('DRIVE_ROUTE_CONTENT_AMBIGUOUS', f'Cannot determine one content type for {file_name!r}: {sorted(types)}.')
    content = next(iter(types))
    if content == 'facebook': return content, None
    # Filename date is the publication date; the job may run the day before.
    dates = re.findall(r'\b\d{1,2}[- _][A-Za-z]{3,9}[- _]\d{4}\b', file_name)
    if not dates and job_date: dates = [job_date]
    parsed = set()
    for value in dates:
        value = re.sub(r'[- _]', '-', value)
        for fmt in ('%d-%b-%Y', '%d-%B-%Y'):
            try:
                parsed.add(datetime.strptime(value, fmt).date()); break
            except ValueError: pass
        else: fail('DRIVE_ROUTE_DATE_INVALID', f'Invalid publication date: {value}.')
    if len(parsed) != 1:
        fail('DRIVE_ROUTE_DATE_AMBIGUOUS', f'Need one publication date for {file_name!r}; no current-month guess is allowed.')
    date = next(iter(parsed))
    return content, f'{MONTHS[date.month - 1]} {date.year}'


def _children(service, parent_id, *, folders_only=True):
    from drive import _execute
    result = []; page = None
    while True:
        kwargs = dict(q=(f"'{parent_id}' in parents and trashed=false"
                         + (f" and mimeType='{FOLDER_MIME}'" if folders_only else "")),
                      fields='nextPageToken,incompleteSearch,files(id,name,parents,mimeType,trashed,modifiedTime)', pageSize=1000,
                      supportsAllDrives=True, includeItemsFromAllDrives=True)
        if page: kwargs['pageToken'] = page
        response = _execute(lambda: service.files().list(**kwargs))
        if response.get('incompleteSearch'):
            fail('DRIVE_ROUTE_ACTIVITY_UNAVAILABLE', 'Drive returned an incomplete folder search; no destination guessed.')
        result.extend(response.get('files', []))
        page = response.get('nextPageToken')
        if not page: return result


def _latest_activity(service, folder, budget):
    """Newest Drive modifiedTime across a folder and its visible active subtree.

    Folder modifiedTime alone does not describe changes to existing descendants.
    Shortcuts are items, not traversal edges. Never follow them outside the subtree.
    """
    latest = None
    stack = [folder]; seen = set()
    while stack:
        item = stack.pop()
        if item['id'] in seen:
            fail('DRIVE_ROUTE_ACTIVITY_UNAVAILABLE', 'Folder activity traversal encountered a cycle.')
        seen.add(item['id'])
        budget[0] -= 1
        if budget[0] < 0:
            fail('DRIVE_ROUTE_ACTIVITY_UNAVAILABLE', 'Duplicate-folder activity scan exceeded 10000 items; no destination guessed.')
        raw = item.get('modifiedTime')
        if not raw:
            fail('DRIVE_ROUTE_ACTIVITY_UNAVAILABLE', f'Missing modification time for {item["id"]}; cannot compare duplicate folders.')
        try:
            stamp = datetime.fromisoformat(raw.replace('Z', '+00:00'))
            if stamp.tzinfo is None: raise ValueError('timezone required')
            stamp = stamp.astimezone(timezone.utc)
        except (ValueError, TypeError):
            fail('DRIVE_ROUTE_ACTIVITY_UNAVAILABLE', f'Invalid modification time for {item["id"]}.')
        latest = max(latest, stamp) if latest else stamp
        if item.get('mimeType') == FOLDER_MIME:
            stack.extend(_children(service, item['id'], folders_only=False))
    return latest


def _most_recent(service, candidates):
    budget = [10000]
    ranked = [(_latest_activity(service, candidate, budget), candidate) for candidate in candidates]
    newest = max(stamp for stamp, _ in ranked)
    winners = [node for stamp, node in ranked if stamp == newest]
    if len(winners) != 1:
        fail('DRIVE_ROUTE_FOLDER_AMBIGUOUS', 'Matching folders have identical latest activity; no upload performed.')
    import logging
    logging.getLogger(__name__).info('Selected duplicate folder %s (%s), latest subtree activity %s',
                                     winners[0]['name'], winners[0]['id'], newest.isoformat())
    return winners[0]


def _choose(service, parent, aliases, label):
    aliases = {normalize(a) for a in aliases}
    candidates = [item for item in _children(service, parent['id'])
                  if normalize(item['name']) in aliases]
    if not candidates:
        fail('DRIVE_FOLDER_NOT_FOUND',
             f'Expected exactly one {label} folder under {parent["path"]}; found {len(candidates)}. No upload performed.')
    selected = _most_recent(service, candidates) if len(candidates) > 1 else candidates[0]
    node = dict(selected); node['path'] = parent['path'] + '/' + node['name']
    return node


def resolve_target(service, root_id, file_name, account, task_type, job_date=''):
    """Resolve only exact aliases along root/business/month/type; never create folders."""
    from drive import _execute
    content, month = classify(file_name, account, task_type, job_date)
    root = _execute(lambda: service.files().get(fileId=root_id,
        fields='id,name,mimeType,trashed', supportsAllDrives=True))
    if root.get('trashed') or root.get('mimeType') != FOLDER_MIME:
        fail('DRIVE_FOLDER_MISSING', 'Configured Drive root is not an active folder.')
    root['path'] = root['name']
    business = _choose(service, root, BUSINESSES[account], 'business')
    parent = _choose(service, business, [month], 'publication month') if month else business
    target = _choose(service, parent, CONTENT[content], content)
    log_message = f'Deterministic route: {target["path"]} (folder ID {target["id"]})'
    return target['id'], target['path'], [log_message]


def verify_parent(service, file_id, expected_parent):
    from drive import _execute
    info = _execute(lambda: service.files().get(fileId=file_id,
        fields='id,parents,trashed', supportsAllDrives=True))
    if info.get('trashed') or info.get('parents') != [expected_parent]:
        fail('DRIVE_PARENT_MISMATCH', f'Drive item {file_id} is not in the verified destination folder. Delivery was not marked successful.')
