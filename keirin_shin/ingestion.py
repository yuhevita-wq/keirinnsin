"""Transactional, idempotent ingestion with pre-race point-in-time snapshots."""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


def _required(value: dict, key: str, kind: type = str):
    item = value.get(key)
    if not isinstance(item, kind) or (kind is str and not item.strip()):
        raise ValueError(f'{key}: expected non-empty {kind.__name__}')
    return item


def _datetime(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value)
    except (TypeError, ValueError) as ex:
        raise ValueError(f'invalid ISO timestamp: {value!r}') from ex
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f'timestamp needs UTC offset: {value!r}')
    return dt


def _date(value: str) -> str:
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError()
        return value
    except (TypeError, ValueError) as ex:
        raise ValueError(f'invalid ISO date: {value!r}') from ex


def _int(value: Any, key: str, lo: int = 0, hi: int = 1000000000) -> int:
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f'{key}: expected integer {lo}..{hi}')
    return value


def _float_optional(value: Any, key: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{key}: expected number or null')
    number = float(value)
    if not (-1e8 < number < 1e8):
        raise ValueError(f'{key}: numeric range exceeded')
    return number


def _ensure_race(con: sqlite3.Connection, race: dict) -> tuple[str, str]:
    rid = _required(race, 'race_id')
    day = _date(_required(race, 'race_date'))
    venue = _required(race, 'venue_code')
    venue_name = _required(race, 'venue_name')
    no = _int(race.get('race_no'), 'race_no', 1, 30)
    start = _required(race, 'scheduled_start')
    _datetime(start)
    existing = con.execute('SELECT * FROM races WHERE race_id=?', (rid,)).fetchone()
    if existing is not None:
        if (existing['race_date'],existing['venue_code'],existing['race_no']) != (day,venue,no):
            raise ValueError(f'race_id {rid} conflicts with existing race identity')
        # Updated scheduling/name metadata is allowed, but old snapshots stay unchanged.
        con.execute('UPDATE races SET venue_name=?,scheduled_start=?,race_class=? WHERE race_id=?',
                    (venue_name, start, race.get('race_class'), rid))
    else:
        con.execute('INSERT INTO races VALUES (?,?,?,?,?,?,?)',
                    (rid,day,venue,venue_name,no,start,race.get('race_class')))
    return rid, start


def _pre_race(con: sqlite3.Connection, race: dict, captured: str, source: str) -> None:
    rid, start = _ensure_race(con, race)
    entries = _required(race, 'entries', list)
    if not entries:
        raise ValueError(f'{rid}: entries cannot be empty')
    digest = hashlib.sha256(json.dumps(race, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    old = con.execute('SELECT digest FROM race_snapshots WHERE race_id=? AND captured_at=? AND source_name=?',
                      (rid,captured,source)).fetchone()
    if old:
        if old['digest'] != digest:
            raise ValueError(f'{rid}: snapshot timestamp/source collision with different payload')
        return
    cursor = con.execute('INSERT INTO race_snapshots(race_id,captured_at,source_name,before_start,digest) VALUES (?,?,?,?,?)',
                         (rid,captured,source,int(_datetime(captured) < _datetime(start)),digest))
    snapshot_id = cursor.lastrowid
    for ent in entries:
        no = _int(ent.get('car_no'), 'car_no', 1, 20)
        rider_id = _required(ent,'rider_id')
        name = _required(ent,'rider_name')
        features = ent.get('features', {})
        if not isinstance(features,dict):
            raise ValueError('features must be an object')
        line_pos = ent.get('line_position')
        if line_pos is not None:
            _int(line_pos,'line_position',1,20)
        score = _float_optional(ent.get('race_score'),'race_score')
        con.execute('INSERT INTO riders(rider_id,latest_name) VALUES (?,?) '
                    'ON CONFLICT(rider_id) DO UPDATE SET latest_name=excluded.latest_name',(rider_id,name))
        con.execute('INSERT INTO entrant_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)',
                    (snapshot_id,no,rider_id,name,score,ent.get('rider_class'),ent.get('running_style'),
                     ent.get('line_group'),line_pos,json.dumps(features,ensure_ascii=False,sort_keys=True)))


def _results(con: sqlite3.Connection, race: dict, captured: str, source: str) -> None:
    rid = _required(race,'race_id')
    row = con.execute('SELECT * FROM races WHERE race_id=?',(rid,)).fetchone()
    if row is None:
        raise ValueError(f'{rid}: results require pre-race record; ingest pre_race first')
    status = _required(race,'status')
    if status not in ('official','cancelled'):
        raise ValueError(f'{rid}: status must be official or cancelled')
    entries = _required(race,'results',list)
    if status == 'official' and not entries:
        raise ValueError(f'{rid}: official race must have results')
    if status == 'cancelled' and entries:
        raise ValueError(f'{rid}: cancelled race must not have results')
    payouts = race.get('payouts',[])
    if not isinstance(payouts,list):
        raise ValueError('payouts must be an array')
    # Only accept riders who are present in a pre-race snapshot. No invented riders.
    valid = {(x['car_no'],x['rider_id']) for x in con.execute('SELECT DISTINCT car_no,rider_id FROM entrant_snapshots '
         'WHERE snapshot_id IN (SELECT snapshot_id FROM race_snapshots WHERE race_id=?)',(rid,))}
    for ent in entries:
        no = _int(ent.get('car_no'),'car_no',1,20)
        rider_id = _required(ent,'rider_id')
        if (no,rider_id) not in valid:
            raise ValueError(f'{rid}: result participant {no}/{rider_id} missing in pre-race entries')
        fin = _required(ent,'finish_status')
        if fin not in ('finished','dns','dnf','disqualified','other'):
            raise ValueError(f'{rid}: unknown finish_status {fin}')
        rank = ent.get('rank')
        if rank is not None:
            _int(rank,'rank',1,20)
        if fin != 'finished' and rank is not None:
            raise ValueError('unfinished riders must have rank=null')
        if fin == 'finished' and rank is None:
            raise ValueError('finished riders must have a rank')
        seconds = _float_optional(ent.get('agari_seconds'),'agari_seconds')
        if seconds is not None and seconds <= 0:
            raise ValueError('agari_seconds must be > 0')
    for pay in payouts:
        _required(pay,'bet_type')
        _required(pay,'combination')
        _int(pay.get('payout_yen'),'payout_yen')
    prior = con.execute('SELECT captured_at FROM final_results WHERE race_id=?',(rid,)).fetchone()
    if prior and _datetime(prior['captured_at']) > _datetime(captured):
        raise ValueError(f'{rid}: older results cannot overwrite newer results')
    con.execute('INSERT INTO final_results VALUES (?,?,?,?) ON CONFLICT(race_id) DO UPDATE SET '
                'source_name=excluded.source_name,captured_at=excluded.captured_at,status=excluded.status',
                (rid,source,captured,status))
    con.execute('DELETE FROM rider_results WHERE race_id=?',(rid,))
    con.execute('DELETE FROM payouts WHERE race_id=?',(rid,))
    for ent in entries:
        con.execute('INSERT INTO rider_results VALUES (?,?,?,?,?,?,?,?)',
                    (rid,ent['car_no'],ent['rider_id'],ent.get('rank'),ent['finish_status'],
                     ent.get('agari_seconds'),ent.get('winning_method'),ent.get('margin')))
    for pay in payouts:
        con.execute('INSERT INTO payouts VALUES (?,?,?,?)',
                    (rid,pay['bet_type'],pay['combination'],pay['payout_yen']))


def _schedule(con: sqlite3.Connection, document: dict) -> None:
    day = _date(_required(document,'race_date'))
    races = _required(document,'races',list)
    # An empty schedule is valid (no races that day). Replacing is authoritative.
    con.execute('DELETE FROM expected_races WHERE race_date=?',(day,))
    for race in races:
        con.execute('INSERT INTO expected_races VALUES (?,?,?,?)',
                    (day,_required(race,'race_id'),_required(race,'venue_code'),_int(race.get('race_no'),'race_no',1,30)))


def ingest(con: sqlite3.Connection, document: dict, filename: str = '') -> bool:
    """Import one document atomically. Returns False for exact duplicate content."""
    if not isinstance(document,dict):
        raise ValueError('top-level JSON must be an object')
    if document.get('schema_version') != 1:
        raise ValueError('schema_version must be 1')
    kind = _required(document,'kind')
    if kind not in ('pre_race','results','schedule'):
        raise ValueError('kind must be pre_race/results/schedule')
    src = _required(document,'source',dict)
    name = _required(src,'name')
    _required(src,'license_reference')  # Traceability, not proof of permission.
    captured = _required(src,'captured_at')
    _datetime(captured)
    payload = json.dumps(document,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    with con:
        if con.execute('SELECT 1 FROM imports WHERE digest=?',(digest,)).fetchone():
            return False
        if kind == 'schedule':
            _schedule(con,document)
        else:
            races = _required(document,'races',list)
            for race in races:
                if kind == 'pre_race':
                    _pre_race(con,race,captured,name)
                else:
                    _results(con,race,captured,name)
        con.execute('INSERT INTO imports(digest,kind,source_name,captured_at,original_filename,payload) '
                    'VALUES (?,?,?,?,?,?)',(digest,kind,name,captured,filename,payload))
    return True


def ingest_file(con: sqlite3.Connection, file: Path) -> bool:
    return ingest(con,json.loads(file.read_text(encoding='utf-8')),str(file))
