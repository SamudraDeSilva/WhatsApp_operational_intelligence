"""
OPERATIONAL INTELLIGENCE v3.0.0 - DESKTOP + JUPYTER EXECUTIVE APPLICATION

Run from VS Code with Python 3.10+:
  python -m pip install -r requirements.txt
  python -m playwright install chromium   # only for optional WhatsApp monitoring
  python app.py  # launch the modular desktop application

The dashboard works completely locally using SQLite in ./data/.
WhatsApp Web collection is optional and requires network access and user login.
"""
from __future__ import annotations

"""Offline operational intelligence: authoritative SQLite store and analytics."""
import csv, hashlib, json, logging, math, random, re, sqlite3, statistics, threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

# A notebook has no __file__: resolve local assets relative to its working folder.
# When running as a .py script, keep assets alongside that script instead.
APP_VERSION = '3.0.0'
ROOT = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
DB = DATA / 'operations.db'
CONFIG = DATA / 'settings.json'
FIELDS = ['Date','Time','Group','Sender','Full Message','T002/T003','PWT','SPL','PL','Near Miss','Late']
METRICS = {'T002/T003':'t002_t003','PWT':'pwt','SPL':'spl','PL':'pl','Near Miss':'near_miss','Late':'late'}
DEFAULT_CONFIG = {'groups':['Gang 1','Team 5'], 'sweep_seconds':5, 'alert_late_threshold':5,
                  'alert_missing_hours':24, 'alert_near_miss_threshold':1, 'theme':'Dark',
                  'db_path': None}


def configuration():
    try: return {**DEFAULT_CONFIG, **json.loads(CONFIG.read_text('utf-8'))}
    except (OSError,ValueError): return DEFAULT_CONFIG.copy()

def save_configuration(obj):
    """Write user preferences separately from the data being monitored."""
    CONFIG.write_text(json.dumps(obj,indent=2),encoding='utf-8')


def group_registry(config=None):
    """Read a validated, ordered group register without breaking legacy settings.

    The original collector expects config['groups'] to be a list of enabled
    group names. This richer register is UI-only metadata. Earlier-version settings
    are automatically shown as active entries without modifying the file.
    """
    cfg = config if config is not None else configuration()
    records = cfg.get('group_registry')
    if not isinstance(records, list):
        records = [{'name': name, 'enabled': True} for name in cfg.get('groups', [])]
    cleaned, seen = [], set()
    for entry in records:
        if isinstance(entry, str):
            name, enabled = entry.strip(), True
        elif isinstance(entry, dict):
            name = str(entry.get('name') or '').strip()
            enabled = entry.get('enabled', True) is not False
        else:
            continue
        if not name or len(name) > 160 or '\n' in name or '\r' in name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append({'name': name, 'enabled': enabled})
    return cleaned


def active_group_names(registry):
    """The exact titles passed to the original, unchanged WhatsApp engine."""
    return [entry['name'] for entry in registry if entry['enabled']]


def validated_group_title(value):
    """Reject invalid names without excluding emoji or ordinary punctuation."""
    raw = str(value)
    if any(c in raw for c in ('\r', '\n', '\t')) or any(ord(c) < 32 for c in raw):
        raise ValueError('Group names cannot contain line breaks or control characters.')
    name = raw.strip()
    if not name:
        raise ValueError('Enter a WhatsApp group name.')
    if len(name) > 160:
        raise ValueError('Group names cannot exceed 160 characters.')
    return name


# The settings file stays in ./data even if the main database is relocated.
# This lets the application remember the selected SQLite directory on restart.
configured_db = configuration().get('db_path')
if configured_db:
    DB = Path(configured_db).expanduser().resolve()
    if not DB.is_file():
        raise FileNotFoundError(
            f'Configured SQLite file is missing: {DB}. Restore it or correct {CONFIG}. '
            'The application will not silently create an empty replacement.'
        )


def relocate_database(folder):
    """Snapshot the DB to a NEW directory, switch, and retain the old DB.

    Caller must stop live monitoring before moving. SQLite's backup API includes
    committed WAL data; a plain file copy would not necessarily do so. Never
    overwrite a database already present in the target directory.
    """
    import os
    import shutil
    import tempfile
    from contextlib import closing

    global DB
    destination_dir = Path(folder).expanduser().resolve()
    if not destination_dir.is_dir():
        raise NotADirectoryError(f'Choose an existing folder: {destination_dir}')
    target = destination_dir / 'operations.db'
    original = DB.resolve()
    if target == original:
        return target
    if target.exists():
        raise FileExistsError(f'{target} already exists. Choose another empty folder.')

    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination_dir, prefix='.operations-',
                                         suffix='.db', delete=False) as tmp:
            temp_name = tmp.name
        # Both context managers close before the atomic rename.
        with closing(connect()) as source, closing(sqlite3.connect(temp_name)) as backup:
            source.backup(backup)
        with closing(sqlite3.connect(temp_name)) as verified:
            check = verified.execute('PRAGMA integrity_check').fetchone()[0]
            if check != 'ok':
                raise RuntimeError(f'Copied database failed validation: {check}')
            count_before = verified.execute('SELECT COUNT(*) FROM reports').fetchone()[0]
        with closing(connect()) as source:
            count_after = source.execute('SELECT COUNT(*) FROM reports').fetchone()[0]
        if count_before != count_after:
            raise RuntimeError('Database changed during relocation. Stop imports and retry.')
        # Avoid accidentally overwriting a database introduced during the backup.
        if target.exists():
            raise FileExistsError(f'{target} already exists; no files were overwritten.')
        # Exclusive destination creation prevents overwriting existing DBs.
        try:
            os.link(temp_name, target)
        except OSError:
            # Hardlinks are not available on all Windows/network filesystems.
            # 'xb' is an exclusive-create fallback: never overwrite a file.
            try:
                with open(temp_name, 'rb') as reader, open(target, 'xb') as writer:
                    shutil.copyfileobj(reader, writer)
                    writer.flush()
                    os.fsync(writer.fileno())
            except Exception:
                # Keep the original intact; remove a partial destination only
                # when this process created it (target did not exist earlier).
                raise
        os.unlink(temp_name)
        temp_name = None
        cfg = configuration()
        cfg['db_path'] = str(target)
        # Save the choice before changing the active connection path, so a
        # failed settings write leaves the original connection untouched.
        save_configuration(cfg)
        DB = target
        audit('DATABASE_RELOCATED', f'Main database changed; previous copy retained at {original}')
        return target
    finally:
        if temp_name and Path(temp_name).exists():
            Path(temp_name).unlink()


class _ClosingSQLiteConnection(sqlite3.Connection):
    """Commit/rollback AND close after with-connect blocks (safe for 40+ groups)."""
    def __exit__(self,exc_type,exc_value,traceback):
        try:
            return super().__exit__(exc_type,exc_value,traceback)
        finally:
            self.close()


def connect():
    con=sqlite3.connect(DB,timeout=15,factory=_ClosingSQLiteConnection)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA busy_timeout=15000')
    con.execute('PRAGMA journal_mode=WAL')
    return con

