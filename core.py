"""
OPERATIONAL INTELLIGENCE v2.8 - DESKTOP + JUPYTER EXECUTIVE APPLICATION

Run from VS Code with Python 3.10+:
  python -m pip install -r requirements.txt
  python -m playwright install chromium   # only for optional WhatsApp monitoring
  python app.py

The application stores local reports in SQLite under ./data/.
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
    group names. This richer register is UI-only metadata. Pre-v2.4 settings
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


def connect():
    con=sqlite3.connect(DB,timeout=15)
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
          near_miss INTEGER, late INTEGER, origin TEXT NOT NULL DEFAULT 'import',
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
    qnote='Missing safety fields' if clean['Near Miss'] is None or clean['Late'] is None else ''
    with connect() as c:
        cursor=c.execute('''INSERT OR IGNORE INTO reports
          (fingerprint,event_at,captured_at,group_name,sender,message,t002_t003,pwt,spl,pl,near_miss,late,origin,quality_note)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (token,at,stamp(),group,sender,message,*(clean[k] for k in METRICS),origin,qnote))
        return cursor.rowcount==1

# Only for automatic replay of the original historical staging CSV.
# Dates from an earlier ambiguous parser require manual reconciliation.
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
            # Spreadsheet exports may include preformatted but empty rows.
            # They are not rejected records; skip them before validating dates.
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
                metric_values={k:row.get(k,'') for k in METRICS}
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


def export_pdf(path,rows,scope='All Groups'):
    """Locally generated PDF; same corrected workforce charts as GUI."""
    from io import BytesIO
    from reportlab.lib import colors as rlcolors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,Image,KeepTogether
    from matplotlib.figure import Figure
    info=summary(rows,configuration()['groups']);st=getSampleStyleSheet()
    class Doc(SimpleDocTemplate):
        def afterFlowable(self,flowable):
            anchor=getattr(flowable,'_section_anchor',None)
            if anchor:self.canv.bookmarkPage(anchor)
    doc=Doc(str(path),pagesize=A4,leftMargin=42,rightMargin=42,topMargin=35,bottomMargin=35)
    def heading(text,anchor):
        h=Paragraph(text,st['Heading2']);h._section_anchor=anchor;return h
    def chart(fig,width=480,height=195):
        bio=BytesIO();fig.savefig(bio,format='png',dpi=125,facecolor='white',bbox_inches='tight');bio.seek(0)
        return Image(bio,width=width,height=height)
    story=[Paragraph('OPERATIONAL INTELLIGENCE | EXECUTIVE BRIEF',st['Title']),
        Paragraph(f'Scope: {scope} | Generated {stamp()} | Local database',st['Normal']),Spacer(1,10),
        Paragraph('Sections: <link href="#kpis">KPIs</link> | <link href="#risk">Safety</link> | <link href="#workforce">Workforce</link> | <link href="#teams">Gang attendance</link>',st['Normal']),
        Spacer(1,13),heading('Executive summary','kpis')]
    complete, total, completeness=safety_completeness(rows)
    completeness_text=f'{completeness:.1f}%' if completeness is not None else '—'
    t=Table([['Captured reports','Reporting teams','Near Miss','Safety data completeness'],
             [str(info['reports']),str(info['teams']),str(info['near_miss']),completeness_text]],
             colWidths=[125]*4,rowHeights=30)
    t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),rlcolors.HexColor('#17243A')),
       ('TEXTCOLOR',(0,0),(-1,0),rlcolors.white),('BACKGROUND',(0,1),(-1,1),rlcolors.HexColor('#EAF0F8')),
       ('ALIGN',(0,0),(-1,-1),'CENTER'),('FONTNAME',(0,0),(-1,0),'Helvetica-Bold')]))
    story.extend([t,Spacer(1,14),heading('Near-miss reporting','risk')])
    fig=Figure(figsize=(7.1,2.4));ax=fig.add_subplot(111)
    plot_near_miss_calendar(ax, rows, dark=False)
    fig.tight_layout()
    story.append(chart(fig,height=170))
    story.append(Paragraph(
        'Only dates with received reports are displayed. A zero bar means a report recorded zero near-miss values.',
        st['Normal']))
    fig=Figure(figsize=(5.4,2.4));ax=fig.add_subplot(111)
    per_gang=near_miss_by_group(rows)
    gang_names=list(per_gang);gang_values=list(per_gang.values())
    gang_colors=['#d35400','#2980b9','#27ae60','#8e44ad','#f39c12','#16a085']
    original_style_pie(ax,gang_names,gang_values,
                       [gang_colors[i%len(gang_colors)] for i in range(len(gang_names))],dark=False)
    fig.tight_layout();story.append(chart(fig,width=380,height=170))
    story.append(Paragraph('Pie shows the share of reported near-miss values by gang, not verified incident rates. '
                           f'Safety fields complete in {complete} of {total} reports.',st['Italic']))
    story.extend([Spacer(1,12),heading('Workforce category volumes and allocation','workforce')])
    names=['T002/T003','PWT','SPL','PL'];values=workforce_totals(rows)
    palette=['#2980b9','#2ecc71','#e67e22','#e74c3c']
    fig=Figure(figsize=(7,2.5));ax=fig.add_subplot(111)
    bars=ax.bar(names,values,color=palette,width=.5)
    for b in bars:
        if b.get_height():ax.annotate(f'{b.get_height():.0f}',(b.get_x()+b.get_width()/2,b.get_height()),ha='center',va='bottom',fontsize=8)
    ax.tick_params(labelsize=9);fig.tight_layout();story.append(chart(fig,height=170))
    fig=Figure(figsize=(5.5,2.65));ax=fig.add_subplot(111)
    original_style_pie(ax,names,values,palette,dark=False);fig.tight_layout();story.append(chart(fig,width=390,height=195))
    story.append(Paragraph('Workforce percentages use only T002/T003, PWT, SPL and PL reported totals; repeated messages may not represent unique people.',st['Italic']))
    story.extend([Spacer(1,12),heading('Gang-wise attendance including Late','teams')])
    gangs=gang_attendance(rows)
    if gangs:
        gang_names=list(gangs)[:12]
        fields=['t002_t003','pwt','spl','pl','late']
        captions=['T002/T003','PWT','SPL','PL','Late']
        gang_palette=['#2980b9','#2ecc71','#e67e22','#e74c3c','#f1c40f']
        fig=Figure(figsize=(7.2,3.1));ax=fig.add_subplot(111)
        positions=list(range(len(gang_names)));width=.16
        for j,(key,label,color) in enumerate(zip(fields,captions,gang_palette)):
            ax.bar([i+(j-2)*width for i in positions],
                   [gangs[name][key] for name in gang_names],width,
                   label=label,color=color)
        ax.set_xticks(positions);ax.set_xticklabels(gang_names,rotation=20,ha='right',fontsize=8)
        ax.legend(fontsize=7,ncol=5);ax.tick_params(labelsize=8)
        fig.tight_layout();story.append(chart(fig,height=210))
    else:story.append(Paragraph('No gang attendance data in this period.',st['Normal']))
    story.append(Paragraph('Late figures are presented only as a separate attendance bar series; '
                           'they are excluded from workforce allocation percentages.',st['Italic']))
    story.extend([Spacer(1,10),Paragraph(f'Incomplete Near Miss or Late fields: {info["missing_safety"]}. Missing values are not confirmed zeroes.',st['Normal']),
                  Paragraph('Interactive filtering is available in the companion offline HTML export.',st['Italic'])])
    doc.build(story);audit('PDF_EXPORT',scope)

def export_interactive_html(path, rows, scope='All Groups'):
    """Self-contained, interactive HTML for local offline exploration."""
    import html
    data=[{'day':r['event_at'][:10], 'group':r['group_name'],
           'sender':r.get('sender') or '',
           'values':[r[k] for k in ('t002_t003','pwt','spl','pl','near_miss','late')]} for r in rows]
    data_json=json.dumps(data,ensure_ascii=True).replace('</','<\\/')
    # No external script, CDN, web API, or online library.
    template="""<!doctype html><html><head><meta charset='utf-8'><title>Interactive Executive Report</title>
