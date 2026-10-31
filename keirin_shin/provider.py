"""Optional authorized HTTPS JSON feed fetcher; no built-in site scraping."""
from __future__ import annotations
from datetime import date
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from .ingestion import ingest


def sync_date(con, config_path: Path, day: str) -> dict:
    date.fromisoformat(day)
    cfg = json.loads(config_path.read_text(encoding='utf-8'))
    if cfg.get('rights_confirmed') is not True:
        raise ValueError('Config rights_confirmed must be true after verifying provider permission')
    sources = cfg.get('urls',{})
    if not isinstance(sources,dict) or not sources:
        raise ValueError('Config urls must contain at least one endpoint template')
    auth_env = cfg.get('token_env')
    token = os.environ.get(auth_env) if auth_env else None
    if auth_env and not token:
        raise ValueError(f'Missing feed access token in environment variable {auth_env}')
    headers = {'Accept':'application/json','User-Agent':'keirin-shin/0.1 licensed-feed-importer'}
    if token:
        headers[cfg.get('token_header','Authorization')] = (cfg.get('token_prefix','Bearer ')+token)
    report = {}
    # Fetch schedule/pre_race before results; results require pre-race records.
    for kind in ('schedule','pre_race','results'):
        template = sources.get(kind)
        if not template:
            continue
        url = template.format(date=day,date_compact=day.replace('-',''))
        if urlparse(url).scheme != 'https' or not urlparse(url).hostname:
            raise ValueError('Only explicit HTTPS feed URLs are supported')
        try:
            with urlopen(Request(url,headers=headers),timeout=30) as response:
                if response.status != 200:
                    raise ValueError(f'HTTP {response.status}: {url}')
                data = response.read(20_000_001)
                if len(data) > 20_000_000:
                    raise ValueError('Response exceeds 20 MB limit')
        except HTTPError as ex:
            if ex.code == 404:
                report[kind] = 'not_published'
                continue
            raise
        doc = json.loads(data.decode('utf-8'))
        if doc.get('kind') != kind:
            raise ValueError(f'Endpoint for {kind} returned kind {doc.get("kind")!r}')
        if kind == 'schedule' and doc.get('race_date') != day:
            raise ValueError('Schedule date does not match requested date')
        for race in doc.get('races',[]):
            if race.get('race_date',day) != day:
                raise ValueError('Feed contains a race outside requested day')
        report[kind] = 'imported' if ingest(con,doc,url) else 'unchanged'
    return report
