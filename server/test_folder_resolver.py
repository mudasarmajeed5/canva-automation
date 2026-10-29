"""No-network routing regression tests. Fake Drive rejects all mutations."""
import copy
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import drive
from folder_resolver import FOLDER_MIME, classify, resolve_target, verify_parent
from models import AutomationError, DownloadedFile


class ReadOnlyDrive:
    def __init__(self, nodes, page_size=1000):
        self.nodes = copy.deepcopy(nodes)
        self.page_size = page_size
        self.reads = []

    def files(self): return self

    def get(self, fileId, **kwargs):
        self.reads.append(('get', fileId))
        return Request(self.nodes[fileId])

    def list(self, q, pageToken=None, **kwargs):
        parent = re.match(r"'([^']+)' in parents", q).group(1)
        self.reads.append(('list', parent))
        nodes = [n for n in self.nodes.values() if parent in n.get('parents', [])
                 and ('mimeType=' not in q or n.get('mimeType') == FOLDER_MIME) and not n.get('trashed')]
        start = int(pageToken or 0); end = start + self.page_size
        result = {'files': nodes[start:end]}
        if end < len(nodes): result['nextPageToken'] = str(end)
        return Request(result)

    def __getattr__(self, name):
        raise AssertionError(f'Forbidden Drive operation: {name}')


class Request:
    def __init__(self, response): self.response = response
    def execute(self): return copy.deepcopy(self.response)


def fixture():
    nodes = {}
    def add(id, name, parent=None):
        nodes[id] = dict(id=id, name=name, mimeType=FOLDER_MIME, trashed=False, modifiedTime='2026-10-01T00:00:00Z',
                         parents=[parent] if parent else [])
    add('root', 'Delivery root')
    for short, name in [('reading', 'READING CHAUFFEURS'), ('excel', 'EXCEL CAR'),
                        ('platinum', 'Platinum Executive Cars'), ('first', 'FIRST CLASS CARS')]:
        add(short, name, 'root');add(short+'-oct', 'October 2026', short)
        add(short+'-stories', 'Stories', short+'-oct')
        add(short+'-posts', 'Static Posts', short+'-oct')
        add(short+'-fb', 'Facebook Assets', short)
    return nodes