<style>body{background:#0c1523;color:#eff5fd;font:15px system-ui;margin:0}main{max-width:1100px;margin:auto;padding:30px}h1{font-size:26px}p{color:#a9bed4}select{background:#273a53;color:white;border:1px solid #5b7895;padding:10px;border-radius:9px}.top{display:flex;gap:18px;margin:20px 0}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.panel{background:#192a40;border:1px solid #354b65;border-radius:12px;padding:16px}.kpi{font-size:26px;color:#73beff;font-weight:bold}.charts{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:16px 0}.bars{display:flex;align-items:end;gap:3px;height:160px;border-bottom:1px solid #668;overflow:hidden}.bar{background:#59bee9;flex:1;min-width:2px}.pie{width:155px;height:155px;border-radius:50%;margin:12px auto;background:#273a53}.wfrow{display:grid;grid-template-columns:90px 1fr 52px;align-items:center;gap:8px;margin:10px 0}.wftrack{background:#263a54;height:16px;border-radius:5px;overflow:hidden}.wffill{height:100%}input{background:#273a53;color:white;border:1px solid #5b7895;padding:8px;border-radius:7px}.legend{display:flex;gap:10px;flex-wrap:wrap}.legend span{padding:8px}table{width:100%;border-collapse:collapse}th,td{padding:9px;border-bottom:1px solid #334760;text-align:left}@media(max-width:720px){.grid{grid-template-columns:1fr 1fr}.charts{grid-template-columns:1fr}.top{flex-wrap:wrap}}</style></head><body><main><h1>Operational Intelligence — Executive Report</h1><p>Offline and interactive • SCOPE_TEXT • GENERATED_TEXT</p><div class='top'><label>Team <select id='team'></select></label><label>Period <select id='period'><option value='all'>All time</option><option value='today'>Today</option><option value='yesterday'>Yesterday</option><option value='7'>7 days</option><option value='30'>30 days</option><option value='90'>90 days</option><option value='custom'>Custom</option></select></label><label>From <input id='from' type='date'></label><label>To <input id='to' type='date'></label></div><div id='cards' class='grid'></div><div class='charts'><section class='panel'><h3>Daily near-miss reports</h3><div id='daily' class='bars'></div></section><section class='panel'><h3>Near-miss distribution by gang</h3><div id='riskpie' class='pie'></div><div id='risklegend' class='legend'></div></section></div><div class='charts'><section class='panel'><h3>Gang-wise attendance including Late</h3><div id='workforcebars'></div></section><section class='panel'><h3>Workforce allocation (reported totals)</h3><div id='workforcepie' class='pie'></div><div id='allocation' class='legend'></div></section></div><section class='panel'><h3>Recent workforce reports (most recent 100)</h3><div style='overflow:auto'><table><thead><tr><th>Date</th><th>Team</th><th>Sender</th><th>T002/T003</th><th>PWT</th><th>SPL</th><th>PL</th><th>Near Miss</th><th>Late</th></tr></thead><tbody id='body'></tbody></table></div></section><p>Figures reflect unverified message reports. Missing values are not confirmed zeroes. All controls run entirely in this local HTML file.</p></main><script>
const data=DATA_JSON, el=id=>document.getElementById(id), e=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
let names=['All Groups',...new Set(data.map(x=>x.group))];el('team').innerHTML=names.map(n=>'<option>'+e(n)+'</option>').join('');
function draw(){let team=el('team').value,days=el('period').value,top=data.map(x=>x.day).sort().at(-1);let cutoff='',exact='';if(days==='today'||days==='yesterday'){let d=new Date();if(days==='yesterday')d.setDate(d.getDate()-1);exact=[d.getFullYear(),String(d.getMonth()+1).padStart(2,'0'),String(d.getDate()).padStart(2,'0')].join('-')}else if(top&&days!=='all'&&days!=='custom'){let d=new Date(top+'T12:00:00');d.setDate(d.getDate()-Number(days)+1);cutoff=d.toISOString().slice(0,10)}let from=el('from').value,to=el('to').value;let a=data.filter(x=>(team==='All Groups'||x.group===team)&&(!cutoff||x.day>=cutoff)&&(!exact||x.day===exact)&&(days!=='custom'||((!from||x.day>=from)&&(!to||x.day<=to))));
let sum=i=>a.reduce((n,x)=>n+Number(x.values[i]??0),0),cards=[['Reports',a.length],['Reporting teams',new Set(a.map(x=>x.group)).size],['Near-miss values',sum(4)],['Safety completeness', a.length ? (100*a.filter(x=>x.values[4]!==null && x.values[5]!==null).length/a.length).toFixed(1)+'%' : '—']];el('cards').innerHTML=cards.map(c=>'<div class="panel"><p>'+c[0]+'</p><div class="kpi">'+c[1]+'</div></div>').join('');
let daily={};a.forEach(x=>daily[x.day]=(daily[x.day]||0)+Number(x.values[4]??0));let dates=Object.keys(daily).sort(),max=Math.max(1,...Object.values(daily));el('daily').innerHTML=dates.map(d=>'<div class="bar" style="height:'+Math.max(1,daily[d]/max*100)+'%" title="'+e(d)+': '+daily[d]+' reports"></div>').join('');
let colors=['#70baff','#67d9ba','#f5c76e','#f48e98'],labels=['T002/T003','PWT','SPL','PL'],total=[0,1,2,3].reduce((n,i)=>n+sum(i),0);el('allocation').innerHTML=labels.map((t,i)=>'<span style="color:'+colors[i]+'">'+t+': '+sum(i)+' ('+(total?(100*sum(i)/total).toFixed(1):'0')+'%)</span>').join('');
let angle=0,segments=[];labels.forEach((t,i)=>{let next=angle+(total?sum(i)/total*360:0);segments.push(colors[i]+' '+angle+'deg '+next+'deg');angle=next;});el('workforcepie').style.background=total?'conic-gradient('+segments.join(',')+')':'#273a53';
let gangTotals={};a.forEach(x=>{let g=gangTotals[x.group]||(gangTotals[x.group]=[0,0,0,0,0]);[0,1,2,3,5].forEach((k,i)=>g[i]+=Number(x.values[k]??0))});
let gp=['T002/T003','PWT','SPL','PL','Late'],gc=['#70baff','#67d9ba','#f5c76e','#f48e98','#f1c40f'];
let bars=Object.keys(gangTotals).sort().map(g=>'<div style="margin:12px 0"><strong>'+e(g)+'</strong><div class="legend">'+gp.map((t,i)=>'<span style="color:'+gc[i]+'">'+t+': '+gangTotals[g][i]+'</span>').join('')+'</div><div style="display:flex;gap:5px;align-items:end;height:90px">'+gp.map((t,i)=>{let m=Math.max(1,...Object.values(gangTotals).flat());return '<div title="'+e(t)+': '+gangTotals[g][i]+'" style="flex:1;height:'+Math.max(2,gangTotals[g][i]/m*100)+'%;background:'+gc[i]+';border-radius:3px"></div>'}).join('')+'</div></div>').join('');el('workforcebars').innerHTML=bars||'<p>No gang attendance data</p>';
let nearByGang={};a.forEach(x=>nearByGang[x.group]=(nearByGang[x.group]||0)+Number(x.values[4]??0));let risk=Object.values(nearByGang).reduce((n,v)=>n+v,0),angle2=0,segments2=[];
Object.keys(nearByGang).sort().forEach((g,i)=>{let next=angle2+(risk?nearByGang[g]/risk*100:0);segments2.push(gc[i%gc.length]+' '+angle2+'% '+next+'%');angle2=next});
el('riskpie').style.background=risk?'conic-gradient('+segments2.join(',')+')':'#273a53';
el('risklegend').innerHTML=Object.keys(nearByGang).sort().map((g,i)=>'<span style="color:'+gc[i%gc.length]+'">'+e(g)+' '+nearByGang[g]+' ('+(risk?100*nearByGang[g]/risk:0).toFixed(1)+'%)</span>').join('');

el('body').innerHTML=a.slice(0,100).map(x=>'<tr><td>'+e(x.day)+'</td><td>'+e(x.group)+'</td><td>'+e(x.sender||'—')+'</td>'+x.values.map(y=>'<td>'+e(y??'—')+'</td>').join('')+'</tr>').join('');}
el('team').onchange=draw;el('period').onchange=draw;el('from').onchange=()=>{el('period').value='custom';draw()};el('to').onchange=()=>{el('period').value='custom';draw()};draw();</script></body></html>"""
    template=template.replace('DATA_JSON',data_json).replace('SCOPE_TEXT',html.escape(scope)).replace('GENERATED_TEXT',stamp())
    Path(path).write_text(template,encoding='utf-8')
    audit('HTML_EXPORT',scope)

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
