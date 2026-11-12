"""Optional authorized WhatsApp Web collector and SQLite bridge (v3.0.0).

The WhatsApp collector is opt-in; changing web interfaces may affect selectors.
"""
import core
from core import *  # Explicit v3.0.0 notebook compatibility contract.


# --- extracted notebook cell 6 ---
# ---- Compatibility: analytics functions share one module in this build ----


# ---- Optional browser monitoring ----
"""Optional live WhatsApp Web adapter. No official messaging API used.
Requires user-authorised browser login; WhatsApp UI selectors can change.
"""
import asyncio, logging, re, threading
from datetime import datetime, timedelta
from pathlib import Path

LOG=logging.getLogger('monitor')


def whatsapp_datetime(pretext):
    '''Parse the timestamp supplied by WhatsApp's message metadata.

    Refuse an unrecognised timestamp instead of silently misdating a report.
    '''
    match = re.search(r"\[([^\]]+)\]", pretext or "")
    if not match or ',' not in match.group(1):
        return None
    time_part, date_part = (v.strip() for v in match.group(1).split(',', 1))
    value = date_part.casefold().strip()
    if value == 'today':
        day = datetime.now().date()
    elif value == 'yesterday':
        day = datetime.now().date() - timedelta(days=1)
    else:
        # Compatibility entry point: delegate numeric dates to the *same*
        # parser used by the original WhatsApp engine. Never parse WhatsApp
        # metadata using the CSV (DD/MM/YYYY) reader.
        try:
            uk_date, normal_time = parse_whatsapp_timestamp(pretext)
            return core.normalise_datetime(uk_date, normal_time)
        except ValueError:
            return None
    try:
        return core.normalise_datetime(day, time_part)
    except ValueError:
        return None