def init_db():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS reports (
          id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
          event_at TEXT NOT NULL, captured_at TEXT NOT NULL,
          group_name TEXT NOT NULL, sender TEXT NOT NULL, message TEXT NOT NULL,
          t002_t003 INTEGER, pwt INTEGER, spl INTEGER, pl INTEGER,
          near_miss INTEGER, late INTEGER, no_shows INTEGER, origin TEXT NOT NULL DEFAULT 'import',
          quality_note TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_reports_event ON reports(event_at);
        CREATE INDEX IF NOT EXISTS idx_reports_group ON reports(group_name);
        CREATE TABLE IF NOT EXISTS alerts (
          id INTEGER PRIMARY KEY, alert_key TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
          level TEXT NOT NULL, title TEXT NOT NULL, detail TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'Open', owner TEXT DEFAULT '', resolved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY, at TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS monitor_state (
          id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL,
          last_checked TEXT, last_success TEXT, last_error TEXT
        );
        INSERT OR IGNORE INTO monitor_state(id,status) VALUES (1,'Stopped');
        ''')
        columns = {r[1] for r in c.execute('PRAGMA table_info(reports)')}
        if 'no_shows' not in columns:
            c.execute('ALTER TABLE reports ADD COLUMN no_shows INTEGER')
        # Existing CSVs/WhatsApp messages keep their original schema. Backfill
        # only explicitly reported numeric no-show counts, never infer from gaps.
        for message_id, raw in c.execute('SELECT id,message FROM reports WHERE no_shows IS NULL'):
            n = parse_explicit_no_shows(raw)
            if n is not None:
                c.execute('UPDATE reports SET no_shows=? WHERE id=?', (n, message_id))

def stamp(): return datetime.now().isoformat(timespec='seconds')

def audit(action,detail):
    with connect() as c: c.execute('INSERT INTO audit(at,action,detail) VALUES(?,?,?)',(stamp(),action,str(detail)[:1500]))

def parse_metrics(message):
    text=' '.join(str(message).split())
    result={}
    patterns={'T002/T003':r'\bT002\s*[/\\_-]\s*T003\s*[:\s-]*([0-9]+)\b',
              'Near Miss':r'\bNear\s+Miss(?:es)?\s*[:\s-]*([0-9]+)\b'}
    for name in METRICS:
        pattern=patterns.get(name, rf'\b{re.escape(name)}\s*[:\s-]*([0-9]+)\b')
        m=re.search(pattern,text,re.I)
        result[name]=int(m.group(1)) if m else None
    return result

def parse_explicit_no_shows(message):
    """Read a numeric no-show count only when WhatsApp explicitly provides one.

    Examples: 'No Shows: 2', 'No-Show 1', 'No show(s) - 0'.
    Missing mention is UNKNOWN, never zero and never equal to shortage.
    """
    text = ' '.join(str(message or '').split())
    match = re.search(r'\bno[\s-]*shows?(?:\(s\))?\s*[:=\-]?\s*(\d{1,5})\b', text, re.I)
    return int(match.group(1)) if match else None


def parse_date(value):
    if isinstance(value,datetime): return value.date()
    if isinstance(value,date): return value
    s=str(value or '').strip()
    for fmt in ('%d/%m/%Y','%Y-%m-%d','%d/%m/%y','%Y-%m-%d %H:%M:%S','%d-%m-%Y'):
        try: return datetime.strptime(s,fmt).date()
        except ValueError: pass
    raise ValueError(f'Unrecognised date: {s!r}')

def normalise_datetime(d,t):
    day=parse_date(d)
    txt=str(t or '00:00:00').strip().upper()
    for fmt in ('%H:%M:%S','%H:%M','%I:%M %p','%I:%M:%S %p'):
        try:
            tt=datetime.strptime(txt,fmt).time()
            return datetime.combine(day,tt).isoformat(timespec='seconds')
        except ValueError: pass
    raise ValueError(f'Unrecognised time: {t!r}')

def fingerprint(group,sender,at,message):
    payload='\0'.join((str(group).strip().casefold(),str(sender).strip().casefold(),at,str(message).strip()))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()

def insert_report(group,sender,message,dt,metrics=None,origin='manual'):
    """Returns True if inserted, False if exact duplicate. Never equates unreported values with zero."""
    group=str(group).strip(); sender=str(sender).strip(); message=str(message).strip()
    if not group or not message: raise ValueError('Group and message cannot be empty')
    at=dt.isoformat(timespec='seconds') if isinstance(dt,datetime) else str(dt)
    datetime.fromisoformat(at) # reject invalid timestamps
    vals=metrics if metrics is not None else parse_metrics(message)
    clean={}
    for k in METRICS:
        v=vals.get(k,None)
        if v in (None,''): clean[k]=None
        else:
            n=int(v)
            if n < 0 or n > 100000: raise ValueError(f'Invalid {k} value: {v}')
            clean[k]=n
    token=fingerprint(group,sender,at,message)
    reported_no_shows = parse_explicit_no_shows(message)
    # Optional structured imports may include a separate No Shows column.
    if metrics is not None and metrics.get('No Shows') not in (None, ''):
        reported_no_shows = _ex_validate_nonneg(metrics['No Shows'], 'No Shows') if '_ex_validate_nonneg' in globals() else int(metrics['No Shows'])
    qnote='Missing safety fields' if clean['Near Miss'] is None or clean['Late'] is None else ''
    with connect() as c:
        cursor=c.execute('''INSERT OR IGNORE INTO reports
          (fingerprint,event_at,captured_at,group_name,sender,message,t002_t003,pwt,spl,pl,near_miss,late,no_shows,origin,quality_note)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (token,at,stamp(),group,sender,message,*(clean[k] for k in METRICS),reported_no_shows,origin,qnote))
        return cursor.rowcount==1

# Only for automatic replay of the original historical staging CSV.
# These specific erroneous legacy dates were identified and explicitly
# rejected by the user in the 10 November 2026 monitoring records.
# Manual historical CSV imports are unaffected.
KNOWN_MISDATED_LEGACY_DATES = frozenset({
    '2026-06-11', '2026-09-11', '2026-10-11'
})


def import_csv(path, *, quarantine_legacy_dates=False):
    added=0; dupes=0; invalid=[]
    with open(path,newline='',encoding='utf-8-sig') as f:
        reader=csv.DictReader(f)
        absent={'Date','Time','Group'}-set(reader.fieldnames or [])
        if absent: raise ValueError(f'CSV missing required columns: {", ".join(sorted(absent))}')
        for number,row in enumerate(reader,2):
            # A blank preformatted spreadsheet row is not an invalid report.
            if not any(str(value or "").strip() for value in row.values()):
                continue
            try:
                dt=normalise_datetime(row['Date'],row['Time'])
                # Old staging files may contain dates written incorrectly by
                # the pre-fix WhatsApp parser. Do not re-import those known
                # bad legacy values during automatic startup reconciliation.
                # This is NOT a global restriction on legitimate historical
                # CSV imports performed manually by the user.
                if quarantine_legacy_dates and dt[:10] in KNOWN_MISDATED_LEGACY_DATES:
                    invalid.append(f'Row {number}: quarantined known legacy date {dt[:10]}')
                    continue
                metric_values={**{k:row.get(k,'') for k in METRICS}, 'No Shows': row.get('No Shows', '')}
                if all(v in ('',None) for v in metric_values.values()) and row.get('Full Message'): metric_values=parse_metrics(row['Full Message'])
                inserted=insert_report(row['Group'],row.get('Sender') or 'Structured export',row.get('Full Message') or ('Structured import: ' + ' | '.join(f'{k}={metric_values.get(k,chr(45))}' for k in METRICS)),dt,metric_values,origin='csv')
                added+=int(inserted); dupes+=int(not inserted)
            except Exception as e:
                invalid.append(f'Row {number}: {e}')
    audit('CSV_IMPORT',f'{Path(path).name}: added={added} duplicates={dupes} rejected={len(invalid)}')
    # If a historical staging CSV contains inconsistent US-formatted dates,
    # do NOT silently reinterpret them; users can review rejected rows.
    return added,dupes,invalid

def import_excel(path):
    from openpyxl import load_workbook
    from tempfile import NamedTemporaryFile
    import os
    wb=load_workbook(path,read_only=True,data_only=True)
    sheet=wb.active
    rows=sheet.iter_rows(values_only=True)
    headers=[str(v) if v is not None else '' for v in next(rows)]
    with NamedTemporaryFile('w',suffix='.csv',delete=False,newline='',encoding='utf-8') as f:
        p=f.name; writer=csv.DictWriter(f,fieldnames=FIELDS);writer.writeheader()
        for values in rows:
            item=dict(zip(headers,values));writer.writerow({k:item.get(k,'') for k in FIELDS})
    wb.close()
    try:return import_csv(p)
    finally:os.unlink(p)

def filtered(start=None,end=None,group=None):
    sql='SELECT * FROM reports WHERE 1=1';params=[]
    if start: sql+=' AND event_at>=?';params.append(str(start)+'T00:00:00')
    if end: sql+=' AND event_at<=?';params.append(str(end)+'T23:59:59')
    if group and group!='All Groups':sql+=' AND group_name=?';params.append(group)
    sql+=' ORDER BY event_at DESC,id DESC'
    with connect() as c:return [dict(r) for r in c.execute(sql,params).fetchall()]

def groups():
    with connect() as c:return [x[0] for x in c.execute('SELECT DISTINCT group_name FROM reports ORDER BY group_name')]

def summary(rows,expected_groups=None):
    count=len(rows);present={r['group_name'] for r in rows}
    expected=set(expected_groups or [])
    total=lambda field:sum(r[field] for r in rows if r[field] is not None)
    daily=Counter(r['event_at'][:10] for r in rows)
    bygroup=Counter(r['group_name'] for r in rows)
    now=datetime.now();last=max((r['event_at'] for r in rows),default=None)
    return dict(reports=count, teams=len(present), late=total('late'), near_miss=total('near_miss'),
                missing_safety=sum(r['near_miss'] is None or r['late'] is None for r in rows),
                missing_groups=sorted(expected-present), coverage=(len(expected & present)/len(expected)*100 if expected else None),
                daily=dict(sorted(daily.items())), bygroup=bygroup,
                staffing={k:total(k) for k in ('t002_t003','pwt','spl','pl')},last_event=last,
                freshness_hours=((now-datetime.fromisoformat(last)).total_seconds()/3600 if last else None))

def compare(rows,start,end):
    if not start or not end: return None
    a=parse_date(start);b=parse_date(end);n=(b-a).days+1
    if n<1:return None
    prior=filtered(a-timedelta(days=n),a-timedelta(days=1))
    curr=summary(rows);prev=summary(prior)
    return {k: (curr[k]-prev[k])/prev[k]*100 if prev[k] else None for k in ('reports','late','near_miss')}

def trend_forecast(rows,days=7):
    """Rolling weekday-aware baseline with uncertainty from historical variability; not causal prediction."""
    if not rows:return [],'No historical data'
    dates=[parse_date(r['event_at'][:10]) for r in rows]; earliest=min(dates); latest=max(dates)
    observed=(latest-earliest).days+1
    if observed<14:return [],'At least 14 calendar days of history needed'
    daily=Counter(dates)
    series=[daily[earliest+timedelta(days=i)] for i in range(observed)]
    recent=series[-min(56,len(series)):]
    predictions=[]
    for i in range(1,days+1):
        target=latest+timedelta(days=i)
        weekday_values=[daily[earliest+timedelta(days=j)] for j in range(observed)
                        if (earliest+timedelta(days=j)).weekday()==target.weekday()]
        vals=weekday_values[-8:] if len(weekday_values)>=3 else recent
        mean=statistics.mean(vals); sigma=statistics.stdev(vals) if len(vals)>1 else 0
        predictions.append({'date':target.isoformat(),'predicted':round(mean,1),
                            'low':max(0,round(mean-1.96*sigma,1)), 'high':round(mean+1.96*sigma,1)})
    return predictions,'Historical weekday baseline; indicative variability band, not a calibrated 95% prediction interval'

def scenarios(demand,available,absence_pct,contractors):
    vals=[float(demand),float(available),float(absence_pct),float(contractors)]
    if min(vals)<0 or absence_pct>100: raise ValueError('Scenario inputs out of range')
    effective=max(0,available*(1-absence_pct/100))+contractors
    return {'effective':effective,'gap':max(0,demand-effective),'surplus':max(0,effective-demand),
            'coverage':(100*effective/demand if demand else 100)}

def upsert_alert(key,level,title,detail):
    with connect() as c:
        c.execute('''INSERT OR IGNORE INTO alerts(alert_key,created_at,level,title,detail) VALUES(?,?,?,?,?)''',
                  (key,stamp(),level,title,detail))

def detect_reporting_anomalies(days=35):
    """Flags unusually low/high daily report counts using a robust median/MAD baseline.
    Informational only; minimum 14 observed calendar days required.
    """
    today=date.today()
    rows=filtered(today-timedelta(days=days-1),today)
    if not rows:return []
    earliest=min(parse_date(x['event_at'][:10]) for x in rows)
    span=(today-earliest).days+1
    if span<14:return []
    observed=Counter(parse_date(x['event_at'][:10]) for x in rows)
    history=[observed[earliest+timedelta(days=i)] for i in range(span-1)]
    if len(history)<13:return []
    baseline=statistics.median(history)
    mad=statistics.median(abs(v-baseline) for v in history)
    robust_sigma=max(1.0,1.4826*mad)
    current=observed[today]
    if abs(current-baseline)<max(3,3*robust_sigma):return []
    direction='above' if current>baseline else 'below'
    return [f'Today currently has {current} report(s), {direction} historical daily median {baseline:g}; incomplete day / collection gaps may explain differences.']

def evaluate_alerts():
    cfg=configuration();today=date.today();rows=filtered(today-timedelta(days=1),today)
    stats=summary(rows, cfg['groups'])
    if stats['near_miss'] >= cfg['alert_near_miss_threshold'] and stats['near_miss']>0:
        upsert_alert('safety-'+today.isoformat(),'High','Near-miss reports require review',
                     f"{stats['near_miss']} reported near-miss values over last two calendar days; verify events and duplicates.")
    if stats['late'] >= cfg['alert_late_threshold']:
        upsert_alert('late-'+today.isoformat(),'Medium','Late reporting above threshold',
                     f"{stats['late']} reported late values across last two calendar days.")
    for group in cfg['groups']:
        with connect() as c:
            last=c.execute('SELECT MAX(event_at) FROM reports WHERE group_name=?',(group,)).fetchone()[0]
        if last and (datetime.now()-datetime.fromisoformat(last)).total_seconds()>cfg['alert_missing_hours']*3600:
            upsert_alert('missing-'+group+'-'+today.isoformat(),'Medium','Possible missing team reporting',
                         f'{group}: latest captured message {last}. Check expected reporting schedule.')
    for index,description in enumerate(detect_reporting_anomalies()):
        upsert_alert('anomaly-'+today.isoformat()+'-'+str(index),'Info','Unusual reporting volume',description)
    if stats['missing_safety']:
        upsert_alert('quality-'+today.isoformat(),'Info','Incomplete safety reporting fields',
                     f"{stats['missing_safety']} reports have an unreported Near Miss or Late field (not assumed zero).")

def alerts(status=None):
    with connect() as c:
        query='SELECT * FROM alerts'+(' WHERE status=?' if status else '')+' ORDER BY CASE level WHEN "High" THEN 0 WHEN "Medium" THEN 1 ELSE 2 END, created_at DESC'
        return [dict(x) for x in c.execute(query,(status,) if status else ())]

def update_alert(id,status,owner=''):
    if status not in ('Open','Acknowledged','Closed'):raise ValueError('Invalid alert state')
    with connect() as c:
        c.execute('UPDATE alerts SET status=?,owner=?,resolved_at=? WHERE id=?',
                  (status,owner,stamp() if status=='Closed' else None,id))
    audit('ALERT_UPDATE',f'{id}: {status} owner={owner}')

def set_monitor_state(status,error=None,success=False):
    with connect() as c:
        c.execute('''UPDATE monitor_state SET status=?,last_checked=?,last_success=CASE WHEN ? THEN ? ELSE last_success END,
                     last_error=? WHERE id=1''',(status,stamp(),int(success),stamp(),error))

def monitor_state():
    with connect() as c:return dict(c.execute('SELECT * FROM monitor_state WHERE id=1').fetchone())

# Exports include the sender when known, but NEVER the original raw message.
EXPORT_FIELDS = ['Date','Time','Group','Sender','T002/T003','PWT','SPL','PL','Near Miss','Late']

def export_rows(rows):
    for r in rows:
        dt=datetime.fromisoformat(r['event_at'])
        yield [dt.strftime('%d/%m/%Y'), dt.strftime('%H:%M:%S'), r['group_name'],
               (r.get('sender') or '') if (r.get('sender') or '') not in ('Unknown Sender', 'Structured export') else ''] + [
            r[METRICS[k]] if r[METRICS[k]] is not None else '' for k in METRICS
        ]

def export_csv(path,rows):
    with open(path,'w',newline='',encoding='utf-8-sig') as f:
        writer=csv.writer(f);writer.writerow(EXPORT_FIELDS);writer.writerows(export_rows(rows))
    audit('CSV_EXPORT',f'{len(rows)} structured records (no raw messages)')

def export_excel(path,rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill,Alignment
    wb=Workbook();ws=wb.active;ws.title='Operational Reports'
    ws.append(EXPORT_FIELDS)
    for record in export_rows(rows):ws.append(record)
    ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
    for cell in ws[1]:
        cell.fill=PatternFill('solid',fgColor='17243A')
        cell.font=Font(bold=True,color='FFFFFF')
        cell.alignment=Alignment(horizontal='center')
    for col,width in {'A':15,'B':14,'C':28,'D':26,'E':17,'F':12,'G':12,'H':12,'I':16,'J':14}.items():
        ws.column_dimensions[col].width=width
    wb.save(path);audit('EXCEL_EXPORT',f'{len(rows)} structured records (no raw messages)')

def safety_daily(rows):
    days=defaultdict(lambda:{'near_miss':0,'late':0})
    for r in rows:
        d=r['event_at'][:10]
        days[d]['near_miss']+=r['near_miss'] or 0
        days[d]['late']+=r['late'] or 0
    return dict(sorted(days.items()))


def near_miss_reported_series(rows):
    """Return sorted dates with received reports only (no fabricated days).

    This aggregation is calculated afresh from the currently filtered rows.
    A date with a report but a reported near-miss count of zero remains
    visible; a date with no reports is not included at all.
    """
    return list(safety_daily(rows).items())


def near_miss_calendar_series(rows, max_days=None):
    """Backward-compatible wrapper for notebooks using the earlier name.

    No continuous calendar or fixed number-of-days cap is applied.
    The boolean marks actual reported dates, always True here.
    """
    return [(day, values, True) for day, values in near_miss_reported_series(rows)]


def plot_near_miss_calendar(ax, rows, *, dark=True):
    """Dynamic near-miss chart: one bar and DD-MM label per *reported* day.

    Do not insert blank calendar dates or skip labels using a fixed stride.
    A user changing a date/group filter causes the chart to recompute its
    dates directly from the filtered records passed by the dashboard.
    """
    ax.clear()
    series = near_miss_reported_series(rows)
    ink = '#AFC0D3' if dark else '#4D5C6F'
    grid = '#38475C' if dark else '#D3DCE8'
    if not series:
        ax.text(.5, .5, 'No reports for selected period', color=ink,
                ha='center', va='center', transform=ax.transAxes)
    else:
        xs = list(range(len(series)))
        values = [counts['near_miss'] for _, counts in series]
        ax.bar(xs, values, width=.62, color='#D35400', zorder=2)
        n = len(series)
        # Label every *reported* day. Adapt label angle as data grows.
        angle = 0 if n <= 12 else (45 if n <= 24 else 75)
        font_size = 8 if n <= 20 else 7
        ax.set_xticks(xs)
        ax.set_xticklabels([uk_chart_date(day) for day, _ in series],
                           rotation=angle,
                           ha='right' if angle else 'center', fontsize=font_size)
        ax.set_xlim(-.65, n - .35)
    ax.tick_params(colors=ink, labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(grid)
    ax.grid(axis='y', color=grid, alpha=.4, zorder=0)

def gang_attendance(rows):
    """Aggregate workforce category values AND lateness by gang.

    Late is an attendance indicator, not a workforce category. It is drawn as
    an independent bar series, never included in workforce pie denominators.
    """
    result = defaultdict(lambda: {k: 0 for k in
                           ('t002_t003', 'pwt', 'spl', 'pl', 'late')})
    for row in rows:
        name = row.get('group_name') or 'Unknown group'
        for key in result[name]:
            result[name][key] += row.get(key) or 0
    return dict(sorted(result.items()))


def safety_completeness(rows):
    """Return numerator, denominator and percentage of complete safety fields."""
    total = len(rows)
    complete = sum(r.get('near_miss') is not None and r.get('late') is not None
                   for r in rows)
    return complete, total, (100 * complete / total if total else None)


def near_miss_by_group(rows):
    """Reported near-miss values by group; not a confirmed incident count."""
    result = Counter()
    for row in rows:
        result[row.get('group_name') or 'Unknown group'] += row.get('near_miss') or 0
    return dict(sorted(result.items(), key=lambda item: (-item[1], item[0])))


def workforce_totals(rows):
    keys=['t002_t003','pwt','spl','pl']
    return [sum((r[k] or 0) for r in rows) for k in keys]

def original_style_pie(ax,labels,values,colors,dark=True):
    """Original notebook styling, with corrected workforce-only denominator."""
    ax.clear()
    indices=[i for i,v in enumerate(values) if v>0]
    if not indices:
        ax.text(.5,.5,'No data in selected period',ha='center',va='center',color='#a0a0a0' if dark else '#666666',transform=ax.transAxes)
        return
    ax.pie([values[i] for i in indices],labels=[labels[i] for i in indices],
           colors=[colors[i] for i in indices],autopct='%1.1f%%',startangle=140,
           textprops={'color':'#ffffff' if dark else '#202C3D','fontsize':8})
    ax.axis('equal')

def uk_chart_date(iso_day):
    """Display a compact UK DD-MM label for Executive near-miss charts only.

    Event timestamps remain stored in ISO format. CSV and Excel exports keep
    their required full DD/MM/YYYY date format. Do not alter database dates.
    """
    return datetime.strptime(iso_day[:10], '%Y-%m-%d').strftime('%d-%m')


# ---------------------------------------------------------------------------
# C-SUITE EXPORTS — SAME EXCEPTION MODEL AS EXECUTIVE DASHBOARD
# ---------------------------------------------------------------------------
# The previous PDF and HTML exporters only accepted WhatsApp message rows.
# Exports now accept the complete exception_dashboard rows for the SAME scope.

def _build_report_export_payload(rows, exception_rows=None):
    """Build a lossless, offline reporting snapshot.

    If exception_rows is omitted by a legacy caller, derive the applicable
    group/date range from the supplied WhatsApp reports. The GUI always passes
    the selected exception_dashboard explicitly, including before-report entries.
    """
    source_rows = list(rows or [])
    if exception_rows is None:
        if source_rows:
            dates = sorted(r['event_at'][:10] for r in source_rows)
            names = {r['group_name'].casefold() for r in source_rows}
            selected = source_rows[0]['group_name'] if len(names) == 1 else 'All Groups'
            exception_rows = exception_dashboard(dates[0], dates[-1], selected)['rows']
        else:
            exception_rows = []
    role_names = list(EX_METRIC_FIELDS)
    exceptions = []
    for record in exception_rows:
        roles = [{k:int(part.get(k,0)) for k in ('required','present','shortage','surplus')}
                 | {'role':str(part.get('role',''))}
                 for part in record.get('role_breakdown',[])]
        exceptions.append({
            'date':str(record['service_date']),
            'group':str(record['group']),
            'before':record.get('before_count'),
            'after':record.get('after_count'),
            'no_shows':record.get('no_shows'),
            'required':record.get('plan_total'),
            'available':record.get('available'),
            'shortage':record.get('shortage'),
            'status':str(record.get('status','Pending')),
            'reason':str(record.get('reason') or ''),
            'report_id':record.get('report_id'),
            'roles':roles
        })
    exceptions.sort(key=lambda x:(x['date'],x['group'].casefold()),reverse=True)
    # Night-shift attendance can be captured AFTER MIDNIGHT. If a source
    # report is linked to an exception service date but falls outside the
    # message-date UI filter, include that evidence explicitly and avoid a
    # duplicate of any report already selected by message date.
    linked_days={int(x['report_id']):x['date'] for x in exceptions
                 if x['report_id'] is not None}
    selected_ids={int(x['id']) for x in source_rows if x.get('id') is not None}
    need_ids=sorted(set(linked_days)-selected_ids)
    if need_ids:
        with connect() as conn:
            for start in range(0,len(need_ids),300):
                batch=need_ids[start:start+300]
                qmarks=','.join('?' for _ in batch)
                source_rows.extend(dict(x) for x in conn.execute(
                    'SELECT * FROM reports WHERE id IN ('+qmarks+')',batch))
    reports = []
    for r in source_rows:
        reports.append({
            'day':linked_days.get(r.get('id'),str(r['event_at'])[:10]),
            'when':str(r['event_at'])[:19].replace('T',' '),
            'group':str(r['group_name']),
            'sender':str(r.get('sender') or ''),
            **{k:r.get(k) for k in ('t002_t003','pwt','spl','pl','near_miss','late','no_shows')}
        })
    reports.sort(key=lambda x:(x['when'],x['group'].casefold()),reverse=True)
    known = lambda k: [x for x in exceptions if x[k] is not None]
    totals = {
        'reports':len(reports),
        'groups':len({x['group'] for x in exceptions} | {x['group'] for x in reports}),
        'changes_before':sum(int(x['before']) for x in known('before')),
        'changes_after':sum(int(x['after']) for x in known('after')),
        'before_known':len(known('before')),
        'after_known':len(known('after')),
        'no_shows':sum(int(x['no_shows']) for x in known('no_shows')),
        'no_shows_known':len(known('no_shows')),
        'shortage':sum(int(x['shortage']) for x in known('shortage')),
        'shortage_known':len(known('shortage')),
        'pending':sum(x['shortage'] is None for x in exceptions),
        'changes_complete':sum(x['before'] is not None and x['after'] is not None for x in exceptions),
        'near_miss':sum(int(x['near_miss'] or 0) for x in reports),
        'late':sum(int(x['late'] or 0) for x in reports)
    }
    return {'reports':reports,'exceptions':exceptions,'totals':totals,
            'roles':role_names,'generated_at':stamp()}


def export_interactive_html(path, rows, scope='All Groups', exception_rows=None):
    """Full-fidelity offline executive HTML with filtering and 4-category charts."""
    import html as _htmlmod
    payload=_build_report_export_payload(rows,exception_rows)
    # Encode script-breaking characters even when strings come from WhatsApp.
    embedded=json.dumps(payload,ensure_ascii=True,separators=(',',':'))
    for ch,replacement in [('<',r'\u003c'),('>',r'\u003e'),('&',r'\u0026'),
                           ('\u2028',r'\u2028'),('\u2029',r'\u2029')]:
        embedded=embedded.replace(ch,replacement)
    output=_HTML_REPORT_TEMPLATE.replace('__PAYLOAD__',embedded)
    output=output.replace('__SCOPE__',_htmlmod.escape(str(scope),quote=True))
    output=output.replace('__GENERATED__',_htmlmod.escape(payload['generated_at'],quote=True))
    Path(path).write_text(output,encoding='utf-8')
    audit('HTML_EXPORT',f'{scope} / {len(payload["exceptions"])} exception records')
    return path



# -------------------------------------------------------------------------
# v3.0.0: Additional board charts matching existing Workforce / Safety views.
# All chart data is derived from the same exported WhatsApp report snapshot.
# -------------------------------------------------------------------------
def _report_chart_dataset(report_rows):
    gang = {}
    near = {}
    roles = ('t002_t003','pwt','spl','pl')
    allocation = {key: 0 for key in roles}
    for row in report_rows:
        name = str(row.get('group') or 'Unknown group')
        values = gang.setdefault(name,{key:0 for key in (*roles,'late')})
        for key in values:
            values[key] += int(row.get(key) or 0)
        near[name] = near.get(name,0) + int(row.get('near_miss') or 0)
        for key in roles:
            allocation[key] += int(row.get(key) or 0)
    return {'gang':gang, 'near':near, 'allocation':allocation}

def _append_pdf_workforce_and_safety_charts(story,reports,heading,p,figimage):
    from matplotlib.figure import Figure
    metrics=_report_chart_dataset(reports)
    columns=[('t002_t003','T002/T003','#2980b9'),('pwt','PWT','#2ecc71'),
             ('spl','SPL','#e67e22'),('pl','PL','#e74c3c'),('late','Late','#f1c40f')]
    story.append(heading('Gang-wise attendance — stacked horizontal comparison'))
    names=sorted(metrics['gang'],key=str.casefold)
    if names:
        for start in range(0,len(names),12):
            segment=names[start:start+12]
            fig=Figure(figsize=(8.2,max(2.5,.38*len(segment)+1.0)))
            ax=fig.add_subplot(111)
            y=list(range(len(segment)));left=[0]*len(segment)
            for key,title,color in columns:
                v=[metrics['gang'][name][key] for name in segment]
                ax.barh(y,v,left=left,label=title,color=color,height=.68)
                left=[a+b for a,b in zip(left,v)]
            ax.set_yticks(y,[(n[:26]+'…') if len(n)>27 else n for n in segment],fontsize=8)
            ax.invert_yaxis();ax.grid(axis='x',alpha=.18);ax.set_axisbelow(True)
            ax.legend(loc='lower center',bbox_to_anchor=(.5,1.02),ncol=5,fontsize=7,frameon=False)
            fig.subplots_adjust(left=.29,right=.97,bottom=.09,top=.86)
            if len(names)>12:
                story.append(p(f'Groups {start+1}–{start+len(segment)} of {len(names)}','ReportSmall'))
            story.append(figimage(fig,min(350,max(155,70+21*len(segment)))))
    else:
        story.append(p('No gang-wise WhatsApp attendance in the selected period.'))
    story.append(p('Stacked bars use T002/T003, PWT, SPL and PL reported values by group. '
                   'Late is a separate attendance exception indicator, not a staffing role. '
                   'Multiple WhatsApp messages may include the same people.','ReportSmall'))
    from reportlab.platypus import KeepTogether
    positive=sorted(((g,n) for g,n in metrics['near'].items() if n>0),
                    key=lambda x:(-x[1],x[0].casefold()))
    if len(positive)>12:
        display_near=positive[:11]+[('Other groups',sum(v for _,v in positive[11:]))]
    else:
        display_near=positive
    role_labels=[('t002_t003','T002/T003'),('pwt','PWT'),('spl','SPL'),('pl','PL')]
    allocation=[(title,metrics['allocation'][key]) for key,title in role_labels
                if metrics['allocation'][key]>0]
    fig=Figure(figsize=(8.2,4.2))
    ax1=fig.add_subplot(121);ax2=fig.add_subplot(122)
    near_colors=['#d35400','#2980b9','#27ae60','#8e44ad','#f39c12',
                 '#16a085','#c0392b','#597cbb','#ae7ac0','#b9a045','#7a9e49','#e18a71']
    def add_pie(ax,items,colors,title):
        ax.set_title(title,fontsize=10,pad=8,color='#15263E')
        if not items:
            ax.text(.5,.5,'No positive reported values',ha='center',va='center',
                    fontsize=9,transform=ax.transAxes)
            ax.axis('off');return
        total=sum(v for _,v in items)
        ax.pie([v for _,v in items],startangle=135,
               colors=[colors[i%len(colors)] for i in range(len(items))],
               autopct=lambda pct:f'{pct:.0f}%' if pct>=5 else '',pctdistance=.75,
               textprops={'fontsize':7,'color':'white'},
               wedgeprops={'linewidth':.7,'edgecolor':'white'})
        ax.legend([f'{name[:18]}  {100*v/total:.1f}%' for name,v in items],
                  loc='upper center',bbox_to_anchor=(.5,-.02),fontsize=6.7,
                  frameon=False,ncol=2 if len(items)>6 else 1)
        ax.set_aspect('equal')
    add_pie(ax1,display_near,near_colors,'Near misses by group')
    add_pie(ax2,allocation,['#4d9ff8','#18c4ac','#f3c66b','#a78bfa'],
            'Workforce allocation by role')
    fig.subplots_adjust(left=.03,right=.97,top=.90,bottom=.19,wspace=.21)
    story.append(figimage(fig,260))
    note=('For readability, smaller group shares are combined under Other groups in the PDF; '
          'the HTML displays each group separately. ' if len(positive)>12 else '')
    story.append(KeepTogether([
        heading('Near-miss share by group and workforce allocation'),
        figimage(fig,260),
        p(note+'Near-miss percentages show each group’s share of reported near-miss values, '
          'not confirmed incidents or per-employee risk. Workforce allocation percentages '
          'use reported T002/T003, PWT, SPL and PL values only; Late is excluded.','ReportSmall')
    ]))

def export_pdf(path, rows, scope='All Groups', exception_rows=None):
    """Board-ready PDF containing both source WhatsApp and staffing exceptions.

    Multi-page with charts, quality status, detailed exception register, and
    a bounded message appendix. The HTML export preserves *every* source row.
    """
    from io import BytesIO
    from xml.sax.saxutils import escape as X
    from matplotlib.figure import Figure
    from reportlab.lib import colors as C
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                     TableStyle, Image, PageBreak, KeepTogether)
    dataset=_build_report_export_payload(rows,exception_rows)
    e=dataset['exceptions'];r=dataset['reports'];t=dataset['totals']
    navy='#15263E';blue='#4d9ff8';teal='#18c4ac';purple='#a78bfa';red='#fb7185';gold='#f3c66b'
    series=[('before','Changes before 15:00',blue),('after','Changes after 15:00',teal),
            ('no_shows','No Shows',purple),('shortage','Shortages',red)]
    def display(value):
        return '—' if value is None else str(value)
    def shortened(name,n=23):
        return name[:n-1]+'…' if len(name)>n else name
    styles=getSampleStyleSheet()
    styles.add(ParagraphStyle(name='ReportTitle',parent=styles['Title'],fontName='Helvetica-Bold',fontSize=20,
                              leading=24,textColor=C.HexColor(navy),spaceAfter=9))
    styles.add(ParagraphStyle(name='ReportSection',parent=styles['Heading2'],fontName='Helvetica-Bold',fontSize=13,
                              leading=17,textColor=C.HexColor(navy),spaceBefore=15,spaceAfter=8))
    styles.add(ParagraphStyle(name='ReportBody',parent=styles['Normal'],fontSize=8.6,leading=12,
                              textColor=C.HexColor('#35475A'),spaceAfter=7))
    styles.add(ParagraphStyle(name='ReportSmall',parent=styles['Normal'],fontSize=7.2,leading=10,
                              textColor=C.HexColor('#53667A'),spaceAfter=7))
    styles.add(ParagraphStyle(name='ReportLabel',parent=styles['Normal'],fontName='Helvetica-Bold',fontSize=8,
                              textColor=C.HexColor('#62728A')))
    doc=SimpleDocTemplate(str(path),pagesize=A4,
         leftMargin=43,rightMargin=43,topMargin=48,bottomMargin=43,
         title='Operational Intelligence - Executive Reporting',author='Operational Intelligence')
    width=A4[0]-86
    def footer(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(C.HexColor('#D7E0E9'));canvas.line(43,37,A4[0]-43,37)
        canvas.setFont('Helvetica',7);canvas.setFillColor(C.HexColor('#586C80'))
        canvas.drawString(43,26,'INTERNAL • Operational Intelligence • Point-in-time snapshot')
        canvas.drawRightString(A4[0]-43,26,f'Page {_doc.page}')
        canvas.restoreState()
    def p(text,style='ReportBody'):
        return Paragraph(X(str(text)),styles[style])
    def heading(text):return Paragraph(X(text),styles['ReportSection'])
    def figimage(fig,h=190):
        stream=BytesIO();fig.savefig(stream,format='png',dpi=125,bbox_inches='tight',facecolor='white');stream.seek(0)
        image=Image(stream,width=width,height=h)
        image.hAlign='CENTER'
        return image
    def table(items,colWidths,small=7,firstrow=True):
        raw=[[str(v) for v in row] for row in items]
        tbl=Table(raw,colWidths=colWidths,repeatRows=1 if firstrow else 0,hAlign='LEFT')
        tbl.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),C.HexColor(navy)),
            ('TEXTCOLOR',(0,0),(-1,0),C.white),
            ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),
            ('FONTSIZE',(0,0),(-1,-1),small),
            ('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),
            ('LINEBELOW',(0,0),(-1,-1),.3,C.HexColor('#E2E9F0')),
            ('ROWBACKGROUNDS',(0,1),(-1,-1),[C.white,C.HexColor('#F1F5F9')]),
            ('VALIGN',(0,0),(-1,-1),'TOP'),
        ]))
        return tbl
    story=[Paragraph('OPERATIONAL INTELLIGENCE',styles['ReportTitle']),
           p('EXECUTIVE WORKFORCE, OPERATIONS AND SAFETY REPORT','ReportLabel'),
           p('Scope: '+str(scope)+'  |  Generated: '+dataset['generated_at']+'  |  Source: local database','ReportSmall'),
           Spacer(1,9)]
    v=lambda k,kn:display(t[k]) if t[kn] else '—'
    cards=[('WhatsApp reports',str(t['reports'])),('Groups represented',str(t['groups'])),
           ('Before 15:00',v('changes_before','before_known')),('After 15:00',v('changes_after','after_known')),
           ('Explicit no-shows',v('no_shows','no_shows_known')),('Calculated shortages',v('shortage','shortage_known')),
           ('Pending nights',str(t['pending'])),('Near-miss reports',str(t['near_miss']))]
    def kpicell(label,value):
        return [Paragraph(X(label),styles['ReportLabel']),Paragraph(X(value),styles['Heading2'])]
    story.append(table([[f'{lab}\n{val}' for lab,val in cards[:4]],
                        [f'{lab}\n{val}' for lab,val in cards[4:]]],[width/4]*4,small=9,firstrow=False))
    story.extend([Spacer(1,13),heading('Management interpretation'),
        p('The WhatsApp message represents final actual night-shift attendance after staffing changes. '
          'Shortages are calculated separately for each required role against the active staffing plan; '
          'operator-entered changes and WhatsApp-reported no-shows are not deducted a second time.'),
        p(f"Confirmed shortage calculations: {t['shortage_known']} night(s). Pending nights: {t['pending']}. "
          f"Both change counts submitted: {t['changes_complete']} night(s). "
          f"Explicit no-show counts: {t['no_shows_known']} night(s). Unreported values remain unknown.",'ReportSmall'),
        heading('Workforce changes, absences and shortages')])
    grouped={}
    for item in e:
        g=grouped.setdefault(item['group'],{k:0 for k,_,_ in series})
        for k,_,_ in series:g[k]+=int(item[k] or 0)
    ranking=sorted(grouped.items(),key=lambda kv:(-sum(kv[1].values()),kv[0].casefold()))[:12]
    fig=Figure(figsize=(7.6,3.1 if ranking else 1.6));ax=fig.add_subplot(111)
    if ranking:
        y=list(range(len(ranking)));left=[0]*len(ranking)
        for k,label,color in series:
            values=[pair[1][k] for pair in ranking]
            ax.barh(y,values,left=left,label=label,color=color,height=.68)
            left=[a+b for a,b in zip(left,values)]
        ax.set_yticks(y,[shortened(pair[0],22) for pair in ranking],fontsize=8)
        ax.invert_yaxis();ax.legend(loc='upper center',bbox_to_anchor=(.5,1.20),ncol=4,fontsize=7,frameon=False)
        ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True)
    else:
        ax.text(.5,.5,'No exceptions in selected scope',ha='center',va='center',transform=ax.transAxes)
        ax.axis('off')
    fig.tight_layout();story.append(figimage(fig,200 if ranking else 115))
    story.append(p('Bars show four independent event categories, not unique people. Values of zero are displayed only when '
                   'the source is known; unreported no-show counts are not treated as confirmed zero.','ReportSmall'))
    story.append(heading('Daily workforce exception trend'))
    dates=sorted(set(x['date'] for x in e))[-14:]
    fig=Figure(figsize=(7.7,2.5));ax=fig.add_subplot(111)
    if dates:
        positions=list(range(len(dates)));bar_width=.18
        for index,(k,label,color) in enumerate(series):
            vals=[sum(int(x[k] or 0) for x in e if x['date']==d) for d in dates]
            ax.bar([q+(index-1.5)*bar_width for q in positions],vals,width=bar_width,label=label,color=color)
        ax.set_xticks(positions,[d[5:] for d in dates],fontsize=7,rotation=35)
        ax.legend(ncol=4,fontsize=7,frameon=False)
        ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    else:
        ax.text(.5,.5,'No dated exception entries',ha='center',va='center',transform=ax.transAxes);ax.axis('off')
    fig.tight_layout();story.append(figimage(fig,170))
    story.extend([heading('Role-specific staffing requirements and gaps')])
    totals={k:{'required':0,'present':0,'shortage':0} for k in dataset['roles']}
    for item in e:
        if item['shortage'] is None:continue
        for role in item['roles']:
            if role['role'] in totals:
                for key in ('required','present','shortage'):totals[role['role']][key]+=int(role[key])
    fig=Figure(figsize=(7.5,2.35));ax=fig.add_subplot(111)
    if t['shortage_known']:
        names=list(totals);y=list(range(len(names)))
        ax.barh([n+.18 for n in y],[totals[k]['present'] for k in names],height=.35,color=teal,label='Actual reported')
        ax.barh([n-.18 for n in y],[totals[k]['required'] for k in names],height=.35,color=blue,label='Required')
        ax.set_yticks(y,names);ax.invert_yaxis();ax.legend(frameon=False,fontsize=8)
        ax.grid(axis='x',alpha=.2);ax.set_axisbelow(True)
    else:
        ax.text(.5,.5,'No role-level shortage calculation available',ha='center',va='center',transform=ax.transAxes);ax.axis('off')
    fig.tight_layout();story.append(figimage(fig,145))
    story.append(p('Role shortages: '+', '.join(f'{name}: {v["shortage"]}' for name,v in totals.items())+'. '
                   'A surplus in another role does not cancel a role-specific deficit.','ReportSmall'))
    story.append(heading('Exception register — latest service-date entries'))
    head=['Service date','Group','Before','After','No shows','Plan / Actual','Short','Status']
    lines=[]
    for item in e[:120]:
        lines.append([item['date'],shortened(item['group'],24),display(item['before']),display(item['after']),
                      display(item['no_shows']),f'{display(item["required"])} / {display(item["available"])}',
                      display(item['shortage']),item['status']])
    if lines:story.append(table([head]+lines,[65,94,40,40,49,75,32,57],small=6.5))
    else:story.append(p('No exception entries in this reporting scope.'))
    if len(e)>len(lines):story.append(p(f'Showing {len(lines)} of {len(e)} exception records; the interactive HTML includes all.','ReportSmall'))
    story.append(heading('WhatsApp attendance and safety reporting'))
    attendance=['T002/T003','PWT','SPL','PL']
    source_keys=['t002_t003','pwt','spl','pl']
    values=[sum(int(x[key] or 0) for x in r) for key in source_keys]
    fig=Figure(figsize=(7.6,2.3));ax=fig.add_subplot(111)
    ax.bar(attendance,values,color=[blue,teal,gold,purple])
    ax.set_ylabel('Sum of reported message values',fontsize=8)
    ax.grid(axis='y',alpha=.2);ax.set_axisbelow(True)
    fig.tight_layout();story.append(figimage(fig,155))
    story.append(p('Attendance is aggregated from WhatsApp report values; multiple reports may refer to the same people, '
                   'so this is not a deduplicated employee count.','ReportSmall'))
    _append_pdf_workforce_and_safety_charts(story,r,heading,p,figimage)
    story.append(heading('Near-miss and Late metrics'))
    fig=Figure(figsize=(7.5,2.4));ax=fig.add_subplot(111)
    near_dates=sorted({row['day'] for row in r})[-18:]
    if near_dates:
        near=[sum(int(q['near_miss'] or 0) for q in r if q['day']==day) for day in near_dates]
        late=[sum(int(q['late'] or 0) for q in r if q['day']==day) for day in near_dates]
        ax.plot(near_dates,near,label='Near Miss',color=red,marker='o',linewidth=1.2)
        ax.plot(near_dates,late,label='Late',color=blue,marker='s',linewidth=1.2)
        ax.tick_params(axis='x',labelrotation=45,labelsize=7);ax.legend(frameon=False)
        ax.grid(axis='y',alpha=.2)
    else:
        ax.text(.5,.5,'No WhatsApp safety reports in selected scope',ha='center',va='center',transform=ax.transAxes);ax.axis('off')
    fig.tight_layout();story.append(figimage(fig,175))
    story.append(p(f'Reported near-miss values: {t["near_miss"]}; Late values: {t["late"]}. '
                   'Both are source-reported indicators, not verified incident counts.','ReportSmall'))
    story.append(heading('Recent WhatsApp report evidence'))
    head=['Message date','Group','T002/T003','PWT','SPL','PL','Near Miss','Late']
    items=[]
    for row in r[:70]:
        items.append([row['when'][:16],shortened(row['group'],23)]+[display(row[key])
                 for key in source_keys+['near_miss','late']])
    if items:story.append(table([head]+items,[91,106,61,36,36,36,63,43],small=6.4))
    else:story.append(p('No WhatsApp reports in this period.'))
    if len(r)>len(items):story.append(p(f'Showing {len(items)} of {len(r)} received messages. The HTML export includes all.','ReportSmall'))
    story.append(heading('Definitions and data quality'))
    story.append(p('Before / after 15:00: counts manually saved by a signed-in coordinator. '
                   'No-show counts are included only when explicitly stated in a WhatsApp message. '
                   '“—” denotes unknown or not submitted; it is not zero. '
                   'Shortage: sum across staffing roles of max(0, required minus final actual attendance). '
                   'A night can have a known staffing shortage while its change-entry status remains incomplete. '
                   'After-midnight WhatsApp reports can be linked to their previous service date.'))
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    audit('PDF_EXPORT',f'{scope} / {len(e)} exception records')
    return path