class RoutingTests(unittest.TestCase):
    def setUp(self): self.nodes = fixture(); self.service = ReadOnlyDrive(self.nodes)

    def route(self, name='Reading-Stories-29-Oct-2026', account='Reading Chauffers', date='28-Oct-2026'):
        return resolve_target(self.service, 'root', name, account, 'Branding/Other', date)

    def assert_code(self, code, fn):
        with self.assertRaises(AutomationError) as caught: fn()
        self.assertEqual(caught.exception.code, code)

    def test_reading_uses_stories_not_static_posts(self):
        id, path, _ = self.route()
        self.assertEqual(id, 'reading-stories')
        self.assertEqual(path, 'Delivery root/READING CHAUFFEURS/October 2026/Stories')

    def test_all_four_businesses(self):
        for name, account, expected in [('Reading','Reading Chauffers','reading'),
            ('Excel','Excel Executive Cars','excel'),('Platinum','Platinum Exetuive Car','platinum'),
            ('First-Class-Cars','First Class Cars','first')]:
            with self.subTest(name=name):
                self.assertEqual(self.route(name+'-Stories-29-Oct-2026',account)[0],expected+'-stories')

    def test_yesterday_excel_conflict_stops_before_drive_reads(self):
        self.assert_code('DRIVE_ROUTE_BUSINESS_CONFLICT',lambda:self.route('Excel-Stories-29-Oct-2026','First Class Cars'))
        self.assertEqual(self.service.reads, [])

    def test_yesterday_first_class_conflict(self):
        self.assert_code('DRIVE_ROUTE_BUSINESS_CONFLICT',lambda:self.route('First-Class-Cars-Stories-29-Oct-2026','Excel Executive Cars'))

    def test_duplicate_case_variant_stories_blocked(self):
        self.service.nodes['duplicate'] = dict(self.nodes['excel-stories'],id='duplicate',name='STORIES')
        self.assert_code('DRIVE_ROUTE_FOLDER_AMBIGUOUS',lambda:self.route('Excel-Stories-29-Oct-2026','Excel Executive Cars'))

    def test_duplicate_business_blocked(self):
        self.service.nodes['duplicate'] = dict(self.nodes['reading'],id='duplicate')
        self.assert_code('DRIVE_ROUTE_FOLDER_AMBIGUOUS',self.route)

    def test_duplicate_month_blocked(self):
        self.service.nodes['duplicate'] = dict(self.nodes['reading-oct'],id='duplicate')
        self.assert_code('DRIVE_ROUTE_FOLDER_AMBIGUOUS',self.route)

    def test_missing_stories_never_falls_back(self):
        del self.service.nodes['reading-stories']
        self.assert_code('DRIVE_FOLDER_NOT_FOUND',self.route)

    def test_trashed_destination_ignored(self):
        self.service.nodes['reading-stories']['trashed'] = True
        self.assert_code('DRIVE_FOLDER_NOT_FOUND',self.route)

    def test_pagination_finds_destination(self):
        self.service.page_size = 1
        self.assertEqual(self.route()[0], 'reading-stories')

    def test_facebook_skips_month(self):
        self.assertEqual(self.route('Platinum-FB-CoverPage.png','Platinum Exetuive Car')[0], 'platinum-fb')

    def test_filename_month_beats_previous_day_job(self):
        self.service.nodes['reading-nov'] = dict(self.nodes['reading-oct'],id='reading-nov',name='November 2026')
        self.service.nodes['nov-stories'] = dict(self.nodes['reading-stories'],id='nov-stories',parents=['reading-nov'])
        self.assertEqual(self.route('Reading-Stories-1-Nov-2026',date='31-Oct-2026')[0], 'nov-stories')

    def test_job_date_used_when_filename_undated(self):
        self.assertEqual(self.route('Reading-Stories')[0], 'reading-stories')

    def test_missing_date_blocked(self):
        self.assert_code('DRIVE_ROUTE_DATE_AMBIGUOUS',lambda:self.route('Reading-Stories',date=''))

    def test_invalid_filename_date_never_uses_job_date(self):
        self.assert_code('DRIVE_ROUTE_DATE_INVALID',lambda:self.route('Reading-Stories-32-Oct-2026'))

    def test_multiple_publication_dates_blocked(self):
        self.assert_code('DRIVE_ROUTE_DATE_AMBIGUOUS',lambda:self.route('Reading-Stories-29-Oct-2026-30-Nov-2026'))

    def test_unknown_content_blocked(self):
        self.assert_code('DRIVE_ROUTE_CONTENT_AMBIGUOUS',lambda:self.route('Reading-Design-29-Oct-2026'))

    def test_multiple_content_types_blocked(self):
        self.assert_code('DRIVE_ROUTE_CONTENT_AMBIGUOUS',lambda:self.route('Reading-Stories-Reel-29-Oct-2026'))

    def test_unknown_account_blocked(self):
        self.assert_code('DRIVE_ROUTE_UNKNOWN_BUSINESS',lambda:self.route(account='Unknown'))

    def test_root_is_not_active_folder(self):
        self.service.nodes['root']['trashed'] = True
        self.assert_code('DRIVE_FOLDER_MISSING',self.route)

    def test_exact_alias_does_not_choose_story_archive(self):
        self.service.nodes['archive'] = dict(self.nodes['reading-stories'],id='archive',name='Stories Archive')
        self.assertEqual(self.route()[0], 'reading-stories')

    def test_parent_verification_detects_wrong_or_root_parent(self):
        for parents in (['root'], [], ['reading-stories','root']):
            self.service.nodes['file'] = dict(id='file',parents=parents)
            self.assert_code('DRIVE_PARENT_MISMATCH',lambda:verify_parent(self.service,'file','reading-stories'))

    def test_parent_verification_accepts_correct_parent(self):
        self.service.nodes['file'] = dict(id='file',parents=['reading-stories'])
        verify_parent(self.service,'file','reading-stories')

    def test_upload_conflict_never_reaches_write(self):
        file = DownloadedFile(Path(__file__), 'Excel-Stories-29-Oct-2026', 1)
        with patch.object(drive,'get_settings'), patch.object(drive,'build',return_value=self.service):
            self.assert_code('DRIVE_ROUTE_BUSINESS_CONFLICT',lambda:drive.upload_and_share(
                'test-token','First Class Cars','Branding/Other',file,'root','28-Oct-2026'))
        self.assertEqual(self.service.reads, [])

    def test_replacement_wrong_parent_blocked_before_update(self):
        self.service.nodes['existing-file-12345'] = dict(id='existing-file-12345',parents=['root'])
        file = DownloadedFile(Path(__file__), 'Reading-Stories-29-Oct-2026.png', 1)
        with patch.object(drive,'build',return_value=self.service):
            self.assert_code('DRIVE_PARENT_MISMATCH',lambda:drive.replace_file(
                'test-token','Reading Chauffers','Branding/Other',file,
                'https://drive.google.com/file/d/existing-file-12345/view','root','28-Oct-2026'))

    def test_upload_uses_resolved_id_and_checks_parent(self):
        file = DownloadedFile(Path(__file__), 'Reading-Stories-29-Oct-2026.png', 1)
        with patch.object(drive,'get_settings'), patch.object(drive,'build',return_value=self.service), \
             patch.object(drive,'_upload_one',return_value=('uploaded-file',1)) as upload, \
             patch.object(drive,'_verify'), patch('folder_resolver.verify_parent') as parent, \
             patch.object(drive,'_share'), patch.object(drive,'_link',return_value='test-link'):
            result = drive.upload_and_share('test-token','Reading Chauffers','Branding/Other',file,'root','28-Oct-2026')
            upload.assert_called_once_with(self.service,file.path,'reading-stories')
            parent.assert_called_once_with(self.service,'uploaded-file','reading-stories')
            self.assertEqual(result.folder_path,'Delivery root/READING CHAUFFEURS/October 2026/Stories')

    def test_directory_parent_verified_before_children(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory); (path/'page.png').write_bytes(b'page')
            file = DownloadedFile(path,'Reading-Stories-29-Oct-2026',4,True)
            with patch.object(drive,'get_settings'), patch.object(drive,'build',return_value=self.service), \
                 patch.object(drive,'_create_folder',return_value='bundle') as create, \
                 patch('folder_resolver.verify_parent',side_effect=AutomationError('DRIVE_PARENT_MISMATCH','bad parent')), \
                 patch.object(drive,'_upload_one') as upload:
                self.assert_code('DRIVE_PARENT_MISMATCH',lambda:drive.upload_and_share(
                    'test-token','Reading Chauffers','Branding/Other',file,'root','28-Oct-2026'))
                create.assert_called_once_with(self.service,'reading-stories',file.name)
                upload.assert_not_called()

    def test_legacy_ai_path_cannot_override_tree_path(self):
        from llm_router import _validate
        from types import SimpleNamespace
        result = _validate(dict(folder_id='stories',path='My Drive/fabricated',confidence=.99),
            [dict(id='stories',path='Verified/Stories')],SimpleNamespace(routing_min_confidence=.75))
        self.assertEqual(result['path'],'Verified/Stories')

    def test_duplicate_uses_newest_folder_activity(self):
        self.service.nodes['duplicate'] = dict(self.nodes['excel-stories'],id='duplicate',name='STORIES',modifiedTime='2026-10-28T00:00:00Z')
        self.assertEqual(self.route('Excel-Stories-29-Oct-2026','Excel Executive Cars')[0], 'duplicate')

    def test_descendant_activity_beats_folder_timestamp(self):
        self.service.nodes['duplicate'] = dict(self.nodes['excel-stories'],id='duplicate',name='STORIES',modifiedTime='2026-10-20T00:00:00Z')
        self.service.nodes['nested'] = dict(self.nodes['excel-stories'],id='nested',parents=['excel-stories'])
        self.service.nodes['image'] = dict(id='image',name='page.png',parents=['nested'],mimeType='image/png',modifiedTime='2026-10-28T00:00:00Z')
        self.assertEqual(self.route('Excel-Stories-29-Oct-2026','Excel Executive Cars')[0], 'excel-stories')

    def test_trashed_recent_content_does_not_win(self):
        self.service.nodes['duplicate'] = dict(self.nodes['excel-stories'],id='duplicate',modifiedTime='2026-10-20T00:00:00Z')
        self.service.nodes['image'] = dict(id='image',parents=['excel-stories'],mimeType='image/png',modifiedTime='2026-10-28T00:00:00Z',trashed=True)
        self.assertEqual(self.route('Excel-Stories-29-Oct-2026','Excel Executive Cars')[0], 'duplicate')

    def test_missing_activity_blocks_selection(self):
        self.service.nodes['duplicate'] = dict(self.nodes['excel-stories'],id='duplicate')
        del self.service.nodes['duplicate']['modifiedTime']
        self.assert_code('DRIVE_ROUTE_ACTIVITY_UNAVAILABLE',lambda:self.route('Excel-Stories-29-Oct-2026','Excel Executive Cars'))

    def test_activity_scan_pagination(self):
        self.service.page_size = 1
        self.service.nodes['duplicate'] = dict(self.nodes['excel-stories'],id='duplicate')
        for number in range(3):
            self.service.nodes[str(number)] = dict(id=str(number),parents=['excel-stories'],mimeType='image/png',modifiedTime=f'2026-10-{20+number}T00:00:00Z')
        self.assertEqual(self.route('Excel-Stories-29-Oct-2026','Excel Executive Cars')[0], 'excel-stories')

    def test_shortcuts_are_not_followed(self):
        self.service.nodes['duplicate'] = dict(self.nodes['excel-stories'],id='duplicate')
        self.service.nodes['shortcut'] = dict(id='shortcut',parents=['duplicate'],mimeType='application/vnd.google-apps.shortcut',modifiedTime='2026-10-28T00:00:00Z')
        self.assertEqual(self.route('Excel-Stories-29-Oct-2026','Excel Executive Cars')[0], 'duplicate')
        self.assertNotIn(('list','shortcut'),self.service.reads)


if __name__ == '__main__': unittest.main()
