import csv
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from keirin_shin.database import connect
from keirin_shin.ingestion import ingest, ingest_file
from keirin_shin.cli import export_riders, report

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


def read(name):
    return json.loads((EXAMPLES/name).read_text(encoding='utf-8'))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = connect(Path(self.temp.name)/'data.sqlite3')
        self.addCleanup(self.db.close)

    def seed(self):
        for f in ('01_schedule.json', '02_pre_race.json', '03_results.json'):
            self.assertTrue(ingest_file(self.db, EXAMPLES/f))

    def test_happy_path_and_duplicate(self):
        self.seed()
        self.assertFalse(ingest_file(self.db, EXAMPLES/'02_pre_race.json'))
        r = report(self.db,'2026-10-31')
        self.assertEqual((r['races_recorded'],r['races_with_before_start_snapshot'],r['races_with_final_status'],r['rider_snapshots']), (1,1,1,3))
        self.assertTrue(r['coverage_certified'])
        dest = Path(self.temp.name)/'riders.csv'
        self.assertEqual(export_riders(self.db,dest),3)
        with dest.open(encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        self.assertEqual(rows[0]['rider_id'],'DEMO-R001')
        self.assertEqual(rows[0]['rank'],'1')
        self.assertEqual(rows[0]['pre_captured_at'],'2026-10-31T09:00:00+09:00')
        self.assertEqual(rows[0]['result_captured_at'],'2026-10-31T12:00:00+09:00')

    def test_results_before_pre_fail_without_commit(self):
        with self.assertRaisesRegex(ValueError, 'pre-race'):
            ingest(self.db,read('03_results.json'))
        self.assertEqual(self.db.execute('SELECT count(*) FROM imports').fetchone()[0],0)

    def test_bad_second_rider_rolls_back_whole_batch(self):
        bad=read('02_pre_race.json')
        bad['races'][0]['entries'][1]['car_no']=1
        with self.assertRaises(Exception):
            ingest(self.db,bad)
        for table in ('races','riders','race_snapshots','entrant_snapshots','imports'):
            self.assertEqual(self.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0],0)

    def test_after_start_snapshot_is_not_a_training_feature(self):
        ingest(self.db,read('02_pre_race.json'))
        newer=read('02_pre_race.json')
        newer['source']['captured_at']='2026-10-31T13:00:00+09:00'
        newer['races'][0]['entries'][0]['race_score']=999
        ingest(self.db,newer)
        self.assertEqual(self.db.execute('SELECT sum(before_start) FROM race_snapshots').fetchone()[0],1)
        dest=Path(self.temp.name)/'training.csv'
        self.assertEqual(export_riders(self.db,dest),3)
        with dest.open(encoding='utf-8-sig',newline='') as f:
            rows=list(csv.DictReader(f))
        self.assertEqual(float(rows[0]['race_score']),90.2)

    def test_unknown_rider_result_rejected(self):
        ingest(self.db,read('02_pre_race.json'))
        bad=read('03_results.json')
        bad['races'][0]['results'][0]['rider_id']='UNKNOWN'
        with self.assertRaisesRegex(ValueError,'missing in pre-race'):
            ingest(self.db,bad)
        self.assertEqual(self.db.execute('SELECT count(*) FROM final_results').fetchone()[0],0)

    def test_correction_newer_is_supported_older_cannot_overwrite(self):
        self.seed()
        revised=read('03_results.json')
        revised['source']['captured_at']='2026-10-31T15:00:00+09:00'
        revised['races'][0]['results'][0]['agari_seconds']=11.1
        self.assertTrue(ingest(self.db,revised))
        self.assertEqual(self.db.execute('SELECT agari_seconds FROM rider_results WHERE car_no=1').fetchone()[0],11.1)
        older=read('03_results.json')
        older['source']['captured_at']='2026-10-31T11:59:00+09:00'
        with self.assertRaisesRegex(ValueError,'older'):
            ingest(self.db,older)

    def test_manifest_missing_races_detected(self):
        schedule=read('01_schedule.json')
        schedule['races'].append({'race_id':'DEMO-MISSING','venue_code':'99','race_no':2})
        ingest(self.db,schedule)
        ingest(self.db,read('02_pre_race.json'))
        r=report(self.db,'2026-10-31')
        self.assertFalse(r['coverage_certified'])
        self.assertEqual(r['missing_expected_races'][0]['race_id'],'DEMO-MISSING')
        self.assertEqual(r['races_missing_results'],['DEMO-20261031-99-01'])

    def test_same_time_different_content_rejected(self):
        ingest(self.db,read('02_pre_race.json'))
        other=read('02_pre_race.json')
        other['races'][0]['entries'][0]['race_score']=80
        with self.assertRaisesRegex(ValueError,'collision'):
            ingest(self.db,other)

    def test_timezone_required(self):
        bad=read('02_pre_race.json')
        bad['source']['captured_at']='2026-10-31T09:00:00'
        with self.assertRaisesRegex(ValueError,'offset'):
            ingest(self.db,bad)

    def test_bad_json_rolls_back(self):
        bad=read('02_pre_race.json')
        bad['races'][0]['entries'][0]['features']=[]
        with self.assertRaisesRegex(ValueError,'features'):
            ingest(self.db,bad)
        self.assertEqual(self.db.execute('SELECT count(*) FROM races').fetchone()[0],0)


if __name__=='__main__':
    unittest.main()