def collector_diagnostics():
    with core.connect() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS collection_diagnostics (
          id INTEGER PRIMARY KEY CHECK(id=1), updated_at TEXT,
          group_name TEXT, visible_messages INTEGER DEFAULT 0,
          metric_messages INTEGER DEFAULT 0, saved INTEGER DEFAULT 0,
          duplicate INTEGER DEFAULT 0, missing_timestamp INTEGER DEFAULT 0,
          last_note TEXT DEFAULT '')''')
        con.execute('INSERT OR IGNORE INTO collection_diagnostics(id) VALUES (1)')
        return dict(con.execute('SELECT * FROM collection_diagnostics WHERE id=1').fetchone())


def save_collector_diagnostics(group, counters, note=''):
    with core.connect() as con:
        con.execute('''CREATE TABLE IF NOT EXISTS collection_diagnostics (
          id INTEGER PRIMARY KEY CHECK(id=1), updated_at TEXT,
          group_name TEXT, visible_messages INTEGER DEFAULT 0,
          metric_messages INTEGER DEFAULT 0, saved INTEGER DEFAULT 0,
          duplicate INTEGER DEFAULT 0, missing_timestamp INTEGER DEFAULT 0,
          last_note TEXT DEFAULT '')''')
        con.execute('''INSERT INTO collection_diagnostics
          (id,updated_at,group_name,visible_messages,metric_messages,saved,duplicate,missing_timestamp,last_note)
          VALUES(1,?,?,?,?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at,
          group_name=excluded.group_name, visible_messages=excluded.visible_messages,
          metric_messages=excluded.metric_messages,saved=excluded.saved,
          duplicate=excluded.duplicate,missing_timestamp=excluded.missing_timestamp,
          last_note=excluded.last_note''',
          (core.stamp(), group, *(counters.get(k,0) for k in
           ('visible','metrics','saved','duplicate','missing_timestamp')), note[:300]))


# --- extracted notebook cell 8 ---
# ---- Original, user-provided WhatsApp processing engine ----
# Extracted from WhatsApp_Integration_FIXED (Reporting Module)(1).ipynb.
# Collection, parsing, and original CSV/Excel writes are preserved.
# Only the initial URL and proactive group sweep interval are adjusted.
import os
import asyncio
import tkinter as tk
from openpyxl import load_workbook, Workbook
from playwright.async_api import async_playwright

BASE_DIR = str(ROOT)
_legacy_config = ROOT / 'config.json'
try:
    _legacy_paths = json.loads(_legacy_config.read_text(encoding='utf-8'))
except (OSError, ValueError):
    _legacy_paths = {}
LOCAL_FILE_PATH = str(_legacy_paths.get('excel_path') or (DATA / 'WhatsAppData.xlsx'))
STAGING_CSV_PATH = str(_legacy_paths.get('csv_path') or (DATA / 'WhatsAppData_staging.csv'))
TARGET_GROUPS = list(configuration()['groups'])
recently_logged_fingerprints = set()
is_running = False
browser_context = None
playwright_instance = None
worker_loop = None

def load_saved_groups():
    global TARGET_GROUPS
    if os.path.exists(GROUPS_CONFIG_PATH):
        try:
            with open(GROUPS_CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = [line.strip() for line in f if line.strip()]
                if saved:
                    TARGET_GROUPS = saved
                    logging.info(f"📂 Loaded {len(TARGET_GROUPS)} target groups from config file.")
                    return
        except Exception as e:
            logging.warning(f"⚠️ Could not load saved groups config: {e}")
    logging.info("📋 Config file not found or empty. Using default group lists.")

def save_groups_to_disk():
    try:
        with open(GROUPS_CONFIG_PATH, "w", encoding="utf-8") as f:
            for group in TARGET_GROUPS:
                f.write(f"{group}\n")
        logging.info("💾 Target group layout successfully saved to disk.")
    except Exception as e:
        logging.error(f"❌ Failed to auto-save groups configuration: {e}")

def load_existing_fingerprints():
    global recently_logged_fingerprints
    if os.path.exists(STAGING_CSV_PATH):
        try:
            with open(STAGING_CSV_PATH, mode='r', newline='', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if not header:
                    return
                for row in reader:
                    if len(row) >= 5:
                        layout_token = f"{row[2]}_{row[3]}_{row[0]}_{row[1]}_{row[4]}"
                        recently_logged_fingerprints.add(layout_token)
            logging.info(f"💾 Synchronized {len(recently_logged_fingerprints)} historic records into deduplication stack.")
        except Exception as e:
            logging.warning(f"⚠️ Could not load history pipeline: {e}")

def parse_metrics_from_text(text):
    clean_text = " ".join(text.split())
    extracted_values = {}
    t_match = re.search(r"T002\s*[\/\\\-_]\s*T003[:\s\-]*(\d+)", clean_text, re.IGNORECASE)
    extracted_values["T002/T003"] = int(t_match.group(1)) if t_match else ""
    for key in ["PWT", "SPL", "PL", "Near Miss", "Late"]:
        match = re.search(rf"\b{key}[:\s\-]*(\d+)", clean_text, re.IGNORECASE)
        extracted_values[key] = int(match.group(1)) if match else ""
    
    # Capture even a standalone WhatsApp no-show update (the full message is
    # preserved in the existing CSV, and SQLite parses its explicit count).
    found_any = any(extracted_values[k] != "" for k in extracted_values)
    if not found_any and parse_explicit_no_shows(text) is not None:
        found_any = True
    return extracted_values, found_any

# WhatsApp Web's message metadata in this user's installation is US-style
# MONTH/DAY/YEAR. We verified this against stored records captured on
# 10 November 2026: WhatsApp's 11/10/2026 was incorrectly saved as 11 October.
# The staging CSV uses the independent, *strict* UK DAY/MONTH/YEAR convention.
# Do NOT set this to DMY just to make the CSV look British: that swaps the
# meaning of incoming WhatsApp dates. If WhatsApp's own interface is changed
# to a British date locale later, change this input setting to "DMY".
WHATSAPP_NUMERIC_DATE_ORDER = "MDY"
CSV_DATE_OUTPUT_FORMAT = "%d/%m/%Y"
assert WHATSAPP_NUMERIC_DATE_ORDER in {"MDY", "DMY"}


def parse_whatsapp_timestamp(pre_text):
    """Return (DD/MM/YYYY, HH:MM:SS) from WhatsApp message metadata.

    Do not substitute today's date when a historical timestamp cannot be read.
    That would silently convert yesterday's message to a report for today.
    """
    match = re.search(r"\[([^\]]+)\]", pre_text or "")
    if not match:
        raise ValueError("Missing WhatsApp message timestamp metadata")
    parts = match.group(1).split(',', 1)
    if len(parts) != 2:
        raise ValueError(f"Timestamp lacks a date: {match.group(1)!r}")
    raw_time, raw_date = (part.strip() for part in parts)
    today = datetime.now().date()
    date_value = raw_date.casefold()
    if date_value == "today":
        day = today
    elif date_value == "yesterday":
        day = today - timedelta(days=1)
    elif re.fullmatch(r"\d{4}-\d{1,2}-\d{1,2}", raw_date):
        day = datetime.strptime(raw_date, "%Y-%m-%d").date()
    elif re.fullmatch(r"\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}", raw_date):
        # Source input order must be explicit. Guessing from the numbers is
        # unreliable when both components are <=12 (e.g. 11/10/2026).
        # Here MDY 11/10/2026 = 10 November, not 11 October.
        a, b, year = re.split(r"[/.-]", raw_date)
        fmt = "%m/%d/%Y" if WHATSAPP_NUMERIC_DATE_ORDER == "MDY" else "%d/%m/%Y"
        if len(year) == 2:
            fmt = fmt.replace("%Y", "%y")
        day = datetime.strptime("/".join((a, b, year)), fmt).date()
    elif date_value in {"monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"}:
        weekday = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"].index(date_value)
        days_ago = (today.weekday() - weekday) % 7
        day = today - timedelta(days=days_ago)
    else:
        raise ValueError(f"Unsupported WhatsApp date: {raw_date!r}")

    # WhatsApp often supplies time as 9:05 AM or 21:05; normalise it as well.
    time_text = re.sub(r"\s+", " ", raw_time.upper()).strip()
    parsed_time = None
    for fmt in ("%I:%M:%S %p", "%I:%M %p", "%H:%M:%S", "%H:%M"):
        try:
            parsed_time = datetime.strptime(time_text, fmt).time()
            break
        except ValueError:
            continue
    if parsed_time is None:
        raise ValueError(f"Unsupported WhatsApp time: {raw_time!r}")
    return day.strftime(CSV_DATE_OUTPUT_FORMAT), parsed_time.strftime("%H:%M:%S")

def append_to_local_excel(group, sender, message, metrics, actual_date, actual_time):
    # CSV/Excel invariant: dates are ALWAYS DD/MM/YYYY, including if an
    # external caller provided a date object or ISO date string.
    actual_date = parse_date(actual_date).strftime(CSV_DATE_OUTPUT_FORMAT)
    row_data = [actual_date, actual_time, group, sender, message, metrics.get("T002/T003", ""), metrics.get("PWT", ""), metrics.get("SPL", ""), metrics.get("PL", ""), metrics.get("Near Miss", 0), metrics.get("Late", 0)]
    csv_headers = ["Date", "Time", "Group", "Sender", "Full Message", "T002/T003", "PWT", "SPL", "PL",  "Near Miss", "Late"]
    
    file_exists = os.path.exists(STAGING_CSV_PATH)
    try:
        parent_csv_dir = os.path.dirname(STAGING_CSV_PATH)
        if parent_csv_dir and not os.path.exists(parent_csv_dir):
            os.makedirs(parent_csv_dir, exist_ok=True)
            
        with open(STAGING_CSV_PATH, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if not file_exists:
                writer.writerow(csv_headers)
            writer.writerow(row_data)
        logging.info("📝 Message safely staged to CSV backup.")
    except Exception as e:
        logging.error(f"❌ Critical CSV Staging Write Error: {e}")
        return
    
    try:
        parent_excel_dir = os.path.dirname(LOCAL_FILE_PATH)
        if parent_excel_dir and not os.path.exists(parent_excel_dir):
            os.makedirs(parent_excel_dir, exist_ok=True)
            
        if not os.path.exists(LOCAL_FILE_PATH):
            wb = Workbook()
            sheet = wb.active
            sheet.append(csv_headers)
            wb.save(LOCAL_FILE_PATH)
        wb = load_workbook(LOCAL_FILE_PATH)
        sheet = wb.active
        sheet.append(row_data)
        wb.save(LOCAL_FILE_PATH)
        logging.info("📊 Excel file synchronized straight to master layout.")
    except PermissionError:
        logging.warning("⚠️ Master Excel is OPEN. Data is safe inside temporary CSV pipeline.")
    except Exception as e:
        logging.error(f"❌ Local Excel Write Error: {e}")

async def process_live_feed(messages, group_name):
    global recently_logged_fingerprints
    # Process every currently loaded message. The previous last-10 slice
    # silently dropped previous-day messages in busy groups.
    for msg in messages:
        try:
            text_el = await msg.query_selector("span.selectable-text") or                       await msg.query_selector(".selectable-text span") or                       await msg.query_selector(".copyable-text") or                       await msg.query_selector("[class*='message-text']")
            if not text_el:
                continue
            message_text = (await text_el.inner_text()).strip()
            if not message_text:
                continue
                
            parent_div = await msg.query_selector("div.copyable-text")
            sender = "Unknown Sender"
            pre_text = await parent_div.get_attribute("data-pre-plain-text") if parent_div else None
            if pre_text:
                try:
                    msg_date, msg_time = parse_whatsapp_timestamp(pre_text)
                except ValueError as err:
                    logging.warning("Skipping message with invalid timestamp in %s: %s", group_name, err)
                    continue
            else:
                logging.debug("Skipping message without WhatsApp timestamp in %s", group_name)
                continue
            if "]" in pre_text:
                sender = pre_text.split("]")[-1].replace(":", "").strip()
            if sender == "Unknown Sender":
                if await msg.query_selector(".message-out") or await msg.query_selector("[class*='message-out']"):
                    sender = "You"
                else:
                    name_container = await msg.query_selector("span[dir='auto']")
                    if name_container:
                        sender = await name_container.inner_text()
            
            layout_token = f"{group_name}_{sender}_{msg_date}_{msg_time}_{message_text}"
            if layout_token in recently_logged_fingerprints:
                continue
            metrics_dict, values_detected = parse_metrics_from_text(message_text)
            if values_detected:
                logging.info(f"🎯 Metrics Found: Group '{group_name}' | From '{sender}'\n   Text: {message_text}")
                append_to_local_excel(group_name, sender, message_text, metrics_dict, msg_date, msg_time)
                # Mark processed only after the CSV/database writer returned.
                recently_logged_fingerprints.add(layout_token)
                if len(recently_logged_fingerprints) > 500:
                    recently_logged_fingerprints.pop()
        except Exception:
            logging.exception("WhatsApp extraction failed for group %s", group_name)

async def catch_up_recent_history(page, group_name, lookback_days=2, max_scrolls=22):
    """Scroll backwards through a group's virtualised history on startup.

    WhatsApp only renders a window of messages; merely opening an unread chat
    often exposes the newest items, not yesterday's report. Scan each window,
    page upward, and stop once earlier-than-lookback items are reached.
    This is a bounded catch-up, not an unbounded full-history download.
    """
    cutoff = datetime.now().date() - timedelta(days=lookback_days)
    seen_windows = set()
    for step in range(max_scrolls + 1):
        messages = await page.query_selector_all("#main [data-testid='msg-container']")
        if not messages:
            messages = await page.query_selector_all("div[data-testid='msg-container']")
        if messages:
            await process_live_feed(messages, group_name)

        # Detect oldest visible message to avoid scrolling across years.
        oldest = None
        for msg in messages[:5]:
            pre = await msg.query_selector("div.copyable-text[data-pre-plain-text]")
            if not pre:
                continue
            metadata = await pre.get_attribute('data-pre-plain-text')
            try:
                dt, _ = parse_whatsapp_timestamp(metadata)
                parsed = datetime.strptime(dt, "%d/%m/%Y").date()
                oldest = parsed if oldest is None else min(oldest, parsed)
            except ValueError:
                pass
        if oldest is not None and oldest < cutoff:
            logging.info("History catch-up reached %s for %s", oldest, group_name)
            break

        # Scroll the actual conversation pane, not the contacts sidebar.
        position = await page.evaluate("""() => {
          const msg = document.querySelector('#main [data-testid="msg-container"]');
          if (!msg) return null;
          let p = msg.parentElement;
          while (p && p.id !== 'app') {
            if (p.scrollHeight > p.clientHeight + 40 && p.clientHeight > 80) {
              const before = p.scrollTop;
              p.scrollTop = Math.max(0, before - Math.max(300, p.clientHeight * 0.85));
              return {before, after:p.scrollTop, height:p.scrollHeight};
            }
            p = p.parentElement;
          }
          return null;
        }""")
        if position is None:
            logging.warning("No scrollable history panel found for %s", group_name)
            break
        await asyncio.sleep(0.45)
        # Virtualised lists can return a temporary scrollTop of zero while loading.
        # Use message fingerprints to recognise a genuinely stagnant history.
        first = await page.query_selector('#main [data-testid="msg-container"] div.copyable-text[data-pre-plain-text]')
        key = await first.get_attribute('data-pre-plain-text') if first else None
        if key in seen_windows and position['after'] <= 0:
            break
        seen_windows.add(key)
    await page.evaluate("""() => {
      const msg = document.querySelector('#main [data-testid="msg-container"]');
      let p = msg?.parentElement;
      while (p && p.id !== 'app') {
        if (p.scrollHeight > p.clientHeight + 40 && p.clientHeight > 80) {
          p.scrollTop = p.scrollHeight;
          break;
        }
        p = p.parentElement;
      }
    }""")
    logging.info("Completed previous-day catch-up for %s (%d scan windows)", group_name, step + 1)


# ==============================================================================


# --- extracted notebook cell 10 ---
# Date-format smoke test — runs without WhatsApp login or database edits.
assert WHATSAPP_NUMERIC_DATE_ORDER == "MDY"
assert parse_whatsapp_timestamp("[10:44, 11/10/2026] Team:") == ("10/11/2026", "10:44:00")
assert parse_whatsapp_timestamp("[18:38, 11/09/2026] Team:") == ("09/11/2026", "18:38:00")
assert parse_whatsapp_timestamp("[12:56, 11/06/2026] Team:") == ("06/11/2026", "12:56:00")
assert normalise_datetime("10/11/2026", "13:15:00") == "2026-11-10T13:15:00"
print("Date tests passed: incoming MDY -> staging DMY -> SQLite ISO")

# --- extracted notebook cell 12 ---
# SECTION 3: SCRAPER ENGINE INTERNALS (THREAD ISOLATED)
# ==============================================================================
def trigger_start_engine(window_reference):
    global is_running, worker_loop
    if is_running:
        return
    if not TARGET_GROUPS:
        logging.error("❌ Action Interrupted: Tracking array filters cannot be blank.")
        return

    is_running = True
    window_reference.btn_start.configure(state="disabled")
    window_reference.btn_terminate.configure(state="normal")
    window_reference.update_status_led_threadsafe("#2ecc71", "Active")
    window_reference.update_contextual_glow_threadsafe("green")

    def run_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_until_complete(start_scraping_engine(window_reference))

    worker_loop = asyncio.new_event_loop()
    t = threading.Thread(target=run_loop, args=(worker_loop,), daemon=True)
    t.start()

def trigger_stop_engine(window_reference):
    global is_running, worker_loop
    if not is_running:
        return
    is_running = False
    logging.info("⏳ Sending clean up kill-stack events to background threads...")
    if worker_loop and worker_loop.is_running():
        asyncio.run_coroutine_threadsafe(stop_scraping_engine_internal(window_reference), worker_loop)

async def stop_scraping_engine_internal(window_reference):
    global browser_context, playwright_instance, is_running
    is_running = False
    try:
        if browser_context:
            await browser_context.close()
            browser_context = None
        if playwright_instance:
            await playwright_instance.stop()
            playwright_instance = None
    except Exception:
        pass
    finally:
        window_reference.update_status_led_threadsafe("#e74c3c", "Stopped")
        window_reference.update_contextual_glow_threadsafe("default")
        try:
            window_reference.btn_start.configure(state="normal")
            window_reference.btn_terminate.configure(state="disabled")
        except Exception:
            pass
        logging.info("🛑 Engine structural systems offline.")

async def start_scraping_engine(window_reference):
    global is_running, browser_context, playwright_instance
    logging.info("🚀 Allocating Chromium Playwright engine tasks...")
    logging.info("Date formats: WhatsApp incoming=%s, staging CSV/Excel=DD/MM/YYYY",
                 WHATSAPP_NUMERIC_DATE_ORDER)
    try:
        playwright_instance = await async_playwright().start()
        browser_context = await playwright_instance.chromium.launch_persistent_context(
            user_data_dir=os.path.join(BASE_DIR, "whatsapp_session"),
            headless=False,
            args=["--disable-blink-features=AutomationControlled"]
        )
        
        pages_list = browser_context.pages
        page = pages_list[0] if pages_list else await browser_context.new_page()
        
        await page.goto("https://web.whatsapp.com")
        logging.info("Waiting for WhatsApp Web... Please scan QR code if prompted.")
        
        try:
            await page.wait_for_selector("div[data-testid='chat-list']", timeout=90000)
            logging.info("Connected successfully! Anti-lock data parser active...")
        except Exception:
            logging.warning("⚠️ Timeout or alternative layout hook during initialization check.")
            await page.wait_for_selector("[id='app']", timeout=30000)
            logging.info("Connected via application frame checkpoint.")

        await asyncio.sleep(3)
        
        # Track rotation timing (e.g., proactive sweep every 30 seconds)
        last_proactive_sweep = datetime.now() - timedelta(seconds=6)
        sweep_interval_seconds = 5
        backfilled_groups = set()  # reset on each application monitoring session 
        
        while is_running:
            try:
                # --- METHOD 1: PROCESS THE CURRENTLY OPEN CHAT ---
                active_header_el = await page.query_selector("div[data-testid='conversation-header']") or \
                                   await page.query_selector("[class*='conversation-header']")
                if active_header_el:
                    title_el = await active_header_el.query_selector("span[title]")
                    if title_el:
                        active_group_name = await title_el.get_attribute("title")
                        if active_group_name in TARGET_GROUPS:
                            all_messages = await page.query_selector_all("div[data-testid='msg-container']") or \
                                           await page.query_selector_all("div[role='row']") or \
                                           await page.query_selector_all("[class*='message-']")
                            if all_messages:
                                await process_live_feed(all_messages, active_group_name)
                                
                # --- METHOD 2: REACT TO GREEN UNREAD BADGES ---
                unread_chats = await page.query_selector_all("span[aria-label*='unread message']") or \
                               await page.query_selector_all("[aria-label*='unread']")
                for chat in unread_chats:
                    chat_row = await chat.evaluate_handle("el => el.closest('div[role=\\'row\\']') || el.closest('[class*=\\'row\\']')")
                    if not chat_row:
                        continue
                    group_title_el = await chat_row.query_selector("span[title]")
                    if not group_title_el:
                        continue
                    group_name = await group_title_el.get_attribute("title")
                    if group_name in TARGET_GROUPS:
                        logging.info(f"📬 [Reactive Badge] Parsing downstream unread stack in group '{group_name}'...")
                        await chat_row.click()
                        await asyncio.sleep(1.5)
                        all_messages = await page.query_selector_all("div[data-testid='msg-container']") or \
                                       await page.query_selector_all("div[role='row']")
                        if all_messages:
                            await process_live_feed(all_messages, group_name)

                # --- METHOD 3: PROACTIVE ROTATION SWEEP ---
                # Runs automatically if the time interval has elapsed, tracking what was missed by phone views
                time_since_last_sweep = (datetime.now() - last_proactive_sweep).total_seconds()
                if time_since_last_sweep >= sweep_interval_seconds:
                    logging.info(f"🔄 [Proactive Sweep] Cycling target groups to catch up on items read externally...")
                    
                    for group_to_check in TARGET_GROUPS:
                        # Find the chat item in the left-side list by searching for its title text attribute
                        chat_row_el = await page.query_selector(f"span[title='{group_to_check}']")
                        if chat_row_el:
                            list_item_clickable = await chat_row_el.evaluate_handle(
                                "el => el.closest('div[role=\\'row\\']') || el.closest('[class*=\\'row\\']') || el"
                            )
                            if list_item_clickable:
                                await list_item_clickable.click()
                                await asyncio.sleep(1.5) # Give the chat pane a brief window to load
                                
                                all_messages = await page.query_selector_all("div[data-testid='msg-container']") or \
                                               await page.query_selector_all("div[role='row']")
                                if all_messages:
                                    await process_live_feed(all_messages, group_to_check)
                                if group_to_check not in backfilled_groups:
                                    # One bounded previous-day scan per group at start.
                                    # The normal live scanning loop is unchanged.
                                    await catch_up_recent_history(page, group_to_check)
                                    backfilled_groups.add(group_to_check)
                    
                    last_proactive_sweep = datetime.now() # Reset our interval clock tracker
                    
            except Exception as loop_err:
                logging.debug(f"Engine iteration warning: {loop_err}")
                pass
            
            await asyncio.sleep(1)
            
    except Exception as e:
        logging.error(f"❌ Core Parser Engine Crash Loop: {e}")
    finally:
        await stop_scraping_engine_internal(window_reference)

# ==============================================================================


# --- extracted notebook cell 14 ---
# SECTION 4: CUSTOMTKINTER INTERACTIVE GRAPHICAL DASHBOARD WINDOW
# ==============================================================================



# Minimal compatibility bridge: never modify the user's original parser or
# browser navigation. The existing CSV/Excel writer is called first, and the
# *same* parsed record is mirrored into SQLite for the executive dashboards.
_original_append_to_local_excel = append_to_local_excel
_record_sink = None

def append_to_local_excel(group, sender, message, metrics, actual_date, actual_time):
    _original_append_to_local_excel(group, sender, message, metrics, actual_date, actual_time)
    try:
        when = normalise_datetime(actual_date, actual_time)
        added = insert_report(group, sender, message, when, metrics, origin='original_whatsapp_engine')
        if added:
            # A successfully parsed message confirms that an authenticated
            # WhatsApp conversation was accessible (even on alternate layouts).
            if is_running and monitor_state()['status'] != 'Connected':
                set_monitor_state('Connected', success=True)
            if _record_sink is not None:
                _record_sink(('reports_saved', 1, group))
            save_collector_diagnostics(group, {'visible': 1, 'metrics': 1, 'saved': 1, 'duplicate': 0, 'missing_timestamp': 0}, 'Original engine wrote report; bridged to dashboard')
            LOG.info('Bridged WhatsApp report into live SQLite dashboard: %s', group)
    except Exception as exc:
        LOG.exception('Report was seen by original parser but SQLite bridge failed: %s', exc)
        if _record_sink is not None:
            _record_sink('LIVE BRIDGE ERROR: ' + str(exc))

class _NoopButton:
    def configure(self, **kwargs):
        pass

class _OriginalWindowAdapter:
    # Only the UI functions that the original engine calls are adapted.
    btn_start = _NoopButton()
    btn_terminate = _NoopButton()
    def __init__(self, emit):
        self.emit = emit
    def update_status_led_threadsafe(self, color, state):
        # The original engine invokes this upon starting and stopping. "Active"
        # is not proof WhatsApp is authenticated; only mark green on verified login.
        if state == 'Stopped':
            set_monitor_state('Disconnected')
        self.emit('WhatsApp engine: ' + state)
    def update_contextual_glow_threadsafe(self, color):
        pass
    def update_status_led(self, color, state):
        self.emit('WhatsApp engine: ' + state)
    def update_contextual_glow(self, color):
        pass

class _OriginalLogBridge(logging.Handler):
    def __init__(self, sink):
        super().__init__(logging.INFO)
        self.sink = sink
    def emit(self, record):
        message = record.getMessage()
        if 'Connected successfully!' in message:
            # The original engine detected the authenticated chat list.
            set_monitor_state('Connected', success=True)
            self.sink('WhatsApp authenticated: live monitoring connected')
        elif 'Connected via application frame' in message:
            # App frame alone also exists at the QR login screen. Do not claim
            # a connection based only on that fallback selector.
            self.sink('WhatsApp loaded; chat authentication not yet verified')
        elif 'Core Parser Engine Crash' in message:
            set_monitor_state('Error', message)
            self.sink('Original engine error: ' + message)

class WhatsAppMonitor:
    """Thin adapter around original trigger_start_engine/trigger_stop_engine."""
    def __init__(self, callback=None):
        global _record_sink
        self.callback = callback or (lambda msg: None)
        _record_sink = self.callback
        self.window = _OriginalWindowAdapter(self.callback)
        self.log_handler = _OriginalLogBridge(self.callback)
        logging.getLogger().addHandler(self.log_handler)

    @property
    def active(self):
        return bool(is_running)

    def start(self):
        global TARGET_GROUPS
        if is_running:
            return False
        TARGET_GROUPS = list(configuration()['groups'])
        if not TARGET_GROUPS:
            self.callback('Configure at least one WhatsApp group first')
            return False
        # Prevent redownloading all historical rows; previously captured CSV
        # messages can be reconciled through the idempotent SQLite importer.
        set_monitor_state('Starting')
        self.callback('Starting original WhatsApp engine for: ' + ', '.join(TARGET_GROUPS))
        load_existing_fingerprints()
        trigger_start_engine(self.window)
        return True

    def stop(self):
        if is_running:
            set_monitor_state('Disconnecting')
            trigger_stop_engine(self.window)
            self.callback('Stopping original WhatsApp engine')
        else:
            set_monitor_state('Disconnected')


def reconcile_original_csv():
    """Catch up original CSV records on application start, idempotently."""
    if not Path(STAGING_CSV_PATH).is_file():
        return 0
    added, dupes, errors = import_csv(STAGING_CSV_PATH, quarantine_legacy_dates=True)
    if errors:
        LOG.warning('Legacy CSV reconciliation: %d invalid rows; first: %s',len(errors),errors[:2])
    return added