_HTML_REPORT_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Operational Intelligence | Executive report</title>
<style>
:root{--bg:#0b1422;--surface:#152338;--surface2:#1b2c43;--border:#324860;--text:#f1f6fc;--muted:#b2c5da;--blue:#4d9ff8;--teal:#18c4ac;--violet:#a78bfa;--red:#fb7185;--gold:#f3c66b;--good:#40d5a0}
*{box-sizing:border-box}body{margin:0;background:linear-gradient(120deg,#0a1220,#0e1b2b 55%,#0c1729);color:var(--text);font:14px/1.5 Inter,Segoe UI,system-ui,sans-serif}main{max-width:1450px;margin:0 auto;padding:26px 30px 65px}header{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;padding:12px 0 25px;border-bottom:1px solid var(--border)}h1{font-size:30px;letter-spacing:-.03em;margin:2px 0 6px}h2{font-size:19px;letter-spacing:-.02em;margin:0 0 12px}h3{font-size:13px;color:#b3c7dd;letter-spacing:.05em;text-transform:uppercase;margin:0 0 12px}.eyebrow{font-size:11px;text-transform:uppercase;letter-spacing:.18em;color:var(--teal);font-weight:700}.muted,.hint{color:var(--muted)}.hint{font-size:12px}.pill{font-size:12px;border:1px solid #397565;padding:8px 12px;background:#163b35;border-radius:20px;white-space:nowrap}.controlbar{display:flex;flex-wrap:wrap;align-items:end;gap:11px;margin:21px 0 13px}.field{display:flex;flex-direction:column;gap:5px;min-width:115px}.field label{font-size:11px;color:var(--muted)}select,input,button{background:#1c3048;color:#fff;border:1px solid #47627f;padding:10px 12px;border-radius:9px;font:inherit;min-height:40px}select:focus,input:focus,button:focus{outline:2px solid var(--blue);outline-offset:2px}button{cursor:pointer;font-weight:600}button:hover{background:#29445f}.nowrap{white-space:nowrap}.scope{padding:11px 14px;border-radius:9px;background:#172c40;border-left:3px solid var(--blue);color:#c8d7e6;margin-bottom:18px}.cards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0 23px}.card,.panel{background:var(--surface);border:1px solid var(--border);border-radius:14px;box-shadow:0 9px 25px #0001}.card{padding:16px 18px}.card .name{font-size:11px;text-transform:uppercase;letter-spacing:.075em;color:var(--muted)}.card .value{font-size:28px;letter-spacing:-.04em;font-weight:750;margin:3px 0;color:#f3f7fc}.card .sub{font-size:11px;color:var(--muted)}.sectionhead{margin-top:32px;margin-bottom:14px;display:flex;justify-content:space-between;align-items:end;gap:15px}.sectionhead p{margin:0;color:var(--muted)}.columns{display:grid;grid-template-columns:1.3fr 1fr;gap:13px}.panel{padding:18px;min-width:0}.legend{display:flex;flex-wrap:wrap;gap:14px;margin:7px 0 15px}.legend b{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px}.hrow{display:grid;grid-template-columns:minmax(110px,180px) 1fr 52px;gap:9px;align-items:center;padding:7px 0;border-bottom:1px solid #2b4056}.hlabel{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.track{height:17px;display:flex;overflow:hidden;border-radius:5px;background:#293a50}.segment{height:100%;min-width:0}.hvalue{text-align:right;color:var(--muted);font-variant-numeric:tabular-nums}.empty{padding:45px 12px;text-align:center;color:var(--muted)}svg{display:block;max-width:100%}.svgwrap{overflow-x:auto}.smallnote{color:var(--muted);font-size:11px;margin-top:12px}.tablewrap{overflow-x:auto;max-height:530px;overflow-y:auto;border:1px solid var(--border);border-radius:11px}table{border-collapse:separate;border-spacing:0;min-width:940px;width:100%;font-variant-numeric:tabular-nums}th{text-align:left;position:sticky;top:0;background:#233851;color:#f5f9ff;font-size:11px;letter-spacing:.02em;z-index:1}td,th{padding:11px 12px;border-bottom:1px solid #30445d}tbody tr:nth-child(even){background:#1b2a40}tbody tr:hover{background:#244161}.risk{color:#ff9cab;font-weight:750}.ok{color:#61d6ae}.warn{color:#f2cd86}.badge{display:inline-block;padding:3px 7px;border-radius:6px;background:#2a3b50;color:#dbebf9;font-size:11px}.note{padding:13px 15px;background:#253343;border:1px solid var(--border);color:#d0deeb;border-radius:11px;margin-top:18px}footer{color:var(--muted);font-size:11px;border-top:1px solid var(--border);margin-top:35px;padding-top:17px}#reportSearch{min-width:195px}
.gang-scroll{max-height:590px;overflow-y:auto;padding-right:8px;scrollbar-color:#45617c #19293d}
.gang-scroll .hrow{grid-template-columns:minmax(125px,200px) 1fr 88px}
.sharepie-layout{display:grid;grid-template-columns:minmax(175px,230px) minmax(0,1fr);gap:14px;align-items:center}
.sharepie-svg{width:100%;height:auto;max-width:230px}
.sharepie-legend{max-height:285px;overflow-y:auto;display:flex;flex-direction:column;gap:7px}
.sharepie-legend .item{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--text);min-width:0}
.sharepie-legend .swatch{width:11px;height:11px;flex:none;border-radius:3px}
.sharepie-legend .item-label{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.sharepie-legend .item-val{flex:none;font-variant-numeric:tabular-nums;color:var(--muted)}
@media(max-width:650px){.sharepie-layout{grid-template-columns:1fr}.sharepie-svg{margin:0 auto}.gang-scroll .hrow{grid-template-columns:minmax(85px,130px) 1fr 58px}}
@media(max-width:980px){.cards{grid-template-columns:repeat(2,1fr)}.columns{grid-template-columns:1fr}.hrow{grid-template-columns:minmax(90px,150px) 1fr 40px}}@media(max-width:600px){main{padding:15px}h1{font-size:24px}header{flex-direction:column}.cards{grid-template-columns:repeat(2,minmax(0,1fr))}.card{padding:12px}.card .value{font-size:24px}.controlbar{gap:8px}.field{min-width:unset;flex:1 1 42%}.sectionhead{display:block}}
@media print{body{background:white;color:#152237}main{padding:10px}header,.panel,.card{break-inside:avoid;box-shadow:none}button,.controlbar{display:none}.panel,.card{background:white;color:#152237;border-color:#bec9d4}h2,h3,.card .value,.card .name{color:#152237}.smallnote,.muted,.hint{color:#46566b}}
</style></head>
<body><main>
<header><div><div class="eyebrow">Operational Intelligence / Board Reporting</div><h1>Workforce &amp; Operations</h1><div class="muted">Interactive executive report · generated __GENERATED__</div></div><div class="pill">INTERNAL · OFFLINE SNAPSHOT</div></header>
<div class="controlbar">
 <div class="field"><label for="team">WhatsApp group</label><select id="team"></select></div>
 <div class="field"><label for="period">Reporting dates</label><select id="period"><option value="all">All included dates</option><option value="7">Latest 7 included days</option><option value="14">Latest 14 included days</option><option value="30">Latest 30 included days</option><option value="custom">Custom range</option></select></div>
 <div class="field"><label for="from">From service / report date</label><input type="date" id="from"></div>
 <div class="field"><label for="to">To service / report date</label><input type="date" id="to"></div>
 <div class="field"><label for="reportSearch">Find a group / record</label><input type="search" id="reportSearch" placeholder="Search table rows"></div>
 <button id="reset">Reset filters</button><button id="download">Export filtered exceptions CSV</button>
</div>
<div class="scope">Exported scope: <b>__SCOPE__</b> · Dates in exception records are <b>night-shift service dates</b>, which may differ from the WhatsApp message timestamp. All charts use the embedded snapshot; this file does not update automatically.</div>
<div class="cards" id="cards"></div>
<div class="sectionhead"><div><div class="eyebrow">01 / Operational performance</div><h2>Staffing changes, no-shows &amp; shortages</h2><p>Independent event categories — totals are not unique employees.</p></div></div>
<div class="columns"><section class="panel"><h3>Four-category comparison · highest activity groups</h3><div id="legend" class="legend"></div><div id="exceptionBars"></div><div class="smallnote">A blank category may mean the value was not reported. Shortages are calculated per required role from final WhatsApp attendance and staffing plans.</div></section><section class="panel"><h3>Daily exception trend · service dates</h3><div id="exceptionTrend" class="svgwrap"></div><div class="smallnote">Daily counts use service dates. Unrecorded days are not represented as confirmed zero.</div></section></div>
<div class="columns" style="margin-top:13px"><section class="panel"><h3>Role-specific shortages · required vs present</h3><div id="roleBars"></div></section><section class="panel"><h3>Completeness &amp; reporting status</h3><div id="quality"></div></section></div>
<div class="sectionhead"><div><div class="eyebrow">02 / Source operations</div><h2>WhatsApp attendance &amp; safety</h2><p>Reported message values, not a deduplicated headcount of unique employees.</p></div></div>
<div class="columns"><section class="panel"><h3>Workforce role allocation · reported totals</h3><div id="attendanceBars"></div></section><section class="panel"><h3>Near-miss reporting trend · message dates</h3><div id="nearTrend" class="svgwrap"></div></section></div>
<div class="panel" style="margin-top:13px"><h3>Gang-wise attendance · stacked horizontal comparison</h3><div id="gangAttendanceLegend" class="legend"></div><div id="gangAttendance" class="gang-scroll"></div><p class="smallnote">Reported T002/T003, PWT, SPL and PL numbers by group. Late is an attendance indicator, not another staffing role; multiple message values are not deduplicated employees.</p></div>
<div class="columns" style="margin-top:13px"><section class="panel"><h3>Near misses percentage by group · pie chart</h3><div id="nearSharePie"></div><p class="smallnote">Share of reported near-miss values per group, not confirmed incident rates.</p></section><section class="panel"><h3>Workforce allocation percentage · pie chart</h3><div id="workforceSharePie"></div><p class="smallnote">Share of reported staffing-role values only; Late is excluded.</p></section></div>
<div class="sectionhead"><div><div class="eyebrow">03 / Evidence registers</div><h2>Staffing exceptions by group and night</h2><p id="exceptionsCount"></p></div></div>
<div class="panel"><div class="tablewrap"><table><thead><tr><th>Service date</th><th>Group</th><th>Before 15:00</th><th>After 15:00</th><th>No-shows</th><th>Required</th><th>Present</th><th>Shortage</th><th>Status</th></tr></thead><tbody id="exceptionsTable"></tbody></table></div><p class="smallnote">“—” means unknown or not submitted, rather than zero. Pending shortages must not be treated as confirmed staffing compliance.</p></div>
<div class="sectionhead"><div><h2>WhatsApp report evidence</h2><p id="reportsCount"></p></div></div>
<div class="panel"><div class="tablewrap"><table><thead><tr><th>Message date/time</th><th>Group</th><th>Sender</th><th>T002/T003</th><th>PWT</th><th>SPL</th><th>PL</th><th>Near miss</th><th>Late</th><th>No-shows reported</th></tr></thead><tbody id="reportsTable"></tbody></table></div></div>
<div class="note"><b>Interpretation:</b> Changes before / after 15:00 are operator-entered event counts. The WhatsApp message is the final actual attendance snapshot for a night shift. Changes and no-shows must not be subtracted again from that headcount. A no-show value is shown only when explicitly stated in WhatsApp. Excess staff in one role do not cancel shortages in another role. Unknowns stay unknown.</div>
<footer>Prepared from the locally stored Operational Intelligence database. This standalone HTML contains a point-in-time copy of report-level data and may contain internal operational information. Share only with authorised recipients. For the latest figures, re-export from the application.</footer>
</main>
<script>
'use strict';
const DATA = __PAYLOAD__;
const $ = id => document.getElementById(id);
const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = v => v === null || v === undefined ? '—' : Number(v).toLocaleString('en-GB');
const COLOR={before:'#4d9ff8',after:'#18c4ac',no_shows:'#a78bfa',shortage:'#fb7185'};
const SOURCES=[['before','Before 15:00'],['after','After 15:00'],['no_shows','No Shows'],['shortage','Shortages']];
const uniqueDates=[...new Set([...DATA.exceptions.map(x=>x.date),...DATA.reports.map(x=>x.day)])].sort();
const newest=uniqueDates[uniqueDates.length-1] || '';
const names=['All Groups',...new Set([...DATA.exceptions.map(x=>x.group),...DATA.reports.map(x=>x.group)])];
$('team').innerHTML=names.map(n=>'<option value="'+esc(n)+'">'+esc(n)+'</option>').join('');
function minusDays(iso, n){const d=new Date(iso+'T12:00:00Z');d.setUTCDate(d.getUTCDate()-n);return d.toISOString().slice(0,10)}
function matchesDay(d){let p=$('period').value;if(p==='custom')return (!$('from').value||d>=$('from').value)&&(!$('to').value||d<=$('to').value);if(p!=='all'&&newest)return d>=minusDays(newest,Number(p)-1)&&d<=newest;return true}
function filtered(){let t=$('team').value.toLocaleLowerCase('en-GB');return {e:DATA.exceptions.filter(x=>(t==='all groups'||x.group.toLocaleLowerCase('en-GB')===t)&&matchesDay(x.date)),r:DATA.reports.filter(x=>(t==='all groups'||x.group.toLocaleLowerCase('en-GB')===t)&&matchesDay(x.day))}}
function total(arr,k){return arr.reduce((s,x)=>s+Number(x[k]??0),0)}
function card(name,value,sub){return '<div class="card"><div class="name">'+esc(name)+'</div><div class="value">'+esc(value)+'</div><div class="sub">'+esc(sub)+'</div></div>'}
function bars(rows,metrics,max=12){if(!rows.length)return '<div class="empty">No records for the selected period.</div>';const w=rows.slice(0,max);const largest=Math.max(1,...w.map(x=>metrics.reduce((a,m)=>a+Number(x[m]??0),0)));return w.map(x=>{const sum=metrics.reduce((a,m)=>a+Number(x[m]??0),0);const seg=metrics.map(m=>{const v=Number(x[m]??0);return v?'<div class="segment" style="width:'+(v/largest*100)+'%;background:'+COLOR[m]+'" title="'+esc(m)+': '+v+'"></div>':''}).join('');return '<div class="hrow"><span class="hlabel" title="'+esc(x.group)+'">'+esc(x.group)+'</span><div class="track">'+seg+'</div><span class="hvalue">'+sum+'</span></div>'}).join('')}
function trend(rows,fields,daysLimit=14){if(!rows.length)return '<div class="empty">No observations to chart.</div>';let d=[...new Set(rows.map(x=>x.date))].sort().slice(-daysLimit);const vals=d.map(day=>{let r=rows.filter(x=>x.date===day);return fields.map(f=>r.reduce((s,x)=>s+Number(x[f]??0),0))});const peak=Math.max(1,...vals.flat());const W=560,H=244,left=38,top=15,bottom=48,plotH=H-top-bottom, groupW=(W-left-8)/d.length, barW=Math.max(2,Math.min(12,groupW/(fields.length+1)));let svg='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="Daily trends">';for(let k=0;k<=4;k++){let y=top+plotH*k/4;svg+='<line x1="'+left+'" y1="'+y+'" x2="'+(W-4)+'" y2="'+y+'" stroke="#395068" stroke-dasharray="3 3"/><text x="4" y="'+(y+4)+'" fill="#abc0d7" font-size="10">'+Math.round(peak*(4-k)/4)+'</text>'}d.forEach((day,i)=>{fields.forEach((f,j)=>{let n=vals[i][j],height=n/peak*plotH,x=left+i*groupW+4+j*barW,y=top+plotH-height;svg+='<rect x="'+x+'" y="'+y+'" width="'+(barW-1)+'" height="'+height+'" fill="'+COLOR[f]+'"><title>'+esc(day)+' '+esc(f)+': '+n+'</title></rect>'});if(d.length<18||i%2===0)svg+='<text transform="translate('+(left+i*groupW+5)+','+(H-bottom+13)+') rotate(45)" fill="#b6c8da" font-size="10">'+esc(day.slice(5))+'</text>'});return svg+'</svg>'}
function singleTrend(rows){let byday={};rows.forEach(r=>{byday[r.day]=(byday[r.day]||0)+Number(r.near_miss??0)});const d=Object.keys(byday).sort().slice(-20);if(!d.length)return '<div class="empty">No received reports.</div>';const max=Math.max(1,...d.map(x=>byday[x]));let svg='<svg viewBox="0 0 540 228" role="img" aria-label="Near miss values by date">';d.forEach((day,i)=>{let x=30+i*25,h=byday[day]/max*160;svg+='<rect x="'+x+'" y="'+(174-h)+'" width="16" height="'+h+'" rx="3" fill="#f3c66b"><title>'+esc(day)+' · '+byday[day]+' near miss values</title></rect>';if(i%2===0)svg+='<text transform="translate('+x+',183) rotate(45)" fill="#abc0d7" font-size="10">'+esc(day.slice(5))+'</text>'});return svg+'</svg>'}
// v3.0.0: offline Workforce/Safety chart parity. Uses the existing filtered reports.
const GANG_FIELDS=[['t002_t003','T002/T003','#2980b9'],['pwt','PWT','#2ecc71'],['spl','SPL','#e67e22'],['pl','PL','#e74c3c'],['late','Late','#f1c40f']];
const SHARE_COLORS=['#d35400','#2980b9','#27ae60','#8e44ad','#f39c12','#16a085','#c0392b','#597cbb','#ae7ac0','#b9a045','#7a9e49','#e18a71'];
function sharePieHTML(items, colors, title){
 const data=items.filter(x=>Number(x.value)>0).sort((a,b)=>b.value-a.value||a.name.localeCompare(b.name));
 const sum=data.reduce((acc,x)=>acc+Number(x.value),0);
 if(!sum)return '<div class="empty">No positive reported values in the selected period.</div>';
 const cx=120,cy=120,R=93;let a=-Math.PI/2;
 const shapes=data.map((x,i)=>{let fraction=Number(x.value)/sum,delta=2*Math.PI*fraction,color=colors[i%colors.length];
  let shape='';if(fraction>=.999999){shape='<circle cx="120" cy="120" r="93" fill="'+color+'"/>'}
  else{const b=a+delta,p1=[cx+R*Math.cos(a),cy+R*Math.sin(a)],p2=[cx+R*Math.cos(b),cy+R*Math.sin(b)];
   const d='M '+cx+' '+cy+' L '+p1[0].toFixed(3)+' '+p1[1].toFixed(3)+' A '+R+' '+R+' 0 '+(delta>Math.PI?1:0)+' 1 '+p2[0].toFixed(3)+' '+p2[1].toFixed(3)+' Z';shape='<path d="'+d+'" fill="'+color+'" stroke="#152338" stroke-width="1"/>'}
  a+=delta;return '<g>'+shape+'<title>'+esc(x.name)+' · '+x.value+' ('+(100*fraction).toFixed(1)+'%)</title></g>'
 }).join('');
 const legend=data.map((x,i)=>'<div class="item" title="'+esc(x.name)+'"><span class="swatch" style="background:'+colors[i%colors.length]+'"></span><span class="item-label">'+esc(x.name)+'</span><span class="item-val">'+(100*x.value/sum).toFixed(1)+'%</span></div>').join('');
 return '<div class="sharepie-layout"><svg class="sharepie-svg" viewBox="0 0 240 240" role="img" aria-label="'+esc(title)+'">'+shapes+'</svg><div class="sharepie-legend">'+legend+'</div></div>';
}
function gangAttendanceHTML(rows){
 const groups=new Map();rows.forEach(r=>{if(!groups.has(r.group))groups.set(r.group,{});let sums=groups.get(r.group);GANG_FIELDS.forEach(([key])=>sums[key]=(sums[key]||0)+Number(r[key]??0))});
 const entries=[...groups.entries()].sort((a,b)=>a[0].localeCompare(b[0]));
 if(!entries.length)return '<div class="empty">No WhatsApp attendance records for the selected period.</div>';
 const peak=Math.max(1,...entries.map(([g,vals])=>GANG_FIELDS.reduce((s,[key])=>s+Number(vals[key]||0),0)));
 return entries.map(([group,vals])=>{const staff=['t002_t003','pwt','spl','pl'].reduce((s,key)=>s+Number(vals[key]||0),0);
  const segs=GANG_FIELDS.map(([key,label,color])=>{const v=Number(vals[key]||0);return v?'<div class="segment" style="width:'+(v/peak*100)+'%;background:'+color+'" title="'+esc(group+' · '+label+': '+v)+'"></div>':''}).join('');
  return '<div class="hrow"><span class="hlabel" title="'+esc(group)+'">'+esc(group)+'</span><div class="track">'+segs+'</div><span class="hvalue">'+fmt(staff)+' staff</span></div>';
 }).join('');
}
function refresh(){const {e,r}=filtered();const knownNo=e.filter(x=>x.no_shows!==null),knownShort=e.filter(x=>x.shortage!==null);const groups=new Set([...e.map(x=>x.group),...r.map(x=>x.group)]);const pending=e.filter(x=>x.shortage===null).length;
$('cards').innerHTML=[card('WhatsApp reports',r.length,'Received messages in selected scope'),card('Groups represented',groups.size,'Across reports and exception entries'),card('Changes before 15:00',e.some(x=>x.before!==null)?fmt(total(e,'before')):'—',e.filter(x=>x.before!==null).length+' submitted entries'),card('Changes after 15:00',e.some(x=>x.after!==null)?fmt(total(e,'after')):'—',e.filter(x=>x.after!==null).length+' submitted entries'),card('Explicit no-shows',knownNo.length?fmt(total(knownNo,'no_shows')):'—',knownNo.length+' attendance snapshots with counts'),card('Staffing shortages',knownShort.length?fmt(total(knownShort,'shortage')):'—',knownShort.length+' calculated nights'),card('Nights awaiting review',pending,'No confirmed shortage until report/plan is valid'),card('Near-miss values',fmt(total(r,'near_miss')),'Unverified reported values')].join('');
$('legend').innerHTML=SOURCES.map(([k,n])=>'<span><b style="background:'+COLOR[k]+'"></b>'+esc(n)+'</span>').join('');
let group={};e.forEach(x=>{let g=group[x.group]||(group[x.group]={group:x.group,before:0,after:0,no_shows:0,shortage:0});SOURCES.forEach(([k])=>g[k]+=Number(x[k]??0))});let g=Object.values(group).sort((a,b)=>SOURCES.reduce((s,[k])=>s+b[k]-a[k],0)||a.group.localeCompare(b.group));$('exceptionBars').innerHTML=bars(g,SOURCES.map(s=>s[0]));
$('exceptionTrend').innerHTML=trend(e,SOURCES.map(s=>s[0]));
const roles=['T002/T003','PWT','SPL','PL'];let role=Object.fromEntries(roles.map(k=>[k,{group:k,required:0,present:0,shortage:0}]));knownShort.forEach(x=>x.roles.forEach(y=>{const v=role[y.role];if(v){v.required+=Number(y.required||0);v.present+=Number(y.present||0);v.shortage+=Number(y.shortage||0)}}));const peak=Math.max(1,...roles.map(x=>role[x].required));$('roleBars').innerHTML=knownShort.length?roles.map(k=>{const v=role[k];return '<div class="hrow"><span>'+esc(k)+'</span><div class="track"><div class="segment" style="width:'+(v.present/peak*100)+'%;background:#18c4ac"></div><div class="segment" style="width:'+(v.shortage/peak*100)+'%;background:#fb7185"></div></div><span class="hvalue">'+v.shortage+' short</span></div>'}).join('')+'<div class="smallnote">Teal = present; coral = shortage, against planned role requirements. Surplus in a role may extend the teal bar.</div>':'<div class="empty">No calculable staffing snapshots in this period.</div>';
$('quality').innerHTML='<p><b>'+knownShort.length+'</b> nights with verified staffing calculations</p><p><b>'+pending+'</b> nights with shortages pending</p><p><b>'+e.filter(x=>x.before!==null&&x.after!==null).length+'</b> nights with both change counts entered</p><p><b>'+knownNo.length+'</b> nights with explicit WhatsApp no-show counts</p><p class="smallnote">Unknown values are not interpreted as zero. Reported dates may differ from service dates for overnight work.</p>';
const attendance=roles.map((k,i)=>({group:k,n:total(r,['t002_t003','pwt','spl','pl'][i])}));const mx=Math.max(1,...attendance.map(x=>x.n));$('attendanceBars').innerHTML=attendance.map((x,i)=>'<div class="hrow"><span>'+esc(x.group)+'</span><div class="track"><div class="segment" style="width:'+(x.n/mx*100)+'%;background:'+['#4d9ff8','#18c4ac','#f3c66b','#a78bfa'][i]+'"></div></div><span class="hvalue">'+fmt(x.n)+'</span></div>').join('')+'<p class="smallnote">Sums of message values; do not interpret as unique people across multiple reports.</p>';
$('nearTrend').innerHTML=singleTrend(r);
$('gangAttendanceLegend').innerHTML=GANG_FIELDS.map(([key,label,color])=>'<span><b style="background:'+color+'"></b>'+esc(label)+'</span>').join('');
$('gangAttendance').innerHTML=gangAttendanceHTML(r);
const nearGroups=new Map();r.forEach(x=>nearGroups.set(x.group,(nearGroups.get(x.group)||0)+Number(x.near_miss??0)));
$('nearSharePie').innerHTML=sharePieHTML([...nearGroups].map(([name,value])=>({name,value})),SHARE_COLORS,'Reported near-miss percentages by group');
$('workforceSharePie').innerHTML=sharePieHTML([['t002_t003','T002/T003'],['pwt','PWT'],['spl','SPL'],['pl','PL']].map(([key,name])=>({name,value:total(r,key)})),['#4d9ff8','#18c4ac','#f3c66b','#a78bfa'],'Workforce allocation percentage by reported role');

const q=$('reportSearch').value.toLocaleLowerCase('en-GB');const ex=e.filter(x=>!q||(x.group+' '+x.date+' '+x.status).toLocaleLowerCase('en-GB').includes(q));const reports=r.filter(x=>!q||(x.group+' '+x.day+' '+x.sender).toLocaleLowerCase('en-GB').includes(q));
$('exceptionsCount').textContent=ex.length+' of '+e.length+' entries displayed. Dates follow the shift service date.';$('reportsCount').textContent=reports.length+' of '+r.length+' reports displayed.';
$('exceptionsTable').innerHTML=ex.map(x=>'<tr><td>'+esc(x.date)+'</td><td>'+esc(x.group)+'</td><td>'+fmt(x.before)+'</td><td>'+fmt(x.after)+'</td><td>'+fmt(x.no_shows)+'</td><td>'+fmt(x.required)+'</td><td>'+fmt(x.available)+'</td><td class="'+(x.shortage>0?'risk':x.shortage===0?'ok':'warn')+'">'+fmt(x.shortage)+'</td><td><span class="badge" title="'+esc(x.reason)+'">'+esc(x.status)+'</span></td></tr>').join('')||'<tr><td colspan="9">No matching exceptions</td></tr>';
$('reportsTable').innerHTML=reports.map(x=>'<tr><td>'+esc(x.when)+'</td><td>'+esc(x.group)+'</td><td>'+esc(x.sender)+'</td>'+['t002_t003','pwt','spl','pl','near_miss','late','no_shows'].map(k=>'<td>'+fmt(x[k])+'</td>').join('')+'</tr>').join('')||'<tr><td colspan="10">No matching reports</td></tr>';
window.__reportRows={ex,reports};
}
['team','period','from','to'].forEach(id=>$(id).addEventListener('change',()=>{if(id==='from'||id==='to')$('period').value='custom';refresh()}));$('reportSearch').addEventListener('input',refresh);
$('reset').onclick=()=>{$('team').value='All Groups';$('period').value='all';$('from').value='';$('to').value='';$('reportSearch').value='';refresh()};
$('download').onclick=()=>{const cols=['date','group','before','after','no_shows','required','available','shortage','status','reason'];let rows=window.__reportRows?.ex||[];const csv=[cols.join(','),...rows.map(row=>cols.map(k=>'"'+String(row[k]??'').replace(/"/g,'""')+'"').join(','))].join('\r\n');const link=document.createElement('a');link.href=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));link.download='staffing_exceptions_filtered.csv';link.click();setTimeout(()=>URL.revokeObjectURL(link.href),3000)};
refresh();
</script></body></html>
"""

def demo_data(days=42,seed=42):
    rng=random.Random(seed);n=0
    for i in range(days):
        d=date.today()-timedelta(days=days-i-1)
        for g in configuration()['groups']:
            for shift in (0,1):
                vals={'T002/T003':rng.randint(5,12),'PWT':rng.randint(2,6),'SPL':rng.randint(1,4),
                      'PL':rng.randint(0,2),'Near Miss':int(rng.random()<0.04), 'Late':rng.randint(0,2)}
                msg=' '.join(f'{k}: {v}' for k,v in vals.items())
                dt=datetime.combine(d,datetime.min.time()).replace(hour=7+shift*8,minute=rng.randint(0,40))
                n+=insert_report(g,'DEMO Generator',msg,dt,vals,origin='demo')
    audit('DEMO_SEED',f'{n} new labelled synthetic records')
    return n

init_db()


# --- Staffing exceptions, planning and local account management (v3.0.0) ---
# ---------------------------------------------------------------------------
# OPERATIONAL EXCEPTIONS / PLANNED STAFFING (native Python + SQLite)
# Added to the original notebook immediately after the core database cell.
# Excel input is read-only ZIP/XML; hidden worksheets and macros are ignored.
# ---------------------------------------------------------------------------
import hashlib as _ex_hashlib
import hmac as _ex_hmac
import json as _ex_json
import re as _ex_re
import secrets as _ex_secrets
import zipfile as _ex_zipfile
import xml.etree.ElementTree as _ex_et
from datetime import datetime as _ex_datetime, timedelta as _ex_timedelta
from pathlib import Path as _ex_Path

EX_ROLES = ('before',)  # Reuse Operator 1 account; historical Operator 2 is retained read-only.
EX_SHIFTS = ('Shift 1', 'Shift 2', 'Shift 3')
EX_BASIS = ('pre_adjustments', 'already_actual')
EX_METRIC_FIELDS = {'T002/T003': 't002_t003', 'PWT': 'pwt', 'SPL': 'spl', 'PL': 'pl'}
EX_WORKBOOK_NS = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
                  'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def init_exception_db():
    """Independent tables; leaves existing WhatsApp reports and its fingerprint alone."""
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS staffing_plans (
          group_name TEXT NOT NULL, worksheet TEXT NOT NULL,
          effective_from TEXT NOT NULL, effective_to TEXT NOT NULL,
          components_json TEXT NOT NULL, planned_total INTEGER NOT NULL,
          workbook_name TEXT NOT NULL, source_cell TEXT NOT NULL,
          imported_at TEXT NOT NULL,
          PRIMARY KEY (group_name, worksheet, effective_from, effective_to)
        );
        CREATE INDEX IF NOT EXISTS idx_staffing_plans_dates
          ON staffing_plans(group_name, effective_from, effective_to);

        CREATE TABLE IF NOT EXISTS manual_staffing_plans (
          group_name TEXT NOT NULL COLLATE NOCASE,
          effective_from TEXT NOT NULL,
          components_json TEXT NOT NULL,
          planned_total INTEGER NOT NULL CHECK(planned_total > 0),
          updated_at TEXT NOT NULL,
          updated_by TEXT NOT NULL,
          PRIMARY KEY (group_name,effective_from)
        );
        CREATE INDEX IF NOT EXISTS idx_manual_staffing_effective
          ON manual_staffing_plans(group_name,effective_from);
        CREATE TABLE IF NOT EXISTS manual_staffing_plan_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          changed_at TEXT NOT NULL,
          actor TEXT NOT NULL,
          operation TEXT NOT NULL,
          group_name TEXT NOT NULL,
          effective_from TEXT NOT NULL,
          old_state TEXT,
          new_state TEXT
        );
        CREATE TABLE IF NOT EXISTS exception_users (
          role TEXT PRIMARY KEY CHECK(role IN ('before','after')),
          display_name TEXT NOT NULL,
          password_salt TEXT NOT NULL, password_hash TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS app_users (
          username TEXT PRIMARY KEY COLLATE NOCASE,
          display_name TEXT NOT NULL,
          account_role TEXT NOT NULL CHECK(account_role IN ('admin','operator')),
          password_salt TEXT NOT NULL, password_hash TEXT NOT NULL,
          recovery_salt TEXT NOT NULL, recovery_hash TEXT,
          failed_attempts INTEGER NOT NULL DEFAULT 0,
          locked_until TEXT,
          recovery_used INTEGER NOT NULL DEFAULT 0,
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_login TEXT
        );
        CREATE TABLE IF NOT EXISTS exception_entries (
          group_name TEXT NOT NULL, service_date TEXT NOT NULL,
          shift_label TEXT NOT NULL CHECK(shift_label IN ('Shift 1','Shift 2','Shift 3')),
          before_count INTEGER CHECK(before_count >= 0),
          before_impact INTEGER,
          after_count INTEGER CHECK(after_count >= 0),
          after_impact INTEGER,
          no_shows INTEGER CHECK(no_shows >= 0),
          basis TEXT NOT NULL DEFAULT 'already_actual'
              CHECK(basis IN ('pre_adjustments','already_actual')),
          report_id INTEGER,
          before_by TEXT, before_at TEXT, after_by TEXT, after_at TEXT,
          revision INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL,
          PRIMARY KEY (group_name, service_date, shift_label)
        );
        CREATE INDEX IF NOT EXISTS idx_exception_date ON exception_entries(service_date);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_exception_snapshot_unique
          ON exception_entries(report_id) WHERE report_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS exception_sync_state (
          id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0
        );
        INSERT OR IGNORE INTO exception_sync_state(id,revision) VALUES('plans',0);
        CREATE TABLE IF NOT EXISTS exception_entry_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          changed_at TEXT NOT NULL, group_name TEXT NOT NULL,
          service_date TEXT NOT NULL, shift_label TEXT NOT NULL,
          actor_role TEXT NOT NULL, actor_name TEXT NOT NULL,
          old_state TEXT NOT NULL, new_state TEXT NOT NULL
        );
        ''')


def exception_users_ready():
    # Legacy Operator 1 login becomes the sole change-entry login.
    with connect() as c:
        return c.execute("SELECT 1 FROM exception_users WHERE role='before'").fetchone() is not None


def configure_exception_users(name, pin):
    # Create one operator login; preserve legacy accounts and history.
    name, pin = str(name).strip(), str(pin)
    if not name or len(name) > 100:
        raise ValueError('Enter an operator name (maximum 100 characters).')
    if not 6 <= len(pin) <= 128:
        raise ValueError('Use a passcode of 6 to 128 characters.')
    salt = _ex_secrets.token_bytes(16)
    digest = _ex_hashlib.pbkdf2_hmac('sha256', pin.encode('utf-8'), salt, 230000)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("SELECT 1 FROM exception_users WHERE role='before'").fetchone():
            raise ValueError('Change-entry login already exists. Use its existing passcode.')
        c.execute('''INSERT INTO exception_users(role,display_name,password_salt,password_hash,created_at)
          VALUES('before',?,?,?,?)''',(name,salt.hex(),digest.hex(),stamp()))
    audit('CHANGE_OPERATOR_SETUP', 'Single changes operator account configured')


def authenticate_exception_user(role, pin):
    if role not in EX_ROLES:
        raise ValueError('Unknown operator role.')
    with connect() as c:
        row = c.execute('SELECT * FROM exception_users WHERE role=?', (role,)).fetchone()
    if row is None:
        raise PermissionError('The exception operator accounts are not configured.')
    digest = _ex_hashlib.pbkdf2_hmac('sha256', str(pin).encode('utf-8'),
                                     bytes.fromhex(row['password_salt']), 230000)
    if not _ex_hmac.compare_digest(digest, bytes.fromhex(row['password_hash'])):
        raise PermissionError('Incorrect passcode.')
    return row['display_name']


# ===========================================================================
# Local multi-user exception entry (does not require an external API).
# One-time recovery codes are revealed only at account creation/rotation.
# This is a local-workstation access control, not a remote authentication server.
# ===========================================================================
from datetime import timezone as _ex_timezone

_PASSWORD_ITERATIONS = 260000
# Volatile per-process sessions: tokens are never stored in SQLite or settings.json.
_APP_SESSIONS = {}
_SESSION_IDLE_MINUTES = 12 * 60


def app_session_info(token):
    info = _APP_SESSIONS.get(str(token))
    if not info:
        raise PermissionError('Your session has expired. Sign in again.')
    now = _ex_datetime.now(_ex_timezone.utc)
    if (now-info['last_used']).total_seconds() > _SESSION_IDLE_MINUTES*60:
        _APP_SESSIONS.pop(str(token),None)
        raise PermissionError('Session expired. Sign in again.')
    with connect() as c:
        row = c.execute('SELECT account_role,enabled,display_name FROM app_users WHERE username=?',
                        (info['username'],)).fetchone()
    if not row or not row['enabled']:
        _APP_SESSIONS.pop(str(token),None)
        raise PermissionError('Account disabled or unavailable. Sign in again.')
    info['last_used'] = now
    return {'username':info['username'],'display_name':row['display_name'],
            'role':row['account_role'],'token':str(token)}


def revoke_app_session(token):
    _APP_SESSIONS.pop(str(token),None)


def _revoke_user_sessions(username):
    for key,item in list(_APP_SESSIONS.items()):
        if item['username'].casefold()==str(username).casefold():
            _APP_SESSIONS.pop(key,None)

_USERNAME_PATTERN = _ex_re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,31}\Z")


def _validate_username(name):
    name = str(name).strip()
    if not _USERNAME_PATTERN.fullmatch(name):
        raise ValueError('Username must be 3–32 characters: letters, numbers, dots, dashes or underscores.')
    return name


def _validate_password(password):
    password = str(password)
    if len(password) < 12 or len(password) > 128:
        raise ValueError('Password must be 12–128 characters long.')
    if password.strip() != password:
        raise ValueError('Password cannot begin or end with spaces.')
    return password


def _pbkdf2(value, salt):
    return _ex_hashlib.pbkdf2_hmac('sha256', value.encode('utf-8'), salt, _PASSWORD_ITERATIONS).hex()


def _new_credential(value):
    salt = _ex_secrets.token_bytes(16)
    return salt.hex(), _pbkdf2(value, salt)


def _new_recovery():
    code = _ex_secrets.token_hex(20)  # 160-bit random, kept out of logs.
    salt, digest = _new_credential(code)
    return code, salt, digest


def _normalise_recovery(code):
    return _ex_re.sub(r'[\s-]+','',str(code)).lower()


def app_users_ready():
    with connect() as c:
        return bool(c.execute('SELECT 1 FROM app_users LIMIT 1').fetchone())


def legacy_operator_migration_required():
    with connect() as c:
        old = c.execute("SELECT 1 FROM exception_users WHERE role='before'").fetchone()
        new = c.execute('SELECT 1 FROM app_users LIMIT 1').fetchone()
    return bool(old and not new)


def _make_app_user(c, username, display_name, role, password):
    username = _validate_username(username)
    display_name = str(display_name).strip()
    if not display_name or len(display_name)>100:
        raise ValueError('Enter a display name up to 100 characters.')
    if role not in ('admin','operator'):
        raise ValueError('Account role must be administrator or operator.')
    password = _validate_password(password)
    ps, ph = _new_credential(password)
    code, rs, rh = _new_recovery()
    now = stamp()
    c.execute('''INSERT INTO app_users
      (username,display_name,account_role,password_salt,password_hash,
       recovery_salt,recovery_hash,created_at,updated_at)
       VALUES(?,?,?,?,?,?,?,?,?)''',
       (username,display_name,role,ps,ph,rs,rh,now,now))
    return code


def bootstrap_admin_account(username, display_name, password, legacy_passcode=None):
    '''First-run administration; legacy Operator 1 passcode is required on upgrade.'''
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('SELECT 1 FROM app_users LIMIT 1').fetchone():
            raise PermissionError('An administrator already exists. Please sign in.')
        legacy = c.execute("SELECT * FROM exception_users WHERE role='before'").fetchone()
        if legacy:
            digest = _ex_hashlib.pbkdf2_hmac('sha256', str(legacy_passcode or '').encode('utf-8'),
                                     bytes.fromhex(legacy['password_salt']),230000)
            if not _ex_hmac.compare_digest(digest,bytes.fromhex(legacy['password_hash'])):
                raise PermissionError('The existing Operator 1 passcode is required to upgrade accounts.')
        code = _make_app_user(c,username,display_name,'admin',password)
    audit('APP_ADMIN_BOOTSTRAP','Administrator account created; credentials not logged')
    return code


def _require_admin(c, actor):
    username = app_session_info(actor)['username']
    current = c.execute('SELECT account_role,enabled FROM app_users WHERE username=?', (username,)).fetchone()
    if not current or not current['enabled'] or current['account_role']!='admin':
        raise PermissionError('An administrator account is required.')


def list_app_users():
    with connect() as c:
        return [dict(x) for x in c.execute('''SELECT username,display_name,account_role,enabled,
             last_login,created_at FROM app_users ORDER BY account_role,username COLLATE NOCASE''')]


def create_app_user(actor, username, display_name, role, password):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        _require_admin(c,actor)
        code = _make_app_user(c,username,display_name,role,password)
    audit('APP_USER_CREATED',f'{app_session_info(actor)["username"]} added {username} ({role})')
    return code


def authenticate_app_user(username,password):
    name = str(username).strip()
    now = _ex_datetime.now(_ex_timezone.utc)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row = c.execute('SELECT * FROM app_users WHERE username=?', (name,)).fetchone()
        if not row:
            # Similar computational work for an unknown username.
            _pbkdf2(str(password),bytes(16))
            raise PermissionError('Invalid username or password.')
        if not row['enabled']:
            raise PermissionError('Account disabled. Contact an administrator.')
        if row['locked_until']:
            try: locked = _ex_datetime.fromisoformat(row['locked_until'])
            except ValueError: locked = now
            if locked>now:
                raise PermissionError('Too many sign-in attempts. Try again in 15 minutes or use recovery.')
        actual = _pbkdf2(str(password),bytes.fromhex(row['password_salt']))
        if not _ex_hmac.compare_digest(actual,row['password_hash']):
            attempts=int(row['failed_attempts'])+1
            until=(now+_ex_timedelta(minutes=15)).isoformat() if attempts>=5 else None
            c.execute('UPDATE app_users SET failed_attempts=?,locked_until=? WHERE username=?',
                      (attempts,until,name))
            raise_permission=True
        else:
            c.execute('''UPDATE app_users SET failed_attempts=0,locked_until=NULL,
                      last_login=? WHERE username=?''',(now.isoformat(),name))
            raise_permission=False
            session={'username':row['username'],'display_name':row['display_name'],
                     'role':row['account_role']}
    if raise_permission:
        raise PermissionError('Invalid username or password.')
    token=_ex_secrets.token_urlsafe(32)
    _APP_SESSIONS[token]={'username':session['username'],'last_used':now}
    session['token']=token
    audit('APP_USER_SIGN_IN',f"{session['username']} signed in to Exceptions")
    return session


def recover_app_password(username, recovery_code, new_password):
    '''Single-use, offline recovery. A successful reset rotates the recovery code.'''
    new_password=_validate_password(new_password)
    code=_normalise_recovery(recovery_code)
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM app_users WHERE username=?',(str(username).strip(),)).fetchone()
        if not row or not row['enabled'] or not row['recovery_hash'] or row['recovery_used']:
            raise PermissionError('Recovery details are invalid. Contact an administrator.')
        digest=_pbkdf2(code,bytes.fromhex(row['recovery_salt']))
        if not _ex_hmac.compare_digest(digest,row['recovery_hash']):
            raise PermissionError('Recovery details are invalid. Contact an administrator.')
        ps,ph=_new_credential(new_password)
        next_code,rs,rh=_new_recovery()
        c.execute('''UPDATE app_users SET password_salt=?,password_hash=?,
            recovery_salt=?,recovery_hash=?,recovery_used=0,
            failed_attempts=0,locked_until=NULL,updated_at=? WHERE username=?''',
            (ps,ph,rs,rh,stamp(),row['username']))
    _revoke_user_sessions(row['username'])
    audit('APP_PASSWORD_RECOVERED',f"Offline recovery used for {row['username']}")
    return next_code


def rotate_user_recovery_code(actor, username):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        _require_admin(c,actor)
        row=c.execute('SELECT username FROM app_users WHERE username=?',(username,)).fetchone()
        if not row:raise ValueError('Unknown user account.')
        code,rs,rh=_new_recovery()
        c.execute('''UPDATE app_users SET recovery_salt=?,recovery_hash=?,
                    recovery_used=0,updated_at=? WHERE username=?''',
                    (rs,rh,stamp(),row['username']))
    audit('APP_RECOVERY_ROTATED',f'{app_session_info(actor)["username"]} regenerated recovery for {username}')
    return code


def set_app_user_enabled(actor, username, enabled):
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        _require_admin(c,actor)
        row=c.execute('SELECT username,account_role,enabled FROM app_users WHERE username=?',(username,)).fetchone()
        if not row:raise ValueError('Unknown account.')
        if row['username'].casefold()==str(actor).casefold() and not enabled:
            raise ValueError('You cannot disable your own administrator account.')
        if not enabled and row['account_role']=='admin':
            admins=c.execute("SELECT COUNT(*) FROM app_users WHERE enabled=1 AND account_role='admin'").fetchone()[0]
            if admins<=1:raise ValueError('At least one enabled administrator must remain.')
        c.execute('UPDATE app_users SET enabled=?,updated_at=? WHERE username=?',
            (1 if enabled else 0,stamp(),row['username']))
    if not enabled:_revoke_user_sessions(username)
    audit('APP_USER_STATUS',f"{app_session_info(actor)['username']} {'enabled' if enabled else 'disabled'} {username}")


def link_final_attendance_as_user(session_token, group, service_date, report_id, expected_revision):
    principal=app_session_info(session_token)
    return link_final_attendance(principal['username'],group,service_date,
                                 'Shift 1',expected_revision,report_id)


def bulk_save_change_counts(actor, service_date, updates):
    '''Atomic multi-group changes. updates: [{group,revision,before?,after?}, ...].

    Unspecified stages remain NULL/unmodified; explicit zero means submitted zero.
    Any stale revision or invalid count aborts the *entire* batch.
    '''
    actor=app_session_info(actor)['username']
    day=parse_date(service_date).isoformat()
    active={x['name'].casefold() for x in group_registry() if x['enabled']}
    if not isinstance(updates,list) or len(updates)>1000:
        raise ValueError('Submit a list of up to 1000 group updates.')
    checked=[];seen=set()
    for item in updates:
        group=str(item.get('group','')).strip()
        if group.casefold() in seen:raise ValueError('Duplicate group in batch: '+group)
        seen.add(group.casefold())
        if group.casefold() not in active:raise ValueError('Inactive or unknown group: '+group)
        revision=item.get('revision')
        if type(revision) is not int or revision<0:raise ValueError('Invalid revision for '+group)
        fields={}
        for stage in ('before','after'):
            if stage in item:
                fields[stage]=_ex_validate_nonneg(item[stage],stage+' 15:00')
        if fields:checked.append((group,revision,fields))
    if not checked:return 0
    now=stamp()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        changes=[]
        for group,expected,fields in checked:
            previous_row=c.execute('''SELECT * FROM exception_entries
                WHERE group_name=? COLLATE NOCASE AND service_date=? AND shift_label='Shift 1' ''',
                (group,day)).fetchone()
            previous=dict(previous_row) if previous_row else {}
            current_revision=previous.get('revision',0)
            if expected!=current_revision:
                raise RuntimeError('Another user has changed '+group+' since you loaded it. Reload before saving.')
            next_fields={}
            for stage,value in fields.items():
                col=stage+'_count'
                if previous.get(col)!=value:
                    next_fields[col]=value
                    next_fields[stage+'_by']=actor
                    next_fields[stage+'_at']=now
            if not next_fields:continue
            if not previous_row:
                c.execute('''INSERT INTO exception_entries(group_name,service_date,shift_label,updated_at)
                     VALUES(?,?,'Shift 1',?)''',(group,day,now))
            assignments=','.join(k+'=?' for k in next_fields)
            values=list(next_fields.values())+[now,group,day,current_revision]
            updated=c.execute('''UPDATE exception_entries SET '''+assignments+''',
                basis='already_actual',updated_at=?,revision=revision+1
                WHERE group_name=? COLLATE NOCASE AND service_date=? AND shift_label='Shift 1'
                AND revision=?''', values)
            if updated.rowcount != 1:
                raise RuntimeError('Concurrent edit detected for '+group)
            new=dict(c.execute('''SELECT * FROM exception_entries WHERE
                group_name=? COLLATE NOCASE AND service_date=? AND shift_label='Shift 1' ''',
                (group,day)).fetchone())
            c.execute('''INSERT INTO exception_entry_audit
              (changed_at,group_name,service_date,shift_label,actor_role,actor_name,old_state,new_state)
              VALUES(?,?,?,?,?,?,?,?)''',
              (now,group,day,'Shift 1','batch_changes',actor,
               _ex_json.dumps(previous,sort_keys=True),_ex_json.dumps(new,sort_keys=True)))
            changes.append(group)
    audit('CHANGES_BATCH_SAVED',f'{actor} submitted {len(changes)} group(s) for {day}')
    return len(changes)


def _ex_xlsx_rows(workbook_path):
    """Read visible worksheet cell values without loading/executing VBA macros."""
    from posixpath import normpath as _normpath
    path = _ex_Path(workbook_path)
    if path.suffix.lower() not in ('.xlsm', '.xlsx'):
        raise ValueError('Choose a .xlsm or .xlsx staffing tracker.')
    with _ex_zipfile.ZipFile(path) as z:
        all_names = set(z.namelist())
        shared = []
        if 'xl/sharedStrings.xml' in all_names:
            root = _ex_et.fromstring(z.read('xl/sharedStrings.xml'))
            shared = [''.join(n.text or '' for n in item.iter()
                              if n.tag.endswith('}t'))
                      for item in root.findall('m:si', EX_WORKBOOK_NS)]
        workbook = _ex_et.fromstring(z.read('xl/workbook.xml'))
        relroot = _ex_et.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        relmap = {n.attrib['Id']: n.attrib['Target'] for n in relroot}
        for sh in workbook.findall('m:sheets/m:sheet', EX_WORKBOOK_NS):
            if sh.attrib.get('state', 'visible') != 'visible':
                continue
            name = sh.attrib['name']
            target = relmap[sh.attrib['{' + EX_WORKBOOK_NS['r'] + '}id']]
            xml_path = _normpath(target.lstrip('/') if target.startswith('/xl/') else
                                 ('xl/' + target) if not target.startswith('xl/') else target)
            if xml_path not in all_names:
                continue
            root = _ex_et.fromstring(z.read(xml_path))
            rows = {}
            for item in root.findall('.//m:sheetData/m:row/m:c', EX_WORKBOOK_NS):
                match = _ex_re.match(r'^([A-Z]+)(\d+)$', item.attrib.get('r', ''))
                if not match:
                    continue
                col, r = match.group(1), int(match.group(2))
                v = item.find('m:v', EX_WORKBOOK_NS)
                inline = item.find('m:is', EX_WORKBOOK_NS)
                value = v.text if v is not None else None
                if item.attrib.get('t') == 's' and value is not None:
                    value = shared[int(value)]
                elif item.attrib.get('t') == 'inlineStr' and inline is not None:
                    value = ''.join(inline.itertext())
                if value is not None:
                    rows.setdefault(r, {})[col] = value
            yield name, rows


def _ex_col_num(col):
    n = 0
    for c in col:
        n = n * 26 + (ord(c) - 64)
    return n


def _ex_as_count(val):
    try:
        numeric = float(val)
        return int(numeric) if numeric.is_integer() and 0 <= numeric <= 100000 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _ex_date_from_serial(val):
    try:
        v = float(val)
        if 35000 <= v < 90000:
            return (_ex_datetime(1899, 12, 30) + _ex_timedelta(days=v)).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _ex_normalise_component(title):
    label = ' '.join(str(title or '').strip().upper().split())
    if 'T002' in label and 'T003' in label:
        return 'T002/T003'
    if label == 'PWT':
        return 'PWT'
    if label in ("SPL'S", 'SPL', 'SPLS'):
        return 'SPL'
    if label == 'PL':
        return 'PL'
    return label


def inspect_visible_staffing_plans(workbook_path):
    """Read planned staffing ONLY from numeric C cells of each visible PLANNED block.

    Each worksheet block has the gang title in column A, a 'PLANNED' header,
    C='SHIFT', and labelled staffing component rows in B/C. Its daily headers
    establish the dates for which the baseline is applicable. Blank C means
    unknown, NEVER zero; hidden worksheets are always ignored.
    """
    result, warnings = [], []
    for sheet, rows in _ex_xlsx_rows(workbook_path):
        for r in sorted(rows):
            header = rows[r]
            if str(header.get('A') or '').strip().upper() != 'PLANNED' or str(header.get('C') or '').strip().upper() != 'SHIFT':
                continue
            group = str(rows.get(r - 2, {}).get('A') or '').strip()
            if not group:
                warnings.append(f'{sheet}!A{r-2}: missing gang name')
                continue
            dates = []
            for col, val in header.items():
                if col not in ('A', 'B', 'C') and 4 <= _ex_col_num(col) <= 37:
                    day = _ex_date_from_serial(val)
                    if day:
                        dates.append(day)
            if not dates:
                warnings.append(f'{sheet}!D{r}: no dated shift columns')
                continue
            parts = {}
            source_cells = []
            for rr in range(r + 1, r + 12):
                line = rows.get(rr, {})
                raw_label = str(line.get('B') or '').strip()
                if not raw_label or any(key in raw_label.casefold() for key in
                                        ('change', 'no show', 'shortage', 'late', 'near miss')):
                    continue
                val = _ex_as_count(line.get('C'))
                if val is None:
                    continue
                name = _ex_normalise_component(raw_label)
                parts[name] = parts.get(name, 0) + val
                source_cells.append(f'C{rr}')
            if not parts:
                # A blank planned team is not a zero-staffing plan.
                continue
            result.append(dict(group_name=group, worksheet=sheet,
                               effective_from=min(dates), effective_to=max(dates),
                               components=parts, planned_total=sum(parts.values()),
                               source_cell=', '.join(source_cells)))
    return result, warnings


def import_visible_staffing_plans(workbook_path):
    plans, warnings = inspect_visible_staffing_plans(workbook_path)
    if not plans:
        raise ValueError('No visible worksheets with dated planned staffing in column C were found.')
    now = stamp()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        for p in plans:
            c.execute('''INSERT INTO staffing_plans
                (group_name,worksheet,effective_from,effective_to,components_json,
                 planned_total,workbook_name,source_cell,imported_at)
                 VALUES(?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(group_name,worksheet,effective_from,effective_to)
                 DO UPDATE SET components_json=excluded.components_json,
                   planned_total=excluded.planned_total,
                   workbook_name=excluded.workbook_name,
                   source_cell=excluded.source_cell,
                   imported_at=excluded.imported_at''',
                  (p['group_name'], p['worksheet'], p['effective_from'],
                   p['effective_to'], _ex_json.dumps(p['components'], sort_keys=True),
                   p['planned_total'], _ex_Path(workbook_path).name, p['source_cell'], now))
    audit('VISIBLE_PLANS_IMPORTED',
          f'{_ex_Path(workbook_path).name}: {len(plans)} nonblank plans, {len(warnings)} notices')
    return plans, warnings



def manual_plan_versions(group):
    """Versioned, locally configured requirements for one WhatsApp group."""
    with connect() as c:
        rows = c.execute('''SELECT * FROM manual_staffing_plans
            WHERE group_name=? COLLATE NOCASE ORDER BY effective_from DESC''', (group,)).fetchall()
    return [{**dict(row), 'components': _ex_json.loads(row['components_json'])} for row in rows]


def save_manual_staffing_plan(group, effective_from, components, actor='Settings'):
    """Save a date-effective, auditable plan; zero means a role is not required.

    A newer start date supersedes the preceding plan but does not rewrite
    historical staffing requirements. Workbook plans remain a dated fallback.
    """
    group = validated_group_title(group)
    registered = {r['name'].casefold() for r in group_registry()}
    if group.casefold() not in registered:
        raise ValueError('Save this WhatsApp group in Settings before configuring its staffing plan.')
    day = parse_date(effective_from).isoformat()
    if set(components) != set(EX_METRIC_FIELDS):
        raise ValueError('Provide exactly T002/T003, PWT, SPL and PL requirements.')
    clean = {}
    for role in EX_METRIC_FIELDS:
        raw = components[role]
        if isinstance(raw, bool) or (not isinstance(raw, int) and
            (not isinstance(raw, str) or not raw.strip().isascii() or not raw.strip().isdecimal())):
            raise ValueError(f'{role}: enter a whole number between 0 and 10000.')
        amount = int(raw)
        if amount < 0 or amount > 10000:
            raise ValueError(f'{role}: enter a whole number between 0 and 10000.')
        clean[role] = amount
    total = sum(clean.values())
    if total == 0:
        raise ValueError('The required total must be above zero. Use 0 for individual roles that are not required.')
    now = stamp()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('''SELECT * FROM manual_staffing_plans
            WHERE group_name=? COLLATE NOCASE AND effective_from=?''', (group,day)).fetchone()
        old_json = _ex_json.dumps(dict(old), sort_keys=True) if old else None
        new_json = _ex_json.dumps(clean, sort_keys=True)
        if old:
            c.execute('''UPDATE manual_staffing_plans
              SET components_json=?, planned_total=?, updated_at=?, updated_by=?
              WHERE group_name=? COLLATE NOCASE AND effective_from=?''',
              (new_json,total,now,str(actor),group,day))
        else:
            c.execute('''INSERT INTO manual_staffing_plans
              (group_name,effective_from,components_json,planned_total,updated_at,updated_by)
              VALUES(?,?,?,?,?,?)''',(group,day,new_json,total,now,str(actor)))
        c.execute('''INSERT INTO manual_staffing_plan_audit
          (changed_at,actor,operation,group_name,effective_from,old_state,new_state)
          VALUES(?,?,?,?,?,?,?)''',
          (now,str(actor),'UPDATED' if old else 'CREATED',group,day,old_json,new_json))
        c.execute("UPDATE exception_sync_state SET revision=revision+1 WHERE id='plans'")
    audit('MANUAL_PLAN_SAVED', f'{group} {day}: {total} planned (by {actor})')
    return {'group_name':group,'effective_from':day,'components':clean,'planned_total':total}


def delete_manual_staffing_plan(group, effective_from, actor='Settings'):
    """Delete only the exact manual version selected, never imported Excel plans."""
    day = parse_date(effective_from).isoformat()
    now = stamp()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute('''SELECT * FROM manual_staffing_plans
            WHERE group_name=? COLLATE NOCASE AND effective_from=?''',(group,day)).fetchone()
        if old is None:
            raise ValueError('No manually saved plan starts on the selected date.')
        c.execute('''DELETE FROM manual_staffing_plans
            WHERE group_name=? COLLATE NOCASE AND effective_from=?''',(group,day))
        c.execute('''INSERT INTO manual_staffing_plan_audit
          (changed_at,actor,operation,group_name,effective_from,old_state,new_state)
          VALUES(?,?,?,?,?,?,?)''',
          (now,str(actor),'DELETED',group,day,_ex_json.dumps(dict(old),sort_keys=True),None))
        c.execute("UPDATE exception_sync_state SET revision=revision+1 WHERE id='plans'")
    audit('MANUAL_PLAN_DELETED', f'{group} {day} by {actor}')


def exception_plan(group, service_date):
    """Manual dated plans take priority; fall back to visible Excel worksheets."""
    day = parse_date(service_date).isoformat()
    with connect() as c:
        manual = c.execute('''SELECT * FROM manual_staffing_plans
            WHERE group_name=? COLLATE NOCASE AND effective_from<=?
            ORDER BY effective_from DESC LIMIT 1''',(group,day)).fetchone()
        if manual is not None:
            row = dict(manual)
            row['components'] = _ex_json.loads(row['components_json'])
            row['worksheet'] = 'Settings / manual plan'
            row['effective_to'] = 'Until superseded'
            row['source'] = 'Manual staffing planner'
            return row, ''
        matches = [dict(r) for r in c.execute('''
            SELECT * FROM staffing_plans WHERE group_name=? COLLATE NOCASE
              AND effective_from<=? AND effective_to>=?''',
            (group, day, day))]
    if not matches:
        return None, 'No staffing plan for this group/date. Use Settings → Manage Staffing Plans or import a current tracker.'
    # Never guess between two worksheets or overlapping Excel sources.
    if len(matches) != 1:
        return None, 'Multiple overlapping Excel planning sources. Set a manual plan in Settings to resolve this date.'
    row = matches[0]
    row['components'] = _ex_json.loads(row['components_json'])
    row['source'] = 'Visible Excel worksheet'
    unsupported = [v for v in row['components'] if v not in EX_METRIC_FIELDS]
    if unsupported:
        return None, f"Plan includes roles not captured by WhatsApp ({', '.join(unsupported)}). Set a manual plan with supported roles."
    return row, ''


def exception_reports(group, service_date):
    # Include next-morning reports for explicit night-shift selection only.
    day = parse_date(service_date)
    next_day = day + _ex_timedelta(days=1)
    with connect() as c:
        return [dict(r) for r in c.execute('''
           SELECT id,event_at,group_name,t002_t003,pwt,spl,pl,no_shows
           FROM reports WHERE group_name=? COLLATE NOCASE
           AND event_at>=? AND event_at<?
           AND (t002_t003 IS NOT NULL OR pwt IS NOT NULL
                OR spl IS NOT NULL OR pl IS NOT NULL)
           ORDER BY event_at DESC,id DESC''',
           (group, day.isoformat()+'T00:00:00', next_day.isoformat()+'T08:00:00'))]


def whatsapp_no_shows_for_snapshot(group, service_date, snapshot):
    # Use explicit count only; ambiguous multiple WhatsApp updates stay unknown.
    if snapshot.get('no_shows') is not None:
        return snapshot['no_shows']
    report_day = snapshot['event_at'][:10]
    service_day = parse_date(service_date).isoformat()
    with connect() as c:
        others = [r[0] for r in c.execute('''SELECT no_shows FROM reports
          WHERE group_name=? COLLATE NOCASE
            AND substr(event_at,1,10) IN (?,?)
            AND no_shows IS NOT NULL''', (group, report_day, service_day))]
    return others[0] if len(others)==1 else None


def exception_entry(group, service_date, shift_label='Shift 1'):
    with connect() as c:
        found = c.execute('''SELECT * FROM exception_entries
          WHERE group_name=? COLLATE NOCASE AND service_date=? AND shift_label=?''',
          (group, service_date, shift_label)).fetchone()
    return dict(found) if found else None


def _ex_validate_nonneg(n, field):
    if isinstance(n, bool) or not str(n).strip().isdigit() or len(str(n).strip()) > 6:
        raise ValueError(f'{field} must be a non-negative whole number.')
    value = int(n)
    if value > 100000:
        raise ValueError(f'{field} cannot exceed 100000.')
    return value


def _ex_validate_signed(n, field):
    val = str(n).strip()
    if not _ex_re.fullmatch(r'[+-]?\d{1,6}', val):
        raise ValueError(f'{field} must be a signed whole number, e.g. -2, 0, +1.')
    value = int(val)
    if abs(value) > 100000:
        raise ValueError(f'{field} magnitude cannot exceed 100000.')
    return value


def save_change_counts(actor, group, service_date, shift_label,
                       expected_revision, before_count, after_count, report_id=None):
    # Single operator edits both change counts; no-show history is untouched.
    if not str(actor).strip() or len(str(actor)) > 100:
        raise PermissionError('Sign in to record changes.')
    group = str(group).strip()
    registered = {x['name'].casefold() for x in group_registry() if x['enabled']}
    if group.casefold() not in registered:
        raise ValueError('Group is not active. Enable it under Settings → Group Registry.')
    day = parse_date(service_date).isoformat()
    if shift_label not in EX_SHIFTS:
        raise ValueError('Invalid shift.')
    if not isinstance(expected_revision,int) or expected_revision < 0:
        raise ValueError('Invalid entry revision.')
    before = _ex_validate_nonneg(before_count, 'Changes before 15:00')
    after = _ex_validate_nonneg(after_count, 'Changes after 15:00')
    if report_id not in (None,''):
        try: report_id = int(report_id)
        except (ValueError,TypeError): raise ValueError('Invalid WhatsApp report.')
        if report_id not in {x['id'] for x in exception_reports(group, day)}:
            raise ValueError('Selected WhatsApp report is not associated with this group/service window.')
    else:
        report_id = None
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        if report_id is not None:
            duplicate = c.execute('''SELECT shift_label,service_date FROM exception_entries
                WHERE report_id=? AND NOT (group_name=? COLLATE NOCASE
                AND service_date=? AND shift_label=?)''',
                (report_id,group,day,shift_label)).fetchone()
            if duplicate:
                raise ValueError('This WhatsApp report is already linked to another shift: '
                                 + duplicate['service_date'] + ' / ' + duplicate['shift_label'])
        old = c.execute('''SELECT * FROM exception_entries WHERE group_name=? COLLATE NOCASE
             AND service_date=? AND shift_label=?''', (group,day,shift_label)).fetchone()
        old_data = dict(old) if old else {}
        revision = int(old_data.get('revision', 0))
        if expected_revision != revision:
            raise RuntimeError('This entry has changed since you loaded it. Reload before submitting.')
        now = stamp()
        if old is None:
            c.execute('''INSERT INTO exception_entries(group_name,service_date,shift_label,updated_at)
                         VALUES(?,?,?,?)''',(group,day,shift_label,now))
        changed = c.execute('''UPDATE exception_entries SET before_count=?, after_count=?,
             before_impact=NULL, after_impact=NULL, basis='already_actual',report_id=?,
             before_by=?, before_at=?, after_by=?, after_at=?,
             updated_at=?, revision=revision+1
             WHERE group_name=? COLLATE NOCASE AND service_date=? AND shift_label=? AND revision=?''',
             (before,after,report_id,actor,now,actor,now,now,group,day,shift_label,revision))
        if changed.rowcount != 1:
            raise RuntimeError('Concurrent update detected. Reload before submitting.')
        new = dict(c.execute('''SELECT * FROM exception_entries WHERE group_name=? COLLATE NOCASE
          AND service_date=? AND shift_label=?''',(group,day,shift_label)).fetchone())
        c.execute('''INSERT INTO exception_entry_audit
         (changed_at,group_name,service_date,shift_label,actor_role,actor_name,old_state,new_state)
         VALUES(?,?,?,?,?,?,?,?)''',
         (now,group,day,shift_label,'changes',str(actor).strip(),
          _ex_json.dumps(old_data,sort_keys=True),_ex_json.dumps(new,sort_keys=True)))
    audit('CHANGE_COUNTS_EDIT',f'{group} {day} {shift_label} before={before},after={after}; {actor}')
    return new


def save_change_stage(actor, group, service_date, shift_label,
                      expected_revision, stage, count):
    """Save one deadline's change count without requiring a WhatsApp report.

    The other stage and any linked actual-attendance report are left intact.
    A submitted zero means a confirmed zero; an unsubmitted NULL is unknown.
    """
    if not str(actor).strip() or len(str(actor)) > 100:
        raise PermissionError('Sign in to record changes.')
    group = str(group).strip()
    active = {x['name'].casefold() for x in group_registry() if x['enabled']}
    if group.casefold() not in active:
        raise ValueError('Group is not active. Enable it under Settings.')
    day = parse_date(service_date).isoformat()
    if shift_label not in EX_SHIFTS:
        raise ValueError('Invalid shift.')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('Invalid entry revision.')
    if stage not in ('before','after'):
        raise ValueError('Choose before or after 15:00.')
    value = _ex_validate_nonneg(count, 'Changes '+stage+' 15:00')
    # These are fixed, reviewed SQL column names, never passed by the user.
    field, by_field, at_field = {
        'before': ('before_count','before_by','before_at'),
        'after': ('after_count','after_by','after_at')
    }[stage]
    now = stamp()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old = c.execute("""SELECT * FROM exception_entries
            WHERE group_name=? COLLATE NOCASE AND service_date=? AND shift_label=?""",
            (group,day,shift_label)).fetchone()
        previous = dict(old) if old else {}
        revision = int(previous.get('revision',0))
        if revision != expected_revision:
            raise RuntimeError('Another edit was saved. Reload this shift before continuing.')
        if not old:
            c.execute("""INSERT INTO exception_entries(group_name,service_date,shift_label,updated_at)
                VALUES(?,?,?,?)""",(group,day,shift_label,now))
        cur = c.execute(f"""UPDATE exception_entries SET {field}=?, {by_field}=?, {at_field}=?,
            basis='already_actual',updated_at=?,revision=revision+1
            WHERE group_name=? COLLATE NOCASE AND service_date=? AND shift_label=?
              AND revision=?""",
            (value,str(actor).strip(),now,now,group,day,shift_label,revision))
        if cur.rowcount != 1:
            raise RuntimeError('Concurrent edit detected. Reload this shift.')
        updated = dict(c.execute("""SELECT * FROM exception_entries WHERE
            group_name=? COLLATE NOCASE AND service_date=? AND shift_label=?""",
            (group,day,shift_label)).fetchone())
        c.execute("""INSERT INTO exception_entry_audit
            (changed_at,group_name,service_date,shift_label,actor_role,actor_name,old_state,new_state)
            VALUES(?,?,?,?,?,?,?,?)""",
            (now,group,day,shift_label,'changes_'+stage,str(actor).strip(),
             _ex_json.dumps(previous,sort_keys=True),_ex_json.dumps(updated,sort_keys=True)))
    audit('CHANGE_STAGE_SAVED',f'{group} {day} {shift_label}: {stage}={value}, actor={actor}')
    return updated


def link_final_attendance(actor,group,service_date,shift_label,
                          expected_revision,report_id):
    """Explicitly link an ambiguous/after-midnight WhatsApp snapshot after it arrives."""
    if not str(actor).strip():
        raise PermissionError('Sign in to link attendance.')
    day=parse_date(service_date).isoformat()
    group=str(group).strip()
    if group.casefold() not in {x['name'].casefold() for x in group_registry() if x['enabled']}:
        raise ValueError('Group is not active.')
    if shift_label not in EX_SHIFTS:
        raise ValueError('Invalid shift.')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('Invalid revision.')
    try: report_id=int(report_id)
    except (TypeError,ValueError): raise ValueError('Select an actual WhatsApp attendance report.')
    if report_id not in {r['id'] for r in exception_reports(group,day)}:
        raise ValueError('Report does not match this group/night-shift service window.')
    now=stamp()
    with connect() as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute("""SELECT * FROM exception_entries
            WHERE group_name=? COLLATE NOCASE AND service_date=? AND shift_label=?""",
            (group,day,shift_label)).fetchone()
        previous=dict(old) if old else {}
        if expected_revision != int(previous.get('revision',0)):
            raise RuntimeError('This record has changed. Reload before linking a report.')
        collision=c.execute("""SELECT service_date,shift_label FROM exception_entries
            WHERE report_id=? AND NOT (group_name=? COLLATE NOCASE
            AND service_date=? AND shift_label=?) LIMIT 1""",
            (report_id,group,day,shift_label)).fetchone()
        if collision:
            raise ValueError('This WhatsApp report is already linked to '
                             +collision['service_date']+' '+collision['shift_label'])
        if not old:
            c.execute("""INSERT INTO exception_entries(group_name,service_date,shift_label,updated_at)
                VALUES(?,?,?,?)""",(group,day,shift_label,now))
        cur=c.execute("""UPDATE exception_entries
            SET report_id=?,basis='already_actual',updated_at=?,revision=revision+1
            WHERE group_name=? COLLATE NOCASE AND service_date=? AND shift_label=? AND revision=?""",
            (report_id,now,group,day,shift_label,expected_revision))
        if cur.rowcount!=1:
            raise RuntimeError('Concurrent attendance edit detected. Reload.')
        updated=dict(c.execute("""SELECT * FROM exception_entries
            WHERE group_name=? COLLATE NOCASE AND service_date=? AND shift_label=?""",
            (group,day,shift_label)).fetchone())
        c.execute("""INSERT INTO exception_entry_audit
            (changed_at,group_name,service_date,shift_label,actor_role,actor_name,old_state,new_state)
            VALUES(?,?,?,?,?,?,?,?)""",
            (now,group,day,shift_label,'attendance_link',str(actor).strip(),
             _ex_json.dumps(previous,sort_keys=True),_ex_json.dumps(updated,sort_keys=True)))
    audit('FINAL_ATTENDANCE_LINKED',f'{group} {day} {shift_label} linked report {report_id}')
    return updated


def calculate_exception(group, service_date, shift_label='Shift 1'):
    """Calculate shortage by *role* from the actual WhatsApp snapshot.

    A recorded change is an event, not automatically one employee missing.
    The WhatsApp message is actual attendance, so never re-subtract operator
    changes or no-shows (which would double-count absent staff).

    Role shortages are summed; surplus in a different skill category does not
    hide a safety-critical vacancy. Operator change logs are independent
    of availability; an identifiable WhatsApp report is sufficient to show
    a calculated shortage without waiting for manual change counts.
    """
    plan, plan_error = exception_plan(group, service_date)
    record = exception_entry(group, service_date, shift_label)
    snapshots = exception_reports(group, service_date)
    result = {'group': group, 'service_date': service_date, 'shift': shift_label,
              'plan_total': plan['planned_total'] if plan else None,
              'plan_components': plan['components'] if plan else {},
              'before_count': record['before_count'] if record else None,
              'after_count': record['after_count'] if record else None,
              'no_shows': None,  # WhatsApp only; historical manual values are ignored.
              'staffing_reported': None, 'available': None, 'shortage': None,
              'role_breakdown': [], 'group_total_gap': None,
              'basis': 'already_actual',
              'report_id': record['report_id'] if record else None,
              'status': 'Pending', 'reason': '',
              'revision': record['revision'] if record else 0}
    if plan_error:
        result['reason'] = plan_error
        return result
    if record and record['report_id'] is not None:
        matching = [r for r in snapshots if r['id'] == record['report_id']]
        if not matching:
            result['reason'] = 'Selected WhatsApp report is missing or belongs to another date.'
            return result
        snapshot = matching[0]
    elif len(snapshots)==1 and snapshots[0]['event_at'][:10] == service_date:
        # Automatic matching is safe only with exactly one calendar-day report
        # and no post-midnight competing report or other shift assignment.
        with connect() as c:
            other_shift = c.execute('''SELECT 1 FROM exception_entries
                WHERE group_name=? COLLATE NOCASE AND service_date=?
                AND shift_label<>? LIMIT 1''', (group,service_date,shift_label)).fetchone()
            assigned_elsewhere = c.execute('''SELECT 1 FROM exception_entries
                WHERE report_id=? AND NOT (group_name=? COLLATE NOCASE
                AND service_date=? AND shift_label=?) LIMIT 1''',
                (snapshots[0]['id'],group,service_date,shift_label)).fetchone()
        if other_shift or assigned_elsewhere:
            result['reason'] = 'Report already assigned to another shift. Select the correct final report.'
            return result
        snapshot = snapshots[0]
    elif not snapshots:
        result['reason'] = 'No WhatsApp attendance report for this group and date.'
        return result
    else:
        result['reason'] = 'Select the final attendance message for this night shift (the message may be after midnight).'
        return result

    needed = [(role, EX_METRIC_FIELDS[role]) for role, required in plan['components'].items() if required > 0]
    missing = [role for role, field in needed if snapshot[field] is None]
    if missing:
        result['reason'] = 'WhatsApp attendance is missing required role(s): ' + ', '.join(missing)
        return result
    role_breakdown = []
    for role, field in EX_METRIC_FIELDS.items():
        planned = int(plan['components'].get(role, 0))
        raw_actual = snapshot[field]
        # A role not required by the plan can be blank. It cannot substitute
        # for a missing required role; show it as 0 only for total display.
        present = int(raw_actual) if raw_actual is not None else 0
        if present < 0:
            result['reason'] = f'Invalid negative WhatsApp headcount for {role}.'
            return result
        role_breakdown.append({'role': role, 'required': planned, 'present': present,
                               'shortage': max(0, planned - present),
                               'surplus': max(0, present - planned)})
    available = sum(r['present'] for r in role_breakdown)
    shortage = sum(r['shortage'] for r in role_breakdown)
    complete = bool(record and record['before_count'] is not None
                    and record['after_count'] is not None)
    reported_no_shows = whatsapp_no_shows_for_snapshot(group, service_date, snapshot)
    result.update(staffing_reported=available, available=available,
                  shortage=shortage, role_breakdown=role_breakdown,
                  no_shows=reported_no_shows,
                  group_total_gap=max(0, int(plan['planned_total']) - available),
                  status='Complete' if complete else 'Attendance',
                  reason=('Attendance captured; both change counts submitted.' if complete else
                          'Attendance captured; change counts have not yet both been entered.'),
                  report_id=snapshot['id'])
    return result


def exception_dashboard(start=None, end=None, group='All Groups'):
    """Include unreviewed WhatsApp days so missing manual submissions are visible."""
    start_iso = parse_date(start).isoformat() if start else None
    end_iso = parse_date(end).isoformat() if end else None
    conditions, params = [], []
    if start_iso:
        conditions.append('event_at>=?')
        params.append(start_iso + 'T00:00:00')
    if end_iso:
        conditions.append('event_at<?')
        params.append((_ex_datetime.fromisoformat(end_iso) + _ex_timedelta(days=1)).date().isoformat()+'T00:00:00')
    if group != 'All Groups':
        conditions.append('group_name=? COLLATE NOCASE')
        params.append(group)
    report_where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    conditions2, params2 = [], []
    if start_iso:
        conditions2.append('service_date>=?'); params2.append(start_iso)
    if end_iso:
        conditions2.append('service_date<=?'); params2.append(end_iso)
    if group != 'All Groups':
        conditions2.append('group_name=? COLLATE NOCASE'); params2.append(group)
    manual_where = ' WHERE ' + ' AND '.join(conditions2) if conditions2 else ''
    with connect() as c:
        # One after-midnight report linked to the previous shift must not be
        # double-counted as an independent following-day staffing snapshot.
        report_condition = ' AND ' if report_where else ' WHERE '
        reports_sql = ('''SELECT DISTINCT r.group_name,substr(r.event_at,1,10)
           FROM reports r''' + report_where + report_condition +
           ''' (r.t002_t003 IS NOT NULL OR r.pwt IS NOT NULL
                OR r.spl IS NOT NULL OR r.pl IS NOT NULL)
             AND NOT EXISTS (SELECT 1 FROM exception_entries e
               WHERE e.report_id=r.id AND e.service_date<>substr(r.event_at,1,10))''')
        reports = [tuple(x) for x in c.execute(reports_sql, params)]
        manual = [tuple(x) for x in c.execute('SELECT group_name,service_date,shift_label '
                    'FROM exception_entries' + manual_where, params2)]
    manual_by_day = {(g.casefold(), d) for g, d, shift in manual}
    keys = set(manual)
    for g, d in reports:
        if (g.casefold(), d) not in manual_by_day:
            keys.add((g, d, 'Shift 1'))
    # Retain historical data from renamed/paused groups; new active groups
    # appear in the editor immediately even before their first report.
    rows = [calculate_exception(g, d, shift) for g, d, shift in
            sorted(keys, key=lambda x:(x[1], x[0].casefold(), x[2]), reverse=True)]
    calculable = [r for r in rows if r['status'] in ('Complete','Attendance')]
    return dict(rows=rows,
                changes_before=sum(r['before_count'] for r in rows if r['before_count'] is not None),
                changes_after=sum(r['after_count'] for r in rows if r['after_count'] is not None),
                changes_before_known=sum(r['before_count'] is not None for r in rows),
                changes_after_known=sum(r['after_count'] is not None for r in rows),
                no_shows=sum(r['no_shows'] for r in calculable if r['no_shows'] is not None),
                no_shows_known=sum(r['no_shows'] is not None for r in calculable),
                shortage=sum(r['shortage'] for r in calculable),
                short_shifts=sum(r['shortage'] > 0 for r in calculable),
                verified=len(calculable),
                change_logs_complete=sum(r['status']=='Complete' for r in calculable),
                pending=len(rows)-len(calculable),
                awaiting_whatsapp=sum(r['reason'].startswith('No WhatsApp attendance report') for r in rows),
                awaiting_manual_changes=sum(r['status']=='Attendance' for r in rows))



def service_date_has_records(service_date):
    """True for a persisted exception entry or WhatsApp staffing report.

    Deliberately excludes the group registry: 40 registered groups are *not*
    40 records. A manual entry may precede the final WhatsApp report.
    """
    day = parse_date(service_date).isoformat()
    with connect() as c:
        if c.execute('SELECT 1 FROM exception_entries WHERE service_date=? LIMIT 1',
                     (day,)).fetchone():
            return True
        return bool(c.execute("""SELECT 1 FROM reports r
            WHERE substr(r.event_at,1,10)=?
              AND (r.t002_t003 IS NOT NULL OR r.pwt IS NOT NULL
                   OR r.spl IS NOT NULL OR r.pl IS NOT NULL)
              AND NOT EXISTS (
                SELECT 1 FROM exception_entries e
                WHERE e.report_id=r.id AND e.service_date<>?
              ) LIMIT 1""", (day, day)).fetchone())


def workforce_exception_chart_rows(rows, limit=10):
    """Four independently reported KPI categories by group for the chart.

    A missing no-show count is not converted into an asserted zero. Only
    present counts contribute to the bar; the chart labels the coverage caveat.
    Category totals are incident counts, NOT a unique-person headcount.
    """
    grouped={}
    for r in rows:
        name=str(r['group'])
        g=grouped.setdefault(name, {'group':name, 'before':0, 'after':0,
                                    'no_shows':0, 'shortage':0, 'no_shows_known':0})
        for field, source in [('before','before_count'),('after','after_count'),
                              ('no_shows','no_shows'),('shortage','shortage')]:
            value=r.get(source)
            if value is not None:
                g[field]+=max(0,int(value))
                if field=='no_shows':g['no_shows_known']+=1
    result=sorted(grouped.values(),
                  key=lambda g:(-(g['before']+g['after']+g['no_shows']+g['shortage']),
                                g['group'].casefold()))
    return result[:max(0,int(limit))]


def exception_data_signature():
    with connect() as c:
        a = c.execute('SELECT COUNT(*),COALESCE(SUM(revision),0) FROM exception_entries').fetchone()
        b = c.execute("SELECT revision FROM exception_sync_state WHERE id='plans'").fetchone()
    return tuple(a) + tuple(b)


init_exception_db()
