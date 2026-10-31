"""CLI for licensed race feeds, coverage auditing and per-rider exports."""
from __future__ import annotations
import argparse
import csv
from pathlib import Path
import json
from .database import connect
from .ingestion import ingest_file
from .provider import sync_date


def report(con, day: str | None = None) -> dict:
    predicate = ' WHERE race_date=?' if day else ''
    args = (day,) if day else ()
    def count(sql, params=()):
        return con.execute(sql,params).fetchone()[0]
    races = count('SELECT COUNT(*) FROM races'+predicate,args)
    pre = count('SELECT COUNT(DISTINCT r.race_id) FROM races r JOIN race_snapshots s ON r.race_id=s.race_id '
                'WHERE s.before_start=1'+(' AND r.race_date=?' if day else ''),args)
    results = count('SELECT COUNT(*) FROM races r JOIN final_results f ON r.race_id=f.race_id'
                    +(' WHERE r.race_date=?' if day else ''),args)
    expected = count('SELECT COUNT(*) FROM expected_races'+predicate,args)
    missing = [dict(r) for r in con.execute('SELECT e.race_id,e.venue_code,e.race_no FROM expected_races e '
        'LEFT JOIN races r ON r.race_id=e.race_id WHERE r.race_id IS NULL'
        +(' AND e.race_date=?' if day else '')+' ORDER BY e.race_date,e.venue_code,e.race_no',args)]
    no_result = [r['race_id'] for r in con.execute('SELECT r.race_id FROM races r LEFT JOIN final_results f '
        'ON f.race_id=r.race_id WHERE f.race_id IS NULL'+(' AND r.race_date=?' if day else '')+' ORDER BY r.race_date,r.race_id',args)]
    snap_entries = count('SELECT COUNT(*) FROM entrant_snapshots es JOIN race_snapshots s ON es.snapshot_id=s.snapshot_id '
        'JOIN races r ON s.race_id=r.race_id'+(' WHERE r.race_date=?' if day else ''),args)
    return {'date':day,'races_recorded':races,'races_with_before_start_snapshot':pre,
        'races_with_final_status':results,'rider_snapshots':snap_entries,'expected_races_from_manifest':expected,
        'missing_expected_races':missing,'races_missing_results':no_result,
        'coverage_certified': bool(day and expected and not missing and races==expected and pre==expected and results==expected)}


def export_riders(con,path: Path,day: str | None = None) -> int:
    # Only last BEFORE-start snapshot per race, never after-start or final features.
    query = '''SELECT r.race_id,r.race_date,r.venue_code,r.venue_name,r.race_no,r.scheduled_start,
              s.captured_at AS pre_captured_at,s.source_name AS pre_source,
              e.car_no,e.rider_id,e.rider_name,e.race_score,e.rider_class,e.running_style,
              e.line_group,e.line_position,e.features_json,
              f.status AS result_status,f.captured_at AS result_captured_at,
              rr.rank,rr.finish_status,rr.agari_seconds,rr.winning_method,rr.margin
              FROM races r JOIN race_snapshots s ON s.snapshot_id=(
                SELECT s2.snapshot_id FROM race_snapshots s2 WHERE s2.race_id=r.race_id
                AND s2.before_start=1 ORDER BY s2.captured_at DESC,s2.snapshot_id DESC LIMIT 1)
              JOIN entrant_snapshots e ON e.snapshot_id=s.snapshot_id
              LEFT JOIN final_results f ON f.race_id=r.race_id
              LEFT JOIN rider_results rr ON rr.race_id=r.race_id AND rr.car_no=e.car_no AND rr.rider_id=e.rider_id'''
    args=()
    if day:
        query += ' WHERE r.race_date=?'
        args=(day,)
    query += ' ORDER BY r.race_date,r.venue_code,r.race_no,e.car_no'
    cursor=con.execute(query,args)
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f)
        writer.writerow([d[0] for d in cursor.description])
        rows=0
        for row in cursor:
            writer.writerow(tuple(row))
            rows+=1
    return rows


def main(argv=None) -> int:
    p=argparse.ArgumentParser(description='競輪シン：レース前情報と確定結果の記録')
    p.add_argument('--db',default='data/keirin.sqlite3',help='SQLite database file')
    sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('init-db',help='Initialize SQLite schema')
    x=sub.add_parser('ingest',help='Ingest one JSON document');x.add_argument('path',type=Path)
    x=sub.add_parser('ingest-dir',help='Ingest schedule/pre_race/results JSON files from folder');x.add_argument('path',type=Path)
    x=sub.add_parser('report',help='Show data coverage and missing results');x.add_argument('--date')
    x=sub.add_parser('export-riders',help='Export training-ready one-row-per-rider CSV');x.add_argument('--date');x.add_argument('--out',required=True,type=Path)
    x=sub.add_parser('sync-date',help='Fetch feeds from a licensed provider configured by operator');x.add_argument('--date',required=True);x.add_argument('--config',required=True,type=Path)
    args=p.parse_args(argv)
    with connect(args.db) as con:
        if args.cmd=='init-db':
            print(f'Initialized: {args.db}')
        elif args.cmd=='ingest':
            print('Imported' if ingest_file(con,args.path) else 'Already imported')
        elif args.cmd=='ingest-dir':
            paths=sorted(path for path in args.path.glob('*.json') if path.name != 'provider.config.example.json')
            # Ensure result documents never arrive before corresponding pre-race data.
            def order(path):
                doc=json.loads(path.read_text(encoding='utf-8'))
                return ({'schedule':0,'pre_race':1,'results':2}.get(doc.get('kind'),3),str(path))
            imported=0
            for path in sorted(paths,key=order):
                imported+=bool(ingest_file(con,path))
            print(f'Imported {imported}/{len(paths)} documents')
        elif args.cmd=='report':
            print(json.dumps(report(con,args.date),ensure_ascii=False,indent=2))
        elif args.cmd=='export-riders':
            count=export_riders(con,args.out,args.date)
            print(f'Exported {count} rider rows to {args.out}')
        elif args.cmd=='sync-date':
            print(json.dumps(sync_date(con,args.config,args.date),ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
