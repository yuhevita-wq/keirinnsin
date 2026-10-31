"""Pull recent days from an explicitly authorized, licensed JSON provider.

Run from persistent storage via a host scheduler. Does nothing without a config file.
"""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo
from keirin_shin.database import connect
from keirin_shin.provider import sync_date
from keirin_shin.cli import report


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--db',default='data/keirin.sqlite3')
    p.add_argument('--days-back',type=int,default=2)
    args=p.parse_args()
    if not 0 <= args.days_back <= 30:
        p.error('--days-back must be 0..30')
    today=datetime.now(ZoneInfo('Asia/Tokyo')).date()
    with connect(args.db) as con:
        for n in range(args.days_back,-1,-1):
            day=(today-timedelta(days=n)).isoformat()
            synced=sync_date(con,args.config,day)
            r=report(con,day)
            print(json.dumps({'date':day,'sync':synced,'coverage':r},ensure_ascii=False))

if __name__=='__main__':
    main()
