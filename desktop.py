"""v3.0.0 CustomTkinter desktop GUI: Overview, workforce, exceptions and reporting hub."""
from collector import *  # Notebook legacy globals and helpers.
import collector  # Import the mutable collector state by module reference.
import core
# ---- Local CustomTkinter executive desktop UI ----
"""Local desktop executive dashboard. Run: python app.py"""
import logging, queue, tkinter as tk
import json, os, re, sys, subprocess, webbrowser, uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    import customtkinter as ctk
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
except ImportError as exc:
    raise SystemExit(f'Missing dependency: {exc}. Install with: pip install -r requirements.txt')


# All UI colours come from one palette so Tk, ttk and Matplotlib stay in sync.
PALETTES = {
    'Dark': dict(NAV='#0D1626', BG='#101A2B', PANEL='#1B2B41', BORDER='#2D4059',
                 MUTED='#A4B6C9', WHITE='#F1F6FE', BLUE='#56B9FA',
                 TEAL='#54D7BA', AMBER='#FFCB78', RED='#FF8B92'),
    'Light': dict(NAV='#E9F0F7', BG='#F5F8FC', PANEL='#FFFFFF', BORDER='#D3DFEB',
                  MUTED='#53667C', WHITE='#1C3049', BLUE='#1264AB',
                  TEAL='#13765F', AMBER='#9C6509', RED='#B63246'),
}

def apply_palette(mode):
    """Update global colour tokens; rebuild GUI only on user-initiated toggle."""
    colors = PALETTES['Light' if mode == 'Light' else 'Dark']
    globals().update(colors)
    ctk.set_appearance_mode('light' if mode == 'Light' else 'dark')
    return 'Light' if mode == 'Light' else 'Dark'

ctk.set_default_color_theme('blue')
apply_palette(configuration().get('theme', 'Dark'))

class Application(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title(f'Operational Intelligence v{core.APP_VERSION} | Executive Command Centre')
        self.geometry('1480x880');self.minsize(1100,710)
        # Jupyter may configure the root logger at WARNING; enable the original
        # collector's INFO connection events so the status LED is trustworthy.
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        if not any(isinstance(h, logging.FileHandler) and
                   Path(h.baseFilename).resolve() == (DATA / 'collector.log').resolve()
                   for h in root_logger.handlers):
            root_logger.addHandler(logging.FileHandler(DATA / 'collector.log', encoding='utf-8'))
        self.theme_mode = apply_palette(configuration().get('theme', 'Dark'))
        self.configure(fg_color=BG)
        self.queue=queue.Queue();self.monitor=WhatsAppMonitor(self.queue.put)
        # A previous process may have terminated unexpectedly while its
        # SQLite status was Connected. This new process starts disconnected.
        if not collector.is_running:
            core.set_monitor_state('Disconnected')
        self.page='Overview';self._chart=None;self._rows=[];self._last_sig=None;self._last_report_day=None;self._refresh_pending=False;self._next_fallback_check=0.0;self._next_daily_check=0.0;self._next_connection_led_check=0.0;self._live_widgets={};self._visible_record_ids=();self._last_live_render=0.0
        self.period=ctk.StringVar(value='All time');self.group=ctk.StringVar(value='All Groups')
        self.custom_start=ctk.StringVar(value=(date.today()-timedelta(days=6)).isoformat());self.custom_end=ctk.StringVar(value=date.today().isoformat())
        self.status_text=ctk.StringVar(value='Local database ready')
        self.exception_session=None
        self._exception_draft_dirty=False
        self._exception_started_date=None
        self._show_settings_accounts=False
        self._plan_source_mtime=None
        self._plan_import_busy=False
        self._next_plan_check=0.0
        self.exception_group=ctk.StringVar(value=core.configuration()['groups'][0] if core.configuration()['groups'] else 'Gang 1')
        self.exception_day=ctk.StringVar(value=date.today().isoformat())
        self._reconcile_busy=False
        self._nav_compact=False
        self._nav_render_job=None
        self._nav_generation=0
        self._loading_progress=None
        self._last_view_refresh='Not refreshed yet'
        self._layout();self._navigate('Overview');self.after(100,self._poll);self.after(250,self._reconcile_legacy)
        self.protocol('WM_DELETE_WINDOW',self._close)

    def _reconcile_legacy(self):
        def work():
            self._reconcile_busy=True
            try:
                count = reconcile_original_csv()
                self.queue.put(('reports_saved', count, 'original CSV reconciliation'))
            except Exception as exc:
                self.queue.put('Original CSV reconciliation error: ' + str(exc))
            finally:
                self._reconcile_busy=False
        threading.Thread(target=work, daemon=True).start()

    def label(self,parent,text,size=13,color=WHITE,bold=False,**kwargs):
        return ctk.CTkLabel(parent,text=text,font=ctk.CTkFont(family='Segoe UI',size=size,weight='bold' if bold else 'normal'),text_color=color,**kwargs)
    def button(self,parent,text,command,color=None,width=120,**kw):
        # Explicit green/red buttons retain white labels even in light mode.
        custom = color is not None and color != 'transparent'
        color = color if color is not None else ('#D9EAF8' if self.theme_mode == 'Light' else '#254C72')
        fg_text = '#FFFFFF' if custom else WHITE
        if color == 'transparent': fg_text = WHITE
        return ctk.CTkButton(parent,text=text,command=command,fg_color=color,
                             text_color=fg_text, hover_color=('#C0D9ED' if self.theme_mode == 'Light' and not custom else '#346A98'),
                             font=ctk.CTkFont(family='Segoe UI',size=12,weight='bold'),
                             corner_radius=10,width=width,height=36,**kw)
    def panel(self,parent,**kwargs):return ctk.CTkFrame(parent,fg_color=PANEL,corner_radius=14,border_width=1,border_color=BORDER,**kwargs)
    def _table_colors(self):
        """Accessible executive table palette, coordinated with the active theme."""
        if self.theme_mode == 'Light':
            return dict(background='#F4F8FE', alternate='#EAF2FB',
                        foreground='#203850', heading='#243D5B', heading_ink='#F9FCFF',
                        heading_hover='#315778', border='#C9D9E9', selected='#246A9B',
                        selected_ink='#FFFFFF', scrollbar='#7196B7',
                        scrollbar_hover='#457DA7', track='#DFE9F5', editor='#FFFFFF')
        return dict(background='#14273B', alternate='#1B3149',
                    foreground='#E8F3FF', heading='#27415D', heading_ink='#F4F9FF',
                    heading_hover='#315575', border='#36506A', selected='#276E9D',
                    selected_ink='#FFFFFF', scrollbar='#557894',
                    scrollbar_hover='#7BA4C8', track='#162A40', editor='#203A54')

    def _style_table(self, name, rowheight=34, fontsize=10):
        """Style a Tk table with consistent headings, selection and themed scrollbars.

        Tk's native Treeview remains keyboard and screen-reader friendly; changing
        a ttk style does not rebuild the data model or replace its edit bindings.
        """
        colors=self._table_colors()
        style=ttk.Style(self)
        if style.theme_use()!='clam':
            style.theme_use('clam')
        table_style=f'{name}.Treeview'
        style.configure(table_style,
                        font=('Segoe UI',fontsize),rowheight=rowheight,
                        background=colors['background'],foreground=colors['foreground'],
                        fieldbackground=colors['background'],borderwidth=0,
                        relief='flat',lightcolor=colors['background'],darkcolor=colors['background'])
        style.map(table_style,
                  background=[('selected',colors['selected'])],
                  foreground=[('selected',colors['selected_ink'])])
        head_style=f'{table_style}.Heading'
        style.configure(head_style,
                        background=colors['heading'],foreground=colors['heading_ink'],
                        font=('Segoe UI',fontsize,'bold'),padding=(12,12),
                        borderwidth=0,relief='flat',
                        lightcolor=colors['heading'],darkcolor=colors['heading'])
        style.map(head_style,
                  background=[('active',colors['heading_hover']),
                              ('pressed',colors['heading_hover'])],
                  foreground=[('active',colors['heading_ink'])])
        for axis in ('Vertical','Horizontal'):
            name_scroll=f'Ops.{axis}.TScrollbar'
            style.configure(name_scroll,
                background=colors['scrollbar'],troughcolor=colors['track'],
                arrowcolor=colors['heading_ink'],bordercolor=colors['track'],
                lightcolor=colors['scrollbar'],darkcolor=colors['scrollbar'],
                gripcount=0,relief='flat',borderwidth=0,
                width=12)
            style.map(name_scroll,
                background=[('active',colors['scrollbar_hover']),
                            ('pressed',colors['scrollbar_hover'])])
        return table_style

    def _stripe_rows(self, tree):
        """Apply subtle alternating backgrounds without destroying status tags."""
        palette=self._table_colors()
        tree.tag_configure('ops_even',background=palette['background'])
        tree.tag_configure('ops_odd',background=palette['alternate'])
        for index, item in enumerate(tree.get_children('')):
            existing=tuple(t for t in tree.item(item,'tags') if t not in ('ops_even','ops_odd'))
            stripe='ops_odd' if index%2 else 'ops_even'
            if tree.item(item,'tags')!=(stripe,)+existing:
                tree.item(item,tags=(stripe,)+existing)
        # Hover is visual only: it does not change selection, values or editor bindings.
        if not getattr(tree,'_ops_hover_bound',False):
            tree._ops_hover_bound=True
            tree._ops_hover_id=None
            tree.tag_configure('ops_hover',background=('#D8E8F7' if self.theme_mode=='Light' else '#274866'))
            def clear_hover():
                old=tree._ops_hover_id
                if old and tree.exists(old):
                    tree.item(old,tags=tuple(x for x in tree.item(old,'tags') if x!='ops_hover'))
                tree._ops_hover_id=None
            def hover(event):
                row=tree.identify_row(event.y)
                if row==tree._ops_hover_id:return
                clear_hover()
                if row and tree.exists(row):
                    tree.item(row,tags=tuple(x for x in tree.item(row,'tags') if x!='ops_hover')+('ops_hover',))
                    tree._ops_hover_id=row
            tree.bind('<Motion>',hover,add='+')
            tree.bind('<Leave>',lambda _:clear_hover(),add='+')

    def _empty_state(self,parent,title,description,action_label=None,action=None,kind='neutral',compact=False):
        """Accessible distinction between missing data, pending data, and clear filters."""
        accent={'neutral':BLUE,'pending':AMBER,'success':TEAL,'error':RED}.get(kind,BLUE)
        container=self.panel(parent)
        container.pack(fill='x',pady=(7,16))
        header=ctk.CTkFrame(container,fg_color='transparent')
        header.pack(fill='x',padx=18,pady=(14 if compact else 20,5))
        glyph={'neutral':'○','pending':'◷','success':'✓','error':'!'} .get(kind,'○')
        self.label(header,glyph,25,accent,True).pack(side='left',padx=(0,12))
        self.label(header,title,15 if compact else 18,WHITE,True).pack(side='left')
        self.label(container,description,11 if compact else 12,MUTED,wraplength=940,
                   justify='left').pack(anchor='w',padx=20,pady=(0,12))
        if action_label and action:
            self.button(container,action_label,action,width=225).pack(anchor='w',padx=20,pady=(0,18))
        else:
            ctk.CTkFrame(container,height=5,fg_color='transparent').pack()
        return container

    def _show_loading_state(self,page):
        """Show an actual animated Tk loading state while navigation is queued."""
        for widget in self.content.winfo_children():widget.destroy()
        card=self.panel(self.content)
        card.pack(fill='x',pady=(24,14))
        self.label(card,'OPENING  /  '+page.upper(),12,BLUE,True).pack(anchor='w',padx=22,pady=(22,8))
        self.label(card,'Preparing your dashboard',19,WHITE,True).pack(anchor='w',padx=22)
        self.label(card,'Loading saved views and charts. Your operational records are unchanged.',
                   12,MUTED).pack(anchor='w',padx=22,pady=(5,16))
        self._loading_progress=ctk.CTkProgressBar(card,mode='indeterminate',height=7,
                                                    progress_color=BLUE,fg_color=BORDER)
        self._loading_progress.pack(fill='x',padx=22,pady=(0,24))
        self._loading_progress.start()

    def _finish_loading(self):
        if self._loading_progress is not None:
            try:self._loading_progress.stop()
            except Exception:pass
            self._loading_progress=None

    def _toggle_compact_sidebar(self):
        self._nav_compact=not self._nav_compact
        compact=self._nav_compact
        self.sidebar.configure(width=76 if compact else 228)
        self.brand_label.configure(text='◈' if compact else '◈   OPERATIONS')
        self.brand_subtitle.pack_forget()
        if not compact:self.brand_subtitle.pack(anchor='w',padx=22,pady=(0,32),before=next(iter(self.nav.values())))
        self.collection_label.pack_forget()
        if not compact:self.collection_label.pack(anchor='w',padx=22,pady=(35,8),before=self.sidebar_start)
        self.collection_hint.pack_forget()
        if not compact:self.collection_hint.pack(padx=16,pady=9,after=self.sidebar_stop)
        self.nav_toggle.configure(text='»' if compact else '«')
        for name,btn in self.nav.items():
            btn.configure(text=(self.nav_icons[name] if compact else
                         f'{self.nav_icons[name]}    {name}'),width=52 if compact else 190,
                         anchor='center' if compact else 'w')
            btn.pack_configure(padx=9 if compact else 17)
        self.sidebar_start.configure(text='▶' if compact else '▶  Start WhatsApp',
                                     width=52 if compact else 188)
        self.sidebar_stop.configure(text='■' if compact else '■  Stop monitoring',
                                    width=52 if compact else 188)
        self.brand_label.pack_configure(padx=18 if compact else 21)

    def _layout(self):
        self.grid_columnconfigure(1,weight=1);self.grid_rowconfigure(0,weight=1)
        side=ctk.CTkFrame(self,fg_color=NAV,corner_radius=0,width=228)
        self.sidebar=side
        side.grid(row=0,column=0,sticky='nsew');side.grid_propagate(False)
        self.nav_toggle=self.button(side,'«',self._toggle_compact_sidebar,width=35,color='transparent')
        self.nav_toggle.pack(anchor='e',padx=10,pady=(12,3))
        self.brand_label=self.label(side,'◈   OPERATIONS',19,BLUE,True)
        self.brand_label.pack(anchor='w',padx=21,pady=(10,4))
        self.brand_subtitle=self.label(side,'INTELLIGENCE  /  DESKTOP',10,MUTED,True)
        self.brand_subtitle.pack(anchor='w',padx=22,pady=(0,32))
        self.nav_icons={'Overview':'◈','Workforce':'▤','Executive':'▦','Exceptions':'✎',
                        'Safety & Alerts':'⚑','Reports':'▣','System Health':'◉','Settings':'⚙'}
        self.nav={}
        for name in self.nav_icons:
            b=self.button(side,f'{self.nav_icons[name]}    {name}',
                          lambda n=name:self._navigate(n),width=190,color='transparent',anchor='w')
            b.pack(padx=17,pady=5);self.nav[name]=b
        self.collection_label=self.label(side,'COLLECTION CONTROL',10,MUTED,True)
        self.collection_label.pack(anchor='w',padx=22,pady=(35,8))
        self.sidebar_start=self.button(side,'▶  Start WhatsApp',self._start,color='#166653',width=188)
        self.sidebar_start.pack(pady=5)
        self.sidebar_stop=self.button(side,'■  Stop monitoring',self._stop,color='#69424D',width=188)
        self.sidebar_stop.pack(pady=5)
        self.collection_hint=self.label(side,'Requires WhatsApp Web sign-in',10,MUTED,wraplength=180)
        self.collection_hint.pack(padx=16,pady=9)
        self.main=ctk.CTkFrame(self,fg_color=BG,corner_radius=0)
        self.main.grid(row=0,column=1,sticky='nsew');self.main.grid_columnconfigure(0,weight=1)
        bar=ctk.CTkFrame(self.main,fg_color='transparent')
        bar.grid(row=0,column=0,sticky='ew',padx=22,pady=(17,7))
        headings=ctk.CTkFrame(bar,fg_color='transparent')
        headings.pack(side='left',fill='x',expand=True)
        self.heading=self.label(headings,'Operational Overview',25,WHITE,True)
        self.heading.pack(anchor='w')
        self.page_context=self.label(headings,'OPERATIONS  /  LIVE INTELLIGENCE',10,MUTED,True)
        self.page_context.pack(anchor='w',pady=(2,0))
        self.connection_led=self.label(bar,'●  DISCONNECTED',11,RED,True)
        self.connection_led.pack(side='right',padx=8)
        self.button(bar,'☀  Light' if self.theme_mode=='Dark' else '☾  Dark',
                    self._toggle_theme,width=95).pack(side='right',padx=10)
        self._update_connection_led()
        self.content=ctk.CTkScrollableFrame(self.main,fg_color='transparent')
        self.content.grid(row=1,column=0,sticky='nsew',padx=20,pady=8)
        self.main.grid_rowconfigure(1,weight=1)
        foot=ctk.CTkFrame(self.main,fg_color=NAV,height=37,corner_radius=0)
        foot.grid(row=2,column=0,sticky='ew')
        self.label(foot,'●',12,TEAL,True).pack(side='left',padx=(15,7),pady=8)
        self.label(foot,'Desktop ready',10,MUTED,textvariable=self.status_text,
                   width=550,anchor='w').pack(side='left',padx=1,pady=7)
        self.label(foot,'LOCAL-FIRST  •  SQLITE',10,TEAL,True).pack(side='right',padx=16)
        if self._nav_compact:
            self._nav_compact=False
            self._toggle_compact_sidebar()

    def _update_connection_led(self):
        """Paint green only for an active, authenticated WhatsApp monitor."""
        if not hasattr(self, 'connection_led') or not self.connection_led.winfo_exists():
            return
        state = core.monitor_state()['status']
        connected = bool(self.monitor.active and state == 'Connected')
        label = '●  CONNECTED' if connected else ('●  CONNECTING' if self.monitor.active else '●  DISCONNECTED')
        color = TEAL if connected else RED
        if self.connection_led.cget('text') != label or self.connection_led.cget('text_color') != color:
            self.connection_led.configure(text=label, text_color=color)

    def _toggle_theme(self):
        self._set_theme('Light' if self.theme_mode == 'Dark' else 'Dark')

    def _set_theme(self, next_mode):
        """Persist the preference and rebuild only on a manual theme change.

        Live ingestion still updates labels and charts in place; no flicker.
        """
        if next_mode == self.theme_mode:
            return
        cfg = core.configuration()
        cfg['theme'] = next_mode
        try:
            core.save_configuration(cfg)
        except Exception as exc:
            messagebox.showerror('Theme settings', str(exc))
            return
        self.theme_mode = apply_palette(next_mode)
        old_page = self.page
        for widget in self.winfo_children():
            widget.destroy()
        self.configure(fg_color=BG)
        self._layout()
        self._navigate(old_page)

    def _navigate(self,page):
        if (self.page=='Exceptions' and page!='Exceptions'
                and getattr(self,'_exception_draft_dirty',False)):
            if not messagebox.askyesno('Unsaved staffing changes',
                    'Some group counts have not been saved. Discard these drafts and leave the page?'):
                return
            self._exception_draft_dirty=False
        self.page=page
        self.heading.configure(text={'Overview':'Operational Overview','Reports':'Reporting Centre',
            'Executive':'Executive Command Centre',
            'Workforce':'Workforce & Attendance','Safety & Alerts':'Safety & Risk Intelligence',
            'System Health':'System Health & Audit','Settings':'Local Settings',
            'Staffing Plans':'Staffing Planning','Exceptions':'Staffing Exceptions & Changes'}[page])
        selected='#D1E5F7' if self.theme_mode=='Light' else '#23496E'
        for name,btn in self.nav.items():
            btn.configure(fg_color=selected if name==page else 'transparent')
        # Schedule the expensive view creation to allow the loading indicator
        # to paint before Matplotlib and database work begins.
        self._nav_generation+=1
        generation=self._nav_generation
        if self._nav_render_job is not None:
            try:self.after_cancel(self._nav_render_job)
            except Exception:pass
        self._show_loading_state(page)
        self.status_text.set('Opening '+page+'…')
        self._nav_render_job=self.after(75,lambda:self._finish_navigation(page,generation))

    def _finish_navigation(self,page,generation):
        if generation!=self._nav_generation or page!=self.page:return
        self._nav_render_job=None
        try:
            self._render()
            with core.connect() as con:
                row=con.execute('SELECT COUNT(*),MAX(id) FROM reports').fetchone()
            self._last_sig=(tuple(row),core.exception_data_signature(),self.page)
            self.status_text.set(page+' ready · '+datetime.now().strftime('%H:%M:%S'))
        except Exception as exc:
            logging.exception('Unable to open page %s',page)
            for widget in self.content.winfo_children():widget.destroy()
            self._empty_state(self.content,'Unable to display '+page,
                    'The view could not be opened: '+str(exc),
                    'Try refreshing',lambda:self._navigate(page),kind='error')
            self.status_text.set('View error: '+str(exc))

    def _scope(self,parent,show_exports=True):
        row=ctk.CTkFrame(parent,fg_color='transparent');row.pack(fill='x',pady=(0,15))
        self.label(row,'Reporting period',12,MUTED).pack(side='left',padx=(0,8))
        ctk.CTkOptionMenu(row,values=['Today','Yesterday','7 days','14 days','30 days','90 days','All time','Custom'],variable=self.period,
                          command=lambda v:self._render(),width=115,fg_color=PANEL,text_color=WHITE,
                          button_color=('#B8D4ED' if self.theme_mode == 'Light' else '#254C72')).pack(side='left',padx=(0,18))
        self.label(row,'Team',12,MUTED).pack(side='left',padx=(0,8))
        values=['All Groups']+sorted(set(core.groups())|set(core.configuration()['groups']))
        if self.group.get() not in values:self.group.set('All Groups')
        ctk.CTkOptionMenu(row,values=values,variable=self.group,command=lambda v:self._render(),width=190,
                          fg_color=PANEL,text_color=WHITE,
                          button_color=('#B8D4ED' if self.theme_mode == 'Light' else '#254C72')).pack(side='left')
        self.button(row,'↻  Refresh',self._render,width=95).pack(side='right',padx=5)
        if show_exports:
            self.button(row,'Interactive HTML',self._html,width=130).pack(side='right',padx=5)
            self.button(row,'Export PDF',self._pdf,width=100).pack(side='right',padx=5)
        custom=ctk.CTkFrame(parent,fg_color='transparent');custom.pack(fill='x',pady=(0,12))
        self.label(custom,'From',11,MUTED).pack(side='left',padx=(0,4))
        ctk.CTkEntry(custom,textvariable=self.custom_start,placeholder_text='YYYY-MM-DD',width=110).pack(side='left',padx=(0,6))
        self.label(custom,'To',11,MUTED).pack(side='left',padx=(0,4))
        ctk.CTkEntry(custom,textvariable=self.custom_end,placeholder_text='YYYY-MM-DD',width=110).pack(side='left',padx=(0,7))
        self.button(custom,'Apply dates',self._apply_custom_dates,width=94).pack(side='left',padx=(0,10))
        return row

    def _apply_custom_dates(self):
        try:
            a=core.parse_date(self.custom_start.get())
            b=core.parse_date(self.custom_end.get())
            if a>b:raise ValueError('Start date must not be later than end date.')
        except ValueError as exc:
            messagebox.showerror('Invalid custom date range',str(exc)+'\nUse YYYY-MM-DD or DD/MM/YYYY.');return
        self.period.set('Custom');self._render()

    def _date_bounds(self):
        label=self.period.get()
        if label=='Custom':
            try:
                a=core.parse_date(self.custom_start.get());b=core.parse_date(self.custom_end.get())
                if a>b:return date(9999,1,1),date(1000,1,1)
                return a,b
            except ValueError:return date(9999,1,1),date(1000,1,1)
        # Today and Yesterday are exact local calendar days, not rolling 24-hour windows.
        if label=='Today':
            return date.today(), date.today()
        if label=='Yesterday':
            yesterday = date.today() - timedelta(days=1)
            return yesterday, yesterday
        if label=='All time':return None,None
        return date.today()-timedelta(days=int(label.split()[0])-1),date.today()

    def _date_range(self):return self._date_bounds()[0]

    def _selected_rows(self):
        a,b=self._date_bounds()
        return core.filtered(a,b,self.group.get())

    def _reset_view_filters(self):
        self.period.set('All time')
        self.group.set('All Groups')
        self._render()

    def _kpi(self,parent,title,value,sub,color=BLUE):
        p=self.panel(parent);p.pack(side='left',fill='both',expand=True,padx=5)
        ctk.CTkFrame(p,fg_color=color,height=4,corner_radius=3).pack(fill='x',padx=16,pady=(12,1))
        self.label(p,title.upper(),11,MUTED,True).pack(anchor='w',padx=16,pady=(11,5))
        value_label=self.label(p,str(value),30,color,True)
        value_label.pack(anchor='w',padx=16)
        sub_label=self.label(p,sub,11,MUTED)
        sub_label.pack(anchor='w',padx=16,pady=(0,14))
        return value_label,sub_label
    def _title(self,parent,name,detail=None,top_padding=18):
        self.label(parent,name,17,WHITE,True).pack(anchor='w',pady=(top_padding,4))
        if detail:self.label(parent,detail,11,MUTED).pack(anchor='w',pady=(0,7))

    def _render(self):
        """Rebuild only on navigation/filter changes, never on live-message events."""
        # Explicit page/filter navigation may rebuild; automatic updates never do.
        self._finish_loading()
        self._live_widgets={}
        if hasattr(self,'page_context') and self.page_context.winfo_exists():
            if self.page in ('Overview','Executive','Workforce','Safety & Alerts','Reports'):
                detail=f'{self.period.get().upper()}  /  {self.group.get().upper()}'
            elif self.page=='Exceptions':
                detail=f'NIGHT SERVICE  /  {self.exception_day.get()}'
            else:
                detail='OPERATIONS  /  CONFIGURATION & CONTROL'
            self.page_context.configure(text=detail)
        if self.exception_session is not None:
            try:self.exception_session=core.app_session_info(self.exception_session['token'])
            except PermissionError:self.exception_session=None
        for w in self.content.winfo_children():w.destroy()
        self._chart=None
        if self.page in ('Overview','Executive','Workforce','Safety & Alerts','Reports'):
            self._scope(self.content,show_exports=self.page not in ('Overview','Reports'))
            self._rows=self._selected_rows()
        if self.page=='Overview':self._overview()
        elif self.page=='Reports':self._reports_centre()
        elif self.page=='Executive':self._executive()
        elif self.page=='Workforce':self._workforce()
        elif self.page=='Safety & Alerts':self._safety()
        elif self.page=='Exceptions':self._exceptions()
        elif self.page=='System Health':self._health()
        elif self.page=='Settings':
            if self.exception_session is None:
                self._title(self.content,'Settings access',
                            'Sign in as an administrator to manage accounts, groups and staffing plans.')
                self._exception_login()
            elif self.exception_session.get('role')!='admin':
                self._title(self.content,'Administrator access required',
                            'Your operator account can enter changes but cannot manage users or settings.')
                self.button(self.content,'Sign in as administrator',self._settings_switch_account,width=215)
            else:self._settings()
        elif self.page=='Staffing Plans':
            if self.exception_session is None or self.exception_session.get('role')!='admin':
                self._title(self.content,'Administrator access required',
                            'Sign in to Exceptions as an administrator to manage staffing plans.')
                self.button(self.content,'Go to Exceptions sign-in',lambda:self._navigate('Exceptions'),width=220)
            else:self._staffing_plan_settings()

    # ------------------------------------------------------------------
    # OPERATIONAL OVERVIEW - read-only presentation of existing v3.0.0 KPIs
    # ------------------------------------------------------------------
    def _overview(self):
        # Keep the overview focused on the data; the page title and global filters
        # already identify the section, so no second Consolidated View hero card.
        # Keep the heading immediately below the date filters. The empty-data
        # notice is created after the KPI summary, not above the heading.
        self._title(self.content,'Operational snapshot',
                    'WhatsApp attendance and safety indicators, alongside independently entered staffing-change counts.',
                    top_padding=5)
        row1=ctk.CTkFrame(self.content,fg_color='transparent');row1.pack(fill='x',pady=(0,9))
        self._live_widgets['ov_reports']=self._kpi(row1,'Captured reports','-','WhatsApp records',BLUE)
        self._live_widgets['ov_teams']=self._kpi(row1,'Groups reporting','-','Unique groups in scope',TEAL)
        self._live_widgets['ov_near']=self._kpi(row1,'Near misses','-','Reported message totals',RED)
        self._live_widgets['ov_late']=self._kpi(row1,'Late reported','-','Reported attendance exceptions',AMBER)
        row2=ctk.CTkFrame(self.content,fg_color='transparent');row2.pack(fill='x',pady=(0,8))
        self._live_widgets['ov_before']=self._kpi(row2,'Changes before 15:00','-','Known operator entries',BLUE)
        self._live_widgets['ov_after']=self._kpi(row2,'Changes after 15:00','-','Known operator entries',TEAL)
        self._live_widgets['ov_no']=self._kpi(row2,'No shows','-','Explicit WhatsApp count only',AMBER)
        self._live_widgets['ov_short']=self._kpi(row2,'Staff shortages','-','Actual attendance below plan',RED)
        self._live_widgets['ov_status']=self.label(self.content,'Loading operational summary...',11,MUTED)
        self._live_widgets['ov_status'].pack(anchor='w',padx=8,pady=(2,8))
        # Reserve space for an empty/pending notice only when necessary. This
        # maintains a compact gap between filters and Operational snapshot.
        empty_holder=ctk.CTkFrame(self.content,fg_color='transparent')
        empty_holder.pack(fill='x')
        self._live_widgets['ov_empty_holder']=empty_holder

        charts=ctk.CTkFrame(self.content,fg_color='transparent');charts.pack(fill='x',pady=(12,2))
        mix=self.panel(charts);mix.pack(side='left',fill='both',expand=True,padx=(4,8))
        self.label(mix,'WORKFORCE ALLOCATION',13,WHITE,True).pack(anchor='w',padx=16,pady=(13,2))
        self.label(mix,'Reported role totals • shares of attendance observations',10,MUTED).pack(anchor='w',padx=16)
        self._live_widgets['ov_mix']=self._overview_canvas(mix,(5.0,3.3))
        ex=self.panel(charts);ex.pack(side='left',fill='both',expand=True,padx=(5,4))
        self.label(ex,'CHANGES, NO-SHOWS & SHORTAGES',13,WHITE,True).pack(anchor='w',padx=16,pady=(13,2))
        self.label(ex,'Four independent event categories • top eight groups',10,MUTED).pack(anchor='w',padx=16)
        self._live_widgets['ov_ex_chart']=self._overview_canvas(ex,(6.0,3.3))

        strip=ctk.CTkFrame(self.content,fg_color='transparent');strip.pack(fill='x',pady=(10,0))
        chart=self.panel(strip);chart.pack(side='left',fill='both',expand=True,padx=(4,8))
        self.label(chart,'NEAR-MISS REPORTING TREND',13,WHITE,True).pack(anchor='w',padx=16,pady=(12,0))
        self._live_widgets['ov_trend']=self._overview_canvas(chart,(6.0,2.9))
        attention=self.panel(strip);attention.pack(side='left',fill='both',expand=True,padx=(5,4))
        self.label(attention,'MANAGEMENT ATTENTION',13,WHITE,True).pack(anchor='w',padx=16,pady=(13,8))
        self._live_widgets['ov_attention']=[]
        for _ in range(4):
            lab=self.label(attention,'Checking current records…',12,MUTED,wraplength=470,justify='left')
            lab.pack(anchor='w',padx=17,pady=8)
            self._live_widgets['ov_attention'].append(lab)
        self.button(attention,'Review safety alerts',lambda:self._navigate('Safety & Alerts'),width=195).pack(anchor='w',padx=17,pady=(12,16))

        self._title(self.content,'Latest WhatsApp evidence','Double-click an entry to inspect the original captured message. Staffing changes are recorded separately in Exceptions.')
        frame=self.panel(self.content);frame.pack(fill='x',pady=(0,13))
        self._style_table('OverviewRecent',rowheight=34,fontsize=10)
        cols=('Date/time','Group','Sender','People present','Near misses','Late')
        tbl=ttk.Treeview(frame,columns=cols,show='headings',height=7,style='OverviewRecent.Treeview')
        for col in cols:
            tbl.heading(col,text=col)
            tbl.column(col,anchor='w',width=185 if col in ('Date/time','Group','Sender') else 115)
        tbl.grid(row=0,column=0,sticky='nsew',padx=(12,0),pady=(12,0))
        frame.grid_columnconfigure(0,weight=1)
        bar=ttk.Scrollbar(frame,orient='vertical',command=tbl.yview,style='Ops.Vertical.TScrollbar')
        bar.grid(row=0,column=1,sticky='ns',padx=(0,12),pady=(12,0))
        scroll=ttk.Scrollbar(frame,orient='horizontal',command=tbl.xview,style='Ops.Horizontal.TScrollbar')
        scroll.grid(row=1,column=0,sticky='ew',padx=(12,0),pady=(0,12))
        tbl.configure(yscrollcommand=bar.set,xscrollcommand=scroll.set)
        self._live_widgets['ov_recent']=tbl
        self._live_widgets['ov_mapping']={}
        def show_evidence(_):
            key=tbl.focus()
            item=self._live_widgets.get('ov_mapping',{}).get(key)
            if item:
                messagebox.showinfo('Original WhatsApp evidence',
                    f"Time: {item['event_at']}\nGroup: {item['group_name']}\nSender: {item.get('sender') or '-'}\n\n{item.get('message') or ''}")
        tbl.bind('<Double-1>',show_evidence)
        self._update_overview_in_place()

    def _overview_canvas(self,parent,dimensions):
        fig=Figure(figsize=dimensions,dpi=85,facecolor=PANEL)
        ax=fig.add_subplot(111)
        ax.set_facecolor(PANEL)
        canvas=FigureCanvasTkAgg(fig,master=parent)
        canvas.get_tk_widget().pack(fill='both',expand=True,padx=12,pady=(3,12))
        return fig,ax,canvas

    def _draw_overview_workforce(self,ax,rows):
        ax.clear();ax.set_facecolor(PANEL)
        values=core.workforce_totals(rows)
        core.original_style_pie(ax,['T002/T003','PWT','SPL','PL'],values,
                                ['#38BDF8','#2DD4BF','#A78BFA','#F4B35A'],
                                dark=self.theme_mode=='Dark')

    def _draw_overview_exceptions(self,ax,entries):
        ax.clear();ax.set_facecolor(PANEL)
        groups=core.workforce_exception_chart_rows(entries,limit=8)
        if not groups:
            ax.text(.5,.5,'No staffing exceptions in this selection',ha='center',va='center',
                    color=MUTED,transform=ax.transAxes,fontsize=11)
            ax.set_xticks([]);ax.set_yticks([])
            return
        y=list(range(len(groups)))
        left=[0]*len(y)
        for key,title,color in [('before','Before 15:00','#2980B9'),('after','After 15:00','#16A085'),
                                ('no_shows','No shows','#9B59B6'),('shortage','Shortages','#E74C3C')]:
            values=[g[key] for g in groups]
            ax.barh(y,values,left=left,height=.61,label=title,color=color)
            left=[a+b for a,b in zip(left,values)]
        ax.set_yticks(y,labels=[g['group'] for g in groups])
        ax.invert_yaxis();ax.tick_params(colors=WHITE,labelsize=8)
        ax.xaxis.grid(True,color=BORDER,alpha=.35);ax.set_axisbelow(True)
        ax.legend(loc='lower center',bbox_to_anchor=(.5,1.01),ncol=2,
                  fontsize=8,frameon=False,labelcolor=WHITE)
        for edge in ('top','right'):ax.spines[edge].set_visible(False)
        ax.spines['left'].set_color(BORDER);ax.spines['bottom'].set_color(BORDER)
        ax.set_xlabel('Event totals (not unique people)',color=MUTED,fontsize=8)

    def _update_overview_in_place(self):
        if self.page!='Overview' or 'ov_reports' not in self._live_widgets:return
        rows=self._selected_rows()
        self._rows=rows
        a,b=self._date_bounds()
        ex=core.exception_dashboard(a,b,self.group.get())
        summary=core.summary(rows,core.configuration()['groups'])
        empty_holder=self._live_widgets.get('ov_empty_holder')
        if empty_holder is not None and empty_holder.winfo_exists():
            for child in empty_holder.winfo_children():child.destroy()
            if not rows and not ex['rows']:
                self._empty_state(empty_holder,'No operational data for the selected period',
                   'No WhatsApp attendance or saved staffing-change entries match these filters. '
                   'Select another period or group to view operational KPIs.',
                   'Show all dates & groups',self._reset_view_filters,kind='neutral',compact=True)
            elif not rows:
                self._empty_state(empty_holder,'Staffing changes recorded - attendance pending',
                   'Operator changes may have been saved, but no matching final WhatsApp attendance has arrived. '
                   'Shortages cannot be confirmed from change counts alone.',kind='pending',compact=True)
        safety_ok,total,safety_pct=core.safety_completeness(rows)
        self._update_kpi_in_place('ov_reports',f'{len(rows):,}',
                                  f'{safety_ok} report(s) with complete safety fields')
        self._update_kpi_in_place('ov_teams',str(summary['teams']),
                                  f"{len(summary['missing_groups'])} configured groups not represented")
        self._update_kpi_in_place('ov_near',str(summary['near_miss']),'Reported values, not verified distinct incidents')
        self._update_kpi_in_place('ov_late',str(summary['late']),'Reported attendance exceptions')
        self._update_kpi_in_place('ov_before',str(ex['changes_before']) if ex['changes_before_known'] else '-',
                                  f"{ex['changes_before_known']} entry/entries")
        self._update_kpi_in_place('ov_after',str(ex['changes_after']) if ex['changes_after_known'] else '-',
                                  f"{ex['changes_after_known']} entry/entries")
        self._update_kpi_in_place('ov_no',str(ex['no_shows']) if ex['no_shows_known'] else '-',
                                  f"{ex['no_shows_known']} explicit WhatsApp count(s)")
        self._update_kpi_in_place('ov_short',str(ex['shortage']) if ex['verified'] else '-',
                                  f"{ex['verified']} attendance calculation(s); {ex['pending']} pending")
        status=(f"{ex['verified']} staffing records with attendance • {ex['pending']} pending • "
                f"{ex['awaiting_whatsapp']} awaiting WhatsApp • "
                f"Safety fields: {safety_pct:.0f}% complete" if total else
                f"{ex['verified']} staffing records with attendance • {ex['pending']} pending • No WhatsApp reports in this view")
        self._configure_text(self._live_widgets.get('ov_status'),status)
        messages=[(RED,f"{len(core.alerts('Open'))} open alerts (all dates)"),
                  (AMBER,f"{ex['pending']} exception record(s) still awaiting attendance/plan"),
                  (BLUE,f"{len(summary['missing_groups'])} configured group(s) not represented"),
                  (TEAL,f"Last WhatsApp capture: {summary['last_event'][:16] if summary['last_event'] else 'No matching reports'}")]
        for label,(col,txt) in zip(self._live_widgets.get('ov_attention',[]),messages):
            self._configure_text(label,'●  '+txt)
            if label.winfo_exists():label.configure(text_color=col)
        for key,fn,data in [('ov_mix',self._draw_overview_workforce,rows),
                            ('ov_ex_chart',self._draw_overview_exceptions,ex['rows']),
                            ('ov_trend',lambda ax,rs:core.plot_near_miss_calendar(ax,rs,dark=self.theme_mode=='Dark'),rows)]:
            if key in self._live_widgets:
                fig,ax,canvas=self._live_widgets[key]
                fn(ax,data)
                # Some plotting helpers call ax.clear(), restoring Matplotlib's
                # default white axes face colour. Reinstate the active CTk theme
                # after every redraw, including empty charts and live refreshes.
                fig.set_facecolor(PANEL)
                ax.set_facecolor(PANEL)
                fig.tight_layout(pad=1.8)
                if key=='ov_ex_chart':fig.subplots_adjust(left=.24,top=.79,bottom=.20)
                canvas.draw_idle()
        tree=self._live_widgets.get('ov_recent')
        if tree is not None and tree.winfo_exists():
            mapping=self._live_widgets['ov_mapping'];mapping.clear()
            for old in tree.get_children():tree.delete(old)
            for r in rows[:12]:
                role_counts=[r.get(key) for key in ('t002_t003','pwt','spl','pl')]
                headcount=sum(int(v or 0) for v in role_counts) if any(v is not None for v in role_counts) else '-'
                values=(str(r['event_at']).replace('T',' ')[:16],r['group_name'],r.get('sender') or '-',
                        headcount,'-' if r.get('near_miss') is None else r['near_miss'],
                        '-' if r.get('late') is None else r['late'])
                item=tree.insert('','end',values=values);mapping[item]=r
            self._stripe_rows(tree)

    # ------------------------------------------------------------------
    # REPORTING CENTRE - existing PDF/HTML/CSV/XLSX export functions
    # ------------------------------------------------------------------
    def _report_catalog_path(self):
        folder=core.DATA/'reports'
        folder.mkdir(parents=True,exist_ok=True)
        return folder/'report_catalog.json'

    def _register_local_report(self,path):
        """Store display metadata only; never copy or modify exported reports."""
        report=Path(path).expanduser().resolve()
        if not report.is_file():return
        index=self._report_catalog_path()
        try:
            records=json.loads(index.read_text(encoding='utf-8')) if index.exists() else []
            if not isinstance(records,list):records=[]
        except (OSError,ValueError):
            records=[]
        records=[r for r in records if isinstance(r,dict) and r.get('path')!=str(report)]
        records.append({'path':str(report),'created':datetime.now().isoformat(timespec='seconds'),
                        'scope':f'{self.group.get()} / {self.period.get()}'})
        temp=index.with_suffix('.tmp')
        temp.write_text(json.dumps(records[-1000:],indent=2),encoding='utf-8')
        temp.replace(index)

    def _report_catalog(self):
        """Combine built-in automatic report folder with existing local exports."""
        folder=self._report_catalog_path().parent
        try: indexed=json.loads((folder/'report_catalog.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):indexed=[]
        if not isinstance(indexed,list):indexed=[]
        source_paths={}
        for item in indexed:
            if isinstance(item,dict) and isinstance(item.get('path'),str):
                source_paths[item['path']]=item.get('scope','Saved export')
        for ext in ('*.pdf','*.html','*.xlsx','*.csv'):
            for path in folder.glob(ext):source_paths.setdefault(str(path.resolve()),'Automatic / report library')
        found=[]
        for raw_path,scope in source_paths.items():
            try:
                path=Path(raw_path).expanduser().resolve()
                if not path.is_file() or path.suffix.lower() not in ('.pdf','.html','.xlsx','.csv'):continue
                info=path.stat()
                found.append({'path':path,'name':path.name,'type':path.suffix.lower().lstrip('.').upper(),
                              'scope':str(scope),'size':info.st_size,'modified':info.st_mtime})
            except OSError:continue
        return sorted(found,key=lambda item:(-item['modified'],item['name'].lower()))

    @staticmethod
    def _open_local_report(path):
        path=Path(path).expanduser().resolve()
        if not path.is_file():raise FileNotFoundError(f'This report no longer exists: {path}')
        if path.suffix.lower() not in ('.pdf','.html','.csv','.xlsx'):
            raise ValueError('Unsupported report file type.')
        if sys.platform.startswith('win'):
            os.startfile(str(path))
        elif sys.platform=='darwin':
            subprocess.Popen(['open',str(path)])
        else:
            subprocess.Popen(['xdg-open',str(path)])

    def _generate_library_export(self,format_name):
        """Generate an additional copy in the Reports library; use v3.0.0 exports."""
        suffix={'PDF':'pdf','HTML':'html','Excel':'xlsx','CSV':'csv'}[format_name]
        a,b=self._date_bounds()
        group=self.group.get()
        slug=re.sub(r'[^A-Za-z0-9_-]+','_',group).strip('_')[:32] or 'all_groups'
        stamp=datetime.now().strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:5]
        output=self._report_catalog_path().parent/f'operational_{slug}_{stamp}.{suffix}'
        self.status_text.set(f'Generating {format_name} for {group}…')
        try:
            rows=self._selected_rows()
            if format_name in ('PDF','HTML'):
                ex=core.exception_dashboard(a,b,group)['rows']
                fn=core.export_pdf if format_name=='PDF' else core.export_interactive_html
                fn(output,rows,scope=f'{group} / {self.period.get()}',exception_rows=ex)
            elif format_name=='Excel':core.export_excel(output,rows)
            else:core.export_csv(output,rows)
            self._register_local_report(output)
            self.status_text.set(f'{format_name} created • {output.name}')
            callback=self._live_widgets.get('report_library_refresh')
            if callback and self.page=='Reports':callback()
            messagebox.showinfo('Report generated',
                f'{format_name} saved in the report library:\n{output}\n\nOpen it using the report table below.')
        except Exception as exc:
            logging.exception('Report generation error')
            messagebox.showerror('Report generation failed',str(exc))

    def _reports_centre(self):
        hero=self.panel(self.content);hero.pack(fill='x',pady=(0,16))
        self.label(hero,'▣  REPORTING CENTRE',12,BLUE,True).pack(anchor='w',padx=20,pady=(17,5))
        self.label(hero,'One place for executive packs and operational extracts',19,WHITE,True).pack(anchor='w',padx=20)
        self.label(hero,'Use the reporting filters above. PDF and HTML include the existing v3.0.0 charts, staffing exceptions and evidence. '
                        'Excel and CSV contain filtered WhatsApp records. Files remain local to this computer.',
                   11,MUTED,wraplength=1020,justify='left').pack(anchor='w',padx=20,pady=(6,17))
        self._title(self.content,'Create a report','Generated reports are saved under data/reports and appear in the library below.')
        row=ctk.CTkFrame(self.content,fg_color='transparent');row.pack(fill='x',pady=(0,12))
        for title,short,description,color in [
            ('Executive PDF','PDF','Board-ready charts and staffing detail',BLUE),
            ('Interactive HTML','HTML','Self-contained interactive charts and filters',TEAL),
            ('Excel extract','Excel','Filtered WhatsApp data',AMBER),
            ('CSV extract','CSV','Filtered WhatsApp data',BLUE)]:
            tile=self.panel(row);tile.pack(side='left',fill='both',expand=True,padx=5)
            self.label(tile,title,14,WHITE,True).pack(anchor='w',padx=14,pady=(13,4))
            self.label(tile,description,10,MUTED,wraplength=205,justify='left').pack(anchor='w',padx=14,pady=(0,10))
            self.button(tile,'Generate '+short,lambda x=short:self._generate_library_export(x),
                        width=145).pack(anchor='w',padx=14,pady=(0,14))
        self._title(self.content,'Report library','Double-click to open a file in its default desktop application. The library also lists previous exports that are still accessible.')
        searchbar=ctk.CTkFrame(self.content,fg_color='transparent');searchbar.pack(fill='x',pady=(2,9))
        search=ctk.StringVar(value='')
        category=ctk.StringVar(value='All formats')
        box=ctk.CTkEntry(searchbar,textvariable=search,width=310,placeholder_text='Search report names or scopes…')
        box.pack(side='left',padx=(0,10))
        option=ctk.CTkOptionMenu(searchbar,values=['All formats','PDF','HTML','XLSX','CSV'],variable=category,
                                 width=135,fg_color=PANEL,text_color=WHITE,
                                 button_color=('#B8D4ED' if self.theme_mode=='Light' else '#254C72'))
        option.pack(side='left')
        self._live_widgets['report_catalog_info']=self.label(searchbar,'',11,MUTED)
        self._live_widgets['report_catalog_info'].pack(side='right',padx=7)
        frame=self.panel(self.content);frame.pack(fill='x',pady=(0,9))
        self._style_table('ReportLibrary',rowheight=35,fontsize=10)
        columns=('file','kind','scope','modified','size')
        table=ttk.Treeview(frame,columns=columns,show='headings',height=12,style='ReportLibrary.Treeview')
        for key,label,width in [('file','File name',290),('kind','Format',80),('scope','Saved scope',240),
                                 ('modified','Last modified',160),('size','Size',100)]:
            table.heading(key,text=label);table.column(key,width=width,minwidth=65,anchor='w')
        table.grid(row=0,column=0,sticky='nsew',padx=(12,0),pady=(12,0));frame.grid_columnconfigure(0,weight=1)
        vs=ttk.Scrollbar(frame,orient='vertical',command=table.yview,style='Ops.Vertical.TScrollbar')
        vs.grid(row=0,column=1,sticky='ns',padx=(0,12),pady=(12,0))
        hs=ttk.Scrollbar(frame,orient='horizontal',command=table.xview,style='Ops.Horizontal.TScrollbar')
        hs.grid(row=1,column=0,sticky='ew',padx=(12,0),pady=(0,12))
        table.configure(yscrollcommand=vs.set,xscrollcommand=hs.set)
        empty_library=ctk.CTkFrame(self.content,fg_color='transparent')
        empty_library.pack(fill='x',pady=(0,4))
        mapping={}
        def selected():
            key=table.focus()
            if not key or key not in mapping:
                messagebox.showinfo('Select a report','Select a report from the library first.')
                return None
            return mapping[key]['path']
        def open_report(event=None):
            path=selected()
            if path is None:return
            try:self._open_local_report(path)
            except Exception as exc:messagebox.showerror('Unable to open report',str(exc))
        def open_folder():
            try:self._open_local_report_folder()
            except Exception as exc:messagebox.showerror('Unable to open report folder',str(exc))
        def refresh(*_):
            query=search.get().strip().casefold()
            wanted=category.get()
            records=self._report_catalog()
            visible=[r for r in records if (wanted=='All formats' or r['type']==wanted)
                     and (not query or query in r['name'].casefold() or query in r['scope'].casefold())]
            for old in table.get_children():table.delete(old)
            mapping.clear()
            for r in visible:
                values=(r['name'],r['type'],r['scope'],
                        datetime.fromtimestamp(r['modified']).strftime('%d %b %Y  %H:%M'),
                        f"{r['size']/1024:,.1f} KB")
                key=table.insert('','end',values=values);mapping[key]=r
            self._stripe_rows(table)
            for child in empty_library.winfo_children():child.destroy()
            if not records:
                self._empty_state(empty_library,'No reports saved yet',
                    'Generate your first executive PDF or interactive HTML above. Files generated later will appear automatically after refresh.',
                    'Generate executive PDF',lambda:self._generate_library_export('PDF'),kind='neutral',compact=True)
            elif not visible:
                self._empty_state(empty_library,'No reports match the current search',
                    'Adjust the search text or format filter; saved reports have not been removed.',
                    'Clear search',lambda:(search.set(''),category.set('All formats'),refresh()),kind='neutral',compact=True)
            self._configure_text(self._live_widgets.get('report_catalog_info'),
                                 f'{len(visible)} shown / {len(records)} available')
            if not records:
                self.status_text.set('No saved reports found. Generate a report above to get started.')
        search.trace_add('write',refresh)
        option.configure(command=lambda _:refresh())
        table.bind('<Double-1>',open_report)
        controls=ctk.CTkFrame(self.content,fg_color='transparent');controls.pack(fill='x',pady=(0,13))
        self.button(controls,'Open selected',open_report,width=145).pack(side='left',padx=(0,8))
        self.button(controls,'Refresh library',refresh,width=145).pack(side='left',padx=(0,8))
        self.button(controls,'Open reports folder',open_folder,width=175).pack(side='left',padx=(0,8))
        def add_existing():
            selected_files=filedialog.askopenfilenames(title='Add existing reports to the local library',
                filetypes=[('Report documents','*.pdf *.html *.xlsx *.csv'),('All files','*.*')])
            if not selected_files:return
            count=0
            for path in selected_files:
                if Path(path).suffix.lower() not in ('.pdf','.html','.xlsx','.csv'):continue
                try:self._register_local_report(path);count+=1
                except Exception as exc:logging.warning('Could not index report %s: %s',path,exc)
            refresh()
            self.status_text.set(f'{count} existing report(s) added to the library')
        self.button(controls,'Add existing files',add_existing,width=155).pack(side='left')
        self.label(controls,'Reports may contain confidential operational information; share only with authorised users.',
                   10,AMBER,wraplength=410).pack(side='right',padx=5)
        self._live_widgets['report_library_refresh']=refresh
        refresh()

    def _open_local_report_folder(self):
        folder=self._report_catalog_path().parent
        if sys.platform.startswith('win'):os.startfile(str(folder))
        elif sys.platform=='darwin':subprocess.Popen(['open',str(folder)])
        else:subprocess.Popen(['xdg-open',str(folder)])

    def _executive(self):
        if not self._rows:
            self._live_widgets['executive_empty']=self._empty_state(self.content,
                'No WhatsApp attendance in this view',
                'No WhatsApp reports match this date and group selection. Existing staffing-change '
                'entries can still be displayed below. Choose a wider period or another group.',
                'Show all dates & groups',self._reset_view_filters,kind='pending',compact=True)
        s=core.summary(self._rows,core.configuration()['groups'])
        prior=core.compare(self._rows,*self._date_bounds())
        with core.connect() as con:
            all_count=con.execute('SELECT COUNT(*) FROM reports').fetchone()[0]
        self._live_widgets['counter']=self.label(self.content,
            f'LIVE DATA  •  {s["reports"]:,} displayed / {all_count:,} in database  •  Period: {self.period.get()}  •  Team: {self.group.get()}',
            12,TEAL if s['reports'] else AMBER)
        self._live_widgets['counter'].pack(anchor='w',pady=(0,14))
        k=ctk.CTkFrame(self.content,fg_color='transparent');k.pack(fill='x')
        note=(f"{prior['reports']:+.1f}% vs previous period" if prior and prior['reports'] is not None else 'Comparison unavailable')
        self._live_widgets['report_kpi']=self._kpi(k,'Captured reports',f"{s['reports']:,}",note)
        self._live_widgets['team_kpi']=self._kpi(k,'Teams reporting',s['teams'],f"{len(s['missing_groups'])} configured team(s) not represented",TEAL)
        self._live_widgets['near_kpi']=self._kpi(k,'Near-miss reported',s['near_miss'],'Unverified message values',RED)
        complete,total,pct=core.safety_completeness(self._rows)
        self._live_widgets['complete_kpi']=self._kpi(k,'Safety data completeness',f'{pct:.1f}%' if pct is not None else '-',f'{complete} of {total} reports have both fields',TEAL)
        second=ctk.CTkFrame(self.content,fg_color='transparent');second.pack(fill='both',pady=(18,0))
        chart=self.panel(second);chart.pack(side='left',fill='both',expand=True,padx=(5,8))
        self.label(chart,'NEAR-MISS REPORTS - DAILY TREND',13,WHITE,True).pack(anchor='w',padx=14,pady=(12,0))
        self._safety_chart(chart,self._rows)
        distribution=self.panel(second,width=290);distribution.pack(side='left',fill='both',padx=5)
        self.label(distribution,'NEAR-MISS SHARE BY GANG',12,WHITE,True).pack(anchor='w',padx=10,pady=(12,2))
        per_gang=core.near_miss_by_group(self._rows)
        self._pie_chart(distribution,list(per_gang),list(per_gang.values()),
                        ['#d35400','#2980b9','#27ae60','#8e44ad','#f39c12'] * max(1,len(per_gang)),key='safety_pie')
        attention=self.panel(second,width=285);attention.pack(side='right',fill='both',padx=5)
        self.label(attention,'MANAGEMENT ATTENTION',13,WHITE,True).pack(anchor='w',padx=15,pady=(13,10))
        issues=[(RED,f"{len(core.alerts('Open'))} open alerts"),
                (AMBER,f"{s['missing_safety']} reports missing safety values" if s['missing_safety'] else 'Safety values complete in received reports'),
                (BLUE,f"{len(s['missing_groups'])} teams without a captured report"),
                (TEAL,f"Last captured: {s['last_event'][:16] if s['last_event'] else 'No records'}")]
        self._live_widgets['issues']=[]
        for color,text in issues:
            lbl=self.label(attention,'●  '+text,12,color,wraplength=260,justify='left')
            lbl.pack(anchor='w',padx=16,pady=9)
            self._live_widgets['issues'].append(lbl)
        self.button(attention,'Review alerts',lambda:self._navigate('Safety & Alerts'),width=220).pack(padx=15,pady=(15,20))
        # Forecast models remain available elsewhere; the Executive tab intentionally
        # contains only actual reports, safety indicators and recent activity.
        self._exception_executive_panel()
        self._report_table(self.content,self._rows[:80],limit=80,title='Recent WhatsApp report evidence')

    def _pie_chart(self,parent,labels,values,colors,key=None):
        fig=Figure(figsize=(3.4,3.2),dpi=85,facecolor=PANEL)
        ax=fig.add_subplot(111);ax.set_facecolor(PANEL)
        core.original_style_pie(ax,labels,values,colors,dark=self.theme_mode == 'Dark')
        ax.set_facecolor(PANEL)
        fig.tight_layout(pad=1.2)
        canvas=FigureCanvasTkAgg(fig,master=parent);canvas.draw()
        canvas.get_tk_widget().pack(fill='both',expand=True,padx=5,pady=5)
        if key:self._live_widgets[key]=(fig,ax,canvas)
        return fig,ax,canvas

    def _safety_chart(self,parent,rows):
        fig=Figure(figsize=(6.5,3.2),dpi=85,facecolor=PANEL)
        ax=fig.add_subplot(111);ax.set_facecolor(PANEL)
        self._draw_safety_bars(ax,rows)
        fig.tight_layout(pad=1.5)
        canvas=FigureCanvasTkAgg(fig,master=parent);canvas.draw()
        canvas.get_tk_widget().pack(fill='both',expand=True,padx=6,pady=6)
        self._live_widgets['safety_chart']=(fig,ax,canvas)

    @staticmethod
    def _draw_safety_bars(ax,rows):
        """Reported dates only: dynamic daily near-miss chart with DD-MM labels."""
        core.plot_near_miss_calendar(ax, rows, dark=True)
        ax.set_facecolor(PANEL)

    def _gang_attendance_chart(self,parent,rows):
        """Horizontal STACKED bars by group, including all monitored groups.

        Each segment represents a reported quantity (T002/T003, PWT, SPL,
        PL or Late), not a unique person count. Late is an attendance
        exception: stacking it enables compact comparison across 40+ gangs,
        but the resulting bar length must NOT be interpreted as headcount.

        The parent is a vertically scrollable CTkScrollableFrame, so the
        canvas height expands with the number of gangs instead of dropping
        groups or making their labels unreadable.
        """
        groups = core.gang_attendance(rows)
        holder = self.panel(parent)
        holder.pack(fill='x', pady=(0, 12))
        self.label(holder, 'GANG-WISE ATTENDANCE - STACKED HORIZONTAL COMPARISON',
                   13, WHITE, True).pack(anchor='w', padx=14, pady=(12, 0))
        self.label(holder,
                   'Segments show reported values; Late is an exception count, not unique headcount.',
                   11, MUTED).pack(anchor='w', padx=14, pady=(2, 8))
        names = sorted(groups, key=str.casefold)
        if not names:
            self.label(holder,'○  No gang attendance received for these filters',
                       13,WHITE,True).pack(anchor='w',padx=16,pady=(8,5))
            self.label(holder,'Attendance comparisons will appear when a matching WhatsApp report is captured.',
                       11,MUTED).pack(anchor='w',padx=16,pady=(0,18))
            return
        # Give every gang a readable row while letting the page scroll vertically.
        # The width is sized *by Tk's actual panel width*, not a hard-coded 10-inch
        # Matplotlib figure. This prevents clipping and unused space on resize.
        row_pixels = 28
        height_px = max(340, 145 + row_pixels * max(1, len(names)))
        fig = Figure(figsize=(9, height_px / 100), dpi=100, facecolor=PANEL)
        ax = fig.add_subplot(111)
        ax.set_facecolor(PANEL)
        fields = ['t002_t003', 'pwt', 'spl', 'pl', 'late']
        labels = ['T002/T003', 'PWT', 'SPL', 'PL', 'Late']
        colors = ['#2980b9', '#2ecc71', '#e67e22', '#e74c3c', '#f1c40f']
        if names:
            y_positions = list(range(len(names)))
            left = [0] * len(names)
            for field, label, color in zip(fields, labels, colors):
                values = [groups[name][field] for name in names]
                ax.barh(y_positions, values, left=left, height=0.68,
                        label=label, color=color)
                left = [a + b for a, b in zip(left, values)]
            ax.set_yticks(y_positions)
            ax.set_yticklabels(names)
            ax.invert_yaxis()
            ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.005),
                      facecolor=PANEL, labelcolor=WHITE, fontsize=9,
                      ncol=5, frameon=False)
            ax.set_xlim(left=0)
        else:
            ax.text(.5, .5, 'No attendance reports in selected period',
                    ha='center', va='center', color=MUTED,
                    transform=ax.transAxes)
        ax.tick_params(colors=MUTED, labelsize=9)
        for spine in ax.spines.values():
            spine.set_color(BORDER)
        ax.grid(axis='x', color='#38475C', alpha=.35)
        ax.set_axisbelow(True)
        # Reserve a flexible margin for group names and room for the legend.
        # Axes stay within the figure at any application window width.
        max_name_len = max((len(str(name)) for name in names), default=12)
        label_margin_px = min(230, max(105, max_name_len * 7 + 14))
        chart_height = height_px
        top_margin_px = 75
        bottom_margin_px = 38
        canvas = FigureCanvasTkAgg(fig, master=holder)
        chart_widget = canvas.get_tk_widget()
        chart_widget.pack(fill='x', expand=False, padx=12, pady=(3, 12))

        resize_job = [None]
        previous_width = [0]

        def fit_chart_to_panel(width):
            """Resize the existing figure rather than destroying the canvas."""
            if not chart_widget.winfo_exists():
                return
            available_width = max(320, int(width))
            if abs(available_width - previous_width[0]) < 3:
                return
            previous_width[0] = available_width
            dpi = fig.get_dpi()
            fig.set_size_inches(available_width / dpi, chart_height / dpi,
                                forward=False)
            # Pixel-based margins retain predictable label/legend clearance.
            fig.subplots_adjust(
                left=min(.42, label_margin_px / available_width),
                right=.985,
                bottom=bottom_margin_px / chart_height,
                top=1 - top_margin_px / chart_height,
            )
            chart_widget.configure(width=available_width, height=chart_height)
            canvas.draw_idle()

        def on_panel_resize(event):
            # Coalesce bursts of Configure events; never redraw the whole page.
            if resize_job[0] is not None:
                holder.after_cancel(resize_job[0])
            width = max(320, event.width - 24)
            resize_job[0] = holder.after(60, lambda w=width: fit_chart_to_panel(w))

        holder.bind('<Configure>', on_panel_resize, add='+')
        # First paint uses Tk's real width, available once layout has settled.
        holder.after_idle(lambda: fit_chart_to_panel(holder.winfo_width() - 24))

    def _interactive_workforce_analysis(self, parent, rows):
        """Optional analytical drill-down below the existing attendance chart.

        This panel is intentionally self-contained: it reads the already filtered
        SQLite result rows and does not modify WhatsApp collection, saved data,
        the existing stacked chart, or the workforce allocation pie chart.
        Chart updates reuse the same Matplotlib canvas, avoiding screen flicker.
        """
        holder = self.panel(parent)
        holder.pack(fill='x', pady=(8, 12))
        self.label(holder, 'INTERACTIVE WORKFORCE ANALYSIS', 13, WHITE, True).pack(
            anchor='w', padx=14, pady=(13, 2))
        self.label(holder, 'Explore reported values by gang. Near misses and late reports are indicators, not unique workforce headcount.',
                   11, MUTED, wraplength=960, justify='left').pack(
            anchor='w', padx=14, pady=(0, 11))
        if not rows:
            self.label(holder,'○  No reports available for drill-down',13,WHITE,True).pack(
                anchor='w',padx=16,pady=(4,4))
            self.label(holder,'Change reporting filters or import attendance before using interactive analysis.',
                       11,MUTED).pack(anchor='w',padx=16,pady=(0,18))
            return

        # Controls affect only the analysis below; global date/team filters above
        # still determine the eligible records used throughout the application.
        controls = ctk.CTkFrame(holder, fg_color='transparent')
        controls.pack(fill='x', padx=14, pady=(0, 6))
        metric_options = {
            'Workforce Total': 'workforce',
            'T002/T003': 't002_t003',
            'PWT': 'pwt', 'SPL': 'spl', 'PL': 'pl',
            'Late Reported': 'late',
            'Near Misses': 'near_miss',
        }
        metric_var = ctk.StringVar(value='Workforce Total')
        gang_var = ctk.StringVar(value='All Gangs')
        sort_var = ctk.StringVar(value='Highest first')
        view_var = ctk.StringVar(value='Top 10')
        plot_state = {'rows': rows}

        for title, variable, options, width in (
            ('Analyse', metric_var, list(metric_options), 170),
            ('Gang', gang_var, ['All Gangs'], 195),
            ('Sort', sort_var, ['Highest first', 'Lowest first'], 130),
            ('Show', view_var, ['Top 10', 'Bottom 10', 'All Gangs'], 125),
        ):
            cell = ctk.CTkFrame(controls, fg_color='transparent')
            cell.pack(side='left', padx=(0, 12))
            self.label(cell, title, 11, MUTED).pack(anchor='w', pady=(0, 3))
            menu = ctk.CTkOptionMenu(
                cell, variable=variable, values=options, width=width,
                fg_color=PANEL, text_color=WHITE,
                button_color=('#B8D4ED' if self.theme_mode == 'Light' else '#254C72'))
            menu.pack()
            if title == 'Gang':
                gang_menu = menu

        status = self.label(holder, '', 11, MUTED)
        status.pack(anchor='w', padx=16, pady=(4, 3))
        fig = Figure(figsize=(8.8, 3.9), dpi=90, facecolor=PANEL)
        ax = fig.add_subplot(111)
        canvas = FigureCanvasTkAgg(fig, master=holder)
        chart_widget = canvas.get_tk_widget()
        chart_widget.pack(fill='x', expand=False, padx=12, pady=(2, 12))

        # IMPORTANT: FigureCanvasTkAgg automatically matches figure dimensions
        # to its Tk canvas when <Configure> fires. The old code overwrote the
        # figure's width with a constant 8.8 inches on EACH filter update,
        # even though Tk stretched the widget across the full panel. That
        # mismatch made the figure image repeat side by side (two apparent
        # graphs). We keep ONE figure, ONE axes and ONE Tk canvas throughout.
        def keep_chart_width_in_sync(event):
            if event.width < 100 or event.height < 100:
                return
            # Matplotlib's own Tk resize callback runs first and sets its
            # bitmap to the current widget dimensions. Adjust margins only.
            label_pixels = plot_state.get('label_margin_pixels', 140)
            left = min(.42, max(.105, label_pixels / event.width))
            fig.subplots_adjust(
                left=left, right=.985, top=.965,
                bottom=max(.065, min(.17, 34 / event.height)),
            )
            canvas.draw_idle()

        chart_widget.bind('<Configure>', keep_chart_width_in_sync, add='+')
        colors = {'workforce': BLUE, 'late': '#D5A33B', 'near_miss': '#D35400',
                  't002_t003': '#2980b9', 'pwt': '#2ecc71',
                  'spl': '#e67e22', 'pl': '#e74c3c'}

        def refresh_chart(*_):
            # Missing numeric values are not treated as validated zeros by the
            # parser. This chart sums only present numerical report values.
            aggregate = {}
            for report in plot_state['rows']:
                name = report.get('group_name') or 'Unknown gang'
                sums = aggregate.setdefault(name, {name: 0 for name in metric_options.values()})
                for field in ('t002_t003', 'pwt', 'spl', 'pl', 'late', 'near_miss'):
                    value = report.get(field)
                    if isinstance(value, (float, int)):
                        sums[field] += value
                sums['workforce'] = sum(sums[field] for field in ('t002_t003', 'pwt', 'spl', 'pl'))

            choices = ['All Gangs'] + sorted(aggregate, key=str.casefold)
            gang_menu.configure(values=choices)
            if gang_var.get() not in choices:
                gang_var.set('All Gangs')
                return  # StringVar trace runs refresh once with the new choice.
            field = metric_options[metric_var.get()]
            items = [(name, values[field]) for name, values in aggregate.items()
                     if gang_var.get() == 'All Gangs' or name == gang_var.get()]
            items.sort(key=lambda item: (item[1], item[0].casefold()),
                       reverse=sort_var.get() == 'Highest first')
            if gang_var.get() == 'All Gangs':
                if view_var.get() == 'Top 10':
                    items = sorted(items, key=lambda item: (-item[1], item[0].casefold()))[:10]
                elif view_var.get() == 'Bottom 10':
                    items = sorted(items, key=lambda item: (item[1], item[0].casefold()))[:10]
            items.sort(key=lambda item: (item[1], item[0].casefold()),
                       reverse=sort_var.get() == 'Highest first')
            # Adjustable figure height keeps gang labels readable without
            # cropping, including when the user chooses 'All Gangs'.
            # For many gangs, enlarge HEIGHT only. Never reset figure width:
            # Tk owns that dimension and resizing it to 8.8in causes repeated
            # chart images in a wider CustomTkinter panel.
            desired_height = max(310, 105 + 30 * max(1, len(items)))
            plot_state['label_margin_pixels'] = min(
                240, max(110, max((len(name) for name, _ in items), default=12) * 7 + 26)
            )
            ax.clear()
            ax.set_facecolor(PANEL)
            if items:
                names, values = zip(*items)
                ax.barh(range(len(items)), values, color=colors[field], height=.65)
                ax.set_yticks(range(len(items)))
                ax.set_yticklabels(names, fontsize=9)
                ax.invert_yaxis()
                ax.set_xlim(left=0)
                status.configure(text=f'{len(items)} gang(s) shown  •  {metric_var.get()}  •  {sum(values):,.0f} reported units in view')
            else:
                ax.text(.5, .5, 'No matching reports for these filters',
                        transform=ax.transAxes, ha='center', va='center', color=MUTED)
                status.configure(text='No matching gang reports for the selected reporting period.')
            ax.tick_params(axis='both', colors=MUTED, labelsize=9)
            ax.grid(axis='x', color='#38475C', alpha=.3)
            ax.set_axisbelow(True)
            for spine in ax.spines.values(): spine.set_color(BORDER)
            # Tk's Configure event will resize the Matplotlib bitmap to
            # EXACTLY the canvas width, so filtering never tiles a stale image.
            # Reuse the existing canvas instead of creating another one.
            chart_widget.configure(height=desired_height)
            # On the first render Tk may not yet have measured the panel;
            # subsequent Configure events handle sizing without flicker.
            if chart_widget.winfo_width() > 100:
                w = chart_widget.winfo_width()
                fig.subplots_adjust(
                    left=min(.42, max(.105, plot_state['label_margin_pixels'] / w)),
                    right=.985, top=.965,
                    bottom=max(.065, min(.17, 34 / desired_height)),
                )
            canvas.draw_idle()

        # Draw only the analytics panel on interaction; other page components
        # never get destroyed or recreated by these controls.
        for variable in (metric_var, gang_var, sort_var, view_var):
            variable.trace_add('write', refresh_chart)

        def update_from_db():
            if holder.winfo_exists():
                plot_state['rows'] = self._selected_rows()
                refresh_chart()
        self._live_widgets['analysis_refresh'] = update_from_db
        refresh_chart()

    def _workforce_charts(self,parent,rows):
        if not rows:
            self._empty_state(parent,'Workforce charts awaiting attendance',
                'No workforce allocation percentages or role totals can be calculated until '
                'WhatsApp staffing values have been received.',kind='pending',compact=True)
            return
        names=['T002/T003','PWT','SPL','PL'];v=core.workforce_totals(rows)
        colors=['#2980b9','#2ecc71','#e67e22','#e74c3c']
        wrap=ctk.CTkFrame(parent,fg_color='transparent');wrap.pack(fill='x',pady=(8,15))
        a=self.panel(wrap);a.pack(side='left',fill='both',expand=True,padx=5)
        self.label(a,'WORKFORCE VOLUMES + LATE REPORTS',13,WHITE,True).pack(anchor='w',padx=13,pady=(12,2))
        fig=Figure(figsize=(6.1,3.2),dpi=85,facecolor=PANEL);ax=fig.add_subplot(111);ax.set_facecolor(PANEL)
        bar_names=names+['Late']
        bar_values=v+[sum((r['late'] or 0) for r in rows)]
        bars=ax.bar(bar_names,bar_values,color=colors+['#f1c40f'],width=.5)
        for b in bars:
            if b.get_height():ax.annotate(f'{b.get_height():.0f}',(b.get_x()+b.get_width()/2,b.get_height()),
                                          xytext=(0,3),textcoords='offset points',ha='center',color=WHITE,fontsize=8)
        ax.tick_params(colors=MUTED,labelsize=9);ax.grid(axis='y',color='#38475C',alpha=.35)
        for spine in ax.spines.values():spine.set_color(BORDER)
        fig.tight_layout(pad=1.5)
        c=FigureCanvasTkAgg(fig,master=a);c.draw();c.get_tk_widget().pack(fill='both',expand=True,padx=5,pady=5)
        b=self.panel(wrap);b.pack(side='left',fill='both',expand=True,padx=5)
        self.label(b,'WORKFORCE ALLOCATION PERCENTAGE',13,WHITE,True).pack(anchor='w',padx=13,pady=(12,2))
        self._pie_chart(b,names,v,colors)

    def _plot(self,parent,daily,type='line'):
        fig=Figure(figsize=(7,3.2),dpi=90,facecolor=PANEL)
        ax=fig.add_subplot(111);ax.set_facecolor(PANEL)
        keys=list(sorted(daily));values=[daily[k] for k in keys]
        if not keys: ax.text(0.5,0.5,'No reports in this period',color=MUTED,ha='center',va='center',transform=ax.transAxes)
        else:
            if type=='bar':ax.bar(range(len(keys)),values,color=BLUE)
            else:ax.plot(range(len(keys)),values,color=BLUE,linewidth=2.5);ax.fill_between(range(len(keys)),values,color=BLUE,alpha=.12)
            step=max(1,len(keys)//6)
            ax.set_xticks(list(range(0,len(keys),step)))
            ax.set_xticklabels([keys[i][5:] for i in range(0,len(keys),step)],rotation=0)
        ax.tick_params(colors=MUTED,labelsize=9)
        for spine in ax.spines.values():spine.set_color(BORDER)
        ax.grid(axis='y',color='#38475C',alpha=.5)
        fig.tight_layout(pad=2)
        canvas=FigureCanvasTkAgg(fig,master=parent)
        canvas.draw();canvas.get_tk_widget().pack(fill='both',expand=True,padx=8,pady=7)
        self._chart=canvas
        if self.page=='Executive': self._live_widgets['trend_chart']=(fig,ax,canvas)

    def _report_table(self,parent,rows,limit=100,title="Recent reports"):
        """Show raw-message sender plus structured metrics; double-click for evidence."""
        self._title(parent, f'{title} ({min(len(rows),limit)} shown)',
                    'Double-click a row to inspect its original message and parsed values.')
        frame=self.panel(parent);frame.pack(fill='x',pady=(0,16))
        if not rows:
            empty_title=self.label(frame,'○  No report evidence for this selection',13,WHITE,True)
            empty_title.pack(anchor='w',padx=15,pady=(13,3))
            empty_help=self.label(frame,'Records will appear after the next matching WhatsApp message. '
                'Try widening the reporting period or checking collection status.',
                11,MUTED,wraplength=920)
            empty_help.pack(anchor='w',padx=15,pady=(0,8))
            if self.page=='Executive':
                self._live_widgets['report_empty_titles']=(empty_title,empty_help)
        # Sender already exists in SQLite; show it alongside report metrics.
        show_late=self.page in ('Workforce','Safety & Alerts')
        cols=('Date / time','Team','Sender','T002/T003','PWT','SPL','PL','Near Miss') + (('Late',) if show_late else ())
        self._style_table('Exec',rowheight=34,fontsize=10)
        tree=ttk.Treeview(frame,columns=cols,show='headings',height=min(11,max(4,len(rows))),style='Exec.Treeview')
        for c in cols:
            tree.heading(c,text=c)
            tree.column(c,width=175 if c in ('Date / time','Team','Sender') else 103,anchor='w')
        # Separate horizontal/vertical scrolling prevents column clipping on laptops.
        tree.grid(row=0,column=0,sticky='nsew',padx=(14,0),pady=(8,0))
        frame.grid_columnconfigure(0,weight=1)
        scrollbar=ttk.Scrollbar(frame,orient='vertical',command=tree.yview,
                                style='Ops.Vertical.TScrollbar')
        scrollbar.grid(row=0,column=1,sticky='ns',padx=(0,14),pady=(8,0))
        horizontal=ttk.Scrollbar(frame,orient='horizontal',command=tree.xview,
                                 style='Ops.Horizontal.TScrollbar')
        horizontal.grid(row=1,column=0,sticky='ew',padx=(14,0),pady=(0,14))
        tree.configure(yscrollcommand=scrollbar.set,xscrollcommand=horizontal.set)
        mapping={}
        for r in rows[:limit]:
            vals=[r['event_at'].replace('T',' '),r['group_name'],r.get('sender') or '-']+[r[k] if r[k] is not None else '-' for k in (('t002_t003','pwt','spl','pl','near_miss','late') if show_late else ('t002_t003','pwt','spl','pl','near_miss'))]
            item=tree.insert('',tk.END,values=vals);mapping[item]=r
        self._stripe_rows(tree)
        def detail(_):
            key=tree.focus()
            if key and key in mapping:
                r=mapping[key]
                messagebox.showinfo('Report evidence',f"Time: {r['event_at']}\nGroup: {r['group_name']}\nSender: {r['sender']}\nOrigin: {r['origin']}\n\n{r['message']}")
        tree.bind('<Double-1>',detail)
        if self.page=='Executive':
            self._live_widgets['reports_tree']=tree
            self._live_widgets['reports_mapping']=mapping

    def _workforce(self):
        if not self._rows:
            self._empty_state(self.content,'Attendance not available for this selection',
                'No group attendance has been captured for the selected dates. '
                'Charts and staffing volumes require a WhatsApp report; missing data is not zero.',
                'Show all dates & groups',self._reset_view_filters,kind='pending',compact=True)
        s=core.summary(self._rows,core.configuration()['groups'])
        self.label(self.content,'Reporting coverage is message-based, not a verified employee headcount.',12,MUTED).pack(anchor='w',pady=(0,15))
        cards=ctk.CTkFrame(self.content,fg_color='transparent');cards.pack(fill='x')
        for key,name in [('t002_t003','T002/T003'),('pwt','PWT'),('spl','SPL'),('pl','PL')]:
            self._kpi(cards,name,s['staffing'][key],'Sum of reported message values',TEAL)
        # Late remains a KPI on Workforce AND appears on Safety & Alerts.
        # It is an attendance exception; do not include it in allocation pie.
        self._kpi(cards, 'Late reported', s['late'],
                  'Reported attendance exceptions', AMBER)
        # Separate five coverage cards visually from the attendance chart.
        # Use a fixed-height transparent frame so both themes retain the same gap.
        coverage_gap = ctk.CTkFrame(self.content, fg_color='transparent', height=28)
        coverage_gap.pack(fill='x')
        coverage_gap.pack_propagate(False)
        # The old 'number of reports per team' chart was not an attendance
        # measure. Replace it with metric-by-gang grouped bars.
        self._gang_attendance_chart(self.content,self._rows)
        self._interactive_workforce_analysis(self.content,self._rows)
        self._workforce_charts(self.content,self._rows)
        self._title(self.content,'What-if workforce scenario','Enter independent assumptions; figures are not inferred from WhatsApp messages.')
        sc=self.panel(self.content);sc.pack(fill='x',pady=8)
        controls=ctk.CTkFrame(sc,fg_color='transparent');controls.pack(fill='x',padx=14,pady=14)
        vals=[]
        for title,default in [('Required workforce',50),('Scheduled workforce',48),('Absence %',8),('Extra contractors',4)]:
            cell=ctk.CTkFrame(controls,fg_color='transparent');cell.pack(side='left',padx=8)
            self.label(cell,title,11,MUTED).pack(anchor='w')
            ent=ctk.CTkEntry(cell,width=125);ent.insert(0,str(default));ent.pack(pady=5);vals.append(ent)
        result=self.label(sc,'',15,BLUE,True);result.pack(anchor='w',padx=22,pady=(0,16))
        def update(*_):
            try:
                d=core.scenarios(*(float(v.get()) for v in vals))
                result.configure(text=f"Effective supply: {d['effective']:.1f}  |  Gap: {d['gap']:.1f}  |  Surplus: {d['surplus']:.1f}  |  Coverage: {d['coverage']:.1f}%",text_color=RED if d['gap']>0 else TEAL)
            except Exception:result.configure(text='Enter valid non-negative numbers; absence must be 0-100%.',text_color=RED)
        self.button(sc,'Calculate scenario',update,width=180).pack(anchor='w',padx=22,pady=(0,18));update()
        self._report_table(self.content,self._rows,limit=100)

    # ======================================================================
    # OPERATOR EXCEPTIONS  /  STAFFING SHORTAGE CONTROL
    # ======================================================================
    def _show_recovery_code(self, username, code):
        """Display a newly generated single-use offline recovery code exactly once."""
        dialog=ctk.CTkToplevel(self)
        dialog.title('Save account recovery code')
        dialog.geometry('620x300');dialog.resizable(False,False)
        dialog.transient(self);dialog.grab_set()
        self.label(dialog,'Save this recovery code securely',17,WHITE,True).pack(anchor='w',padx=20,pady=(20,6))
        self.label(dialog,f'Account: {username}  •  This code is shown once.',12,AMBER).pack(anchor='w',padx=20)
        code_view=ctk.CTkEntry(dialog,width=565,font=ctk.CTkFont(family='Consolas',size=16))
        code_view.pack(padx=20,pady=(16,7));code_view.insert(0,code)
        code_view.configure(state='readonly')
        self.label(dialog,'Store the code outside the application. Anyone who has it can reset this password. '
                   'There is no email recovery because this app runs locally.',11,MUTED,
                   wraplength=565,justify='left').pack(anchor='w',padx=20,pady=(4,12))
        buttons=ctk.CTkFrame(dialog,fg_color='transparent');buttons.pack(fill='x',padx=20)
        def copy_code():
            dialog.clipboard_clear();dialog.clipboard_append(code)
            messagebox.showinfo('Copied','Recovery code copied. Clear your clipboard after storing it securely.',parent=dialog)
        self.button(buttons,'Copy code',copy_code,width=130).pack(side='left')
        self.button(buttons,'I have saved it',dialog.destroy,color='#166653',width=160).pack(side='right')
        dialog.focus_force()

    def _settings_switch_account(self):
        """Sign out of a non-admin account before administrator login."""
        if self.exception_session:
            core.revoke_app_session(self.exception_session['token'])
        self.exception_session=None
        self._render()

    def _exception_login(self):
        heading = ('Settings - Administrator Sign-in' if self.page=='Settings'
                   else 'Staffing Changes - Account Access')
        self._title(self.content,heading,
                    'Individual usernames, passwords and a private audit history for each user.')
        card=self.panel(self.content);card.pack(fill='x',pady=10)
        if not core.app_users_ready():
            self.label(card,'INITIAL ADMINISTRATOR SETUP',13,TEAL,True).pack(anchor='w',padx=18,pady=(16,5))
            self.label(card,'Create the first administrator. Additional staff accounts can then be added by this administrator.',
                       11,MUTED,wraplength=890,justify='left').pack(anchor='w',padx=18)
            cols=ctk.CTkFrame(card,fg_color='transparent');cols.pack(anchor='w',padx=15,pady=10)
            username=ctk.CTkEntry(cols,width=165,placeholder_text='Admin username')
            fullname=ctk.CTkEntry(cols,width=210,placeholder_text='Full name')
            password=ctk.CTkEntry(cols,width=185,placeholder_text='Password (12+)',show='•')
            confirm=ctk.CTkEntry(cols,width=185,placeholder_text='Confirm password',show='•')
            for w in (username,fullname,password,confirm):w.pack(side='left',padx=4)
            legacy=None
            if core.legacy_operator_migration_required():
                self.label(card,'Existing legacy database detected: supply the old Operator 1 passcode to authorise this upgrade.',
                           11,AMBER,wraplength=890,justify='left').pack(anchor='w',padx=19)
                legacy=ctk.CTkEntry(card,width=275,placeholder_text='Previous Operator 1 passcode',show='•')
                legacy.pack(anchor='w',padx=19,pady=9)
            def setup():
                try:
                    if password.get()!=confirm.get():raise ValueError('Passwords do not match.')
                    code=core.bootstrap_admin_account(username.get(),fullname.get(),password.get(),
                                                       legacy.get() if legacy else None)
                    self._show_recovery_code(username.get(),code)
                    self._render()
                except Exception as exc:messagebox.showerror('Unable to create administrator',str(exc))
            self.button(card,'Create administrator',setup,color='#166653',width=190).pack(anchor='w',padx=19,pady=(3,18))
            return
        self.label(card,'SIGN IN TO RECORD STAFFING CHANGES',14,WHITE,True).pack(anchor='w',padx=18,pady=(17,8))
        controls=ctk.CTkFrame(card,fg_color='transparent');controls.pack(anchor='w',padx=13,pady=(0,14))
        username=ctk.CTkEntry(controls,width=235,placeholder_text='Username')
        password=ctk.CTkEntry(controls,width=235,placeholder_text='Password',show='•')
        username.pack(side='left',padx=5);password.pack(side='left',padx=5)
        def login():
            try:
                self.exception_session=core.authenticate_app_user(username.get(),password.get())
                password.delete(0,'end')
                self._render()
            except Exception as exc:messagebox.showerror('Sign-in failed',str(exc))
        self.button(controls,'Sign in',login,color='#166653',width=125).pack(side='left',padx=6)
        password.bind('<Return>',lambda _:login())
        self.button(card,'Forgot password? Use recovery code',self._exception_recovery,
                    color='transparent',width=290).pack(anchor='w',padx=16,pady=(0,15))
        self.label(card,'Forgot both password and recovery code? Ask an administrator to generate a new recovery code. '
                   'Accounts are local to this database.',11,MUTED,wraplength=900,justify='left').pack(anchor='w',padx=18,pady=(0,16))

    def _exception_recovery(self):
        win=ctk.CTkToplevel(self);win.title('Offline password recovery');win.geometry('550x385')
        win.transient(self);win.grab_set()
        self.label(win,'Reset your password',18,WHITE,True).pack(anchor='w',padx=22,pady=(20,10))
        fields=[]
        for hint,masked in [('Username',False),('Recovery code',False),('New password (12+)',True),('Confirm password',True)]:
            entry=ctk.CTkEntry(win,width=490,placeholder_text=hint,show='•' if masked else '')
            entry.pack(padx=22,pady=6);fields.append(entry)
        def reset():
            try:
                if fields[2].get()!=fields[3].get():raise ValueError('New passwords do not match.')
                next_code=core.recover_app_password(fields[0].get(),fields[1].get(),fields[2].get())
                user=fields[0].get().strip()
                win.destroy()
                self._show_recovery_code(user,next_code)
                messagebox.showinfo('Password updated','Use your new password to sign in.')
            except Exception as exc:messagebox.showerror('Recovery unsuccessful',str(exc),parent=win)
        self.button(win,'Reset password',reset,width=185,color='#166653').pack(pady=(16,9))

    def _exception_manage_users(self, parent=None):
        """Admin-only account management shown within the Settings section."""
        session=self.exception_session
        if not session or session.get('role')!='admin':return
        p=self.panel(parent if parent is not None else self.content)
        p.pack(fill='x',pady=(7,12))
        self.label(p,'ACCOUNT MANAGEMENT / ADMINISTRATOR',13,WHITE,True).pack(anchor='w',padx=15,pady=(13,7))
        self.label(p,'Create staff logins, disable departed users or issue a fresh recovery code. '
                   'Old codes stop working after rotation.',11,MUTED,wraplength=970,justify='left').pack(anchor='w',padx=15)
        form=ctk.CTkFrame(p,fg_color='transparent');form.pack(fill='x',padx=12,pady=10)
        name=ctk.CTkEntry(form,width=140,placeholder_text='New username')
        display=ctk.CTkEntry(form,width=180,placeholder_text='Display name')
        role=ctk.StringVar(value='operator')
        role_pick=ctk.CTkOptionMenu(form,values=['operator','admin'],variable=role,width=115)
        temp_pwd=ctk.CTkEntry(form,width=200,placeholder_text='Initial password (12+)',show='•')
        for w in (name,display,role_pick,temp_pwd):w.pack(side='left',padx=3)
        def add_user():
            try:
                code=core.create_app_user(session['token'],name.get(),display.get(),role.get(),temp_pwd.get())
                target=name.get().strip()
                temp_pwd.delete(0,'end')
                self._show_recovery_code(target,code)
                self._render()
            except Exception as exc:messagebox.showerror('Cannot create user',str(exc))
        self.button(form,'Add user',add_user,width=100,color='#166653').pack(side='left',padx=5)
        users=core.list_app_users()
        self._style_table('Users',rowheight=35,fontsize=10)
        tree=ttk.Treeview(p,columns=('user','name','role','state','login'),show='headings',
                          height=min(6,len(users)+1),style='Users.Treeview')
        for column,heading,w in [('user','Username',150),('name','Name',200),('role','Role',125),
                                  ('state','Access',100),('login','Last sign-in',170)]:
            tree.heading(column,text=heading);tree.column(column,width=w,minwidth=85,anchor='w')
        for user in users:
            tree.insert('','end',iid=user['username'],values=(user['username'],user['display_name'],
                user['account_role'],'Enabled' if user['enabled'] else 'Disabled',
                (user['last_login'] or 'Never')[:19].replace('T',' ')))
        self._stripe_rows(tree)
        tree.pack(fill='x',padx=15,pady=(2,10))
        actions=ctk.CTkFrame(p,fg_color='transparent');actions.pack(fill='x',padx=14,pady=(0,15))
        def selected_user():
            ids=tree.selection()
            if not ids:raise ValueError('Select a user from the table.')
            return ids[0]
        def rotate():
            try:
                user=selected_user()
                if not messagebox.askyesno('Replace recovery code','Issue a new recovery code for '+user+'? The old code will become invalid.'):
                    return
                code=core.rotate_user_recovery_code(session['token'],user)
                self._show_recovery_code(user,code)
            except Exception as exc:messagebox.showerror('Recovery reset failed',str(exc))
        def toggle():
            try:
                user=selected_user()
                state=next(u['enabled'] for u in users if u['username']==user)
                core.set_app_user_enabled(session['token'],user,not state)
                self._render()
            except Exception as exc:messagebox.showerror('Account update failed',str(exc))
        self.button(actions,'Issue new recovery code',rotate,width=215).pack(side='left',padx=4)
        self.button(actions,'Enable / Disable selected',toggle,width=195,color='#69424D').pack(side='left',padx=4)

    def _exceptions(self):
        self.label(self.content,'STAFFING CHANGES / DAILY CONTROL',12,TEAL,True).pack(anchor='w',pady=(0,6))
        self.label(self.content,'One service date, every active group. Double-click a count to edit, press Enter to move to the '
                   'next group, then Save All. Actual WhatsApp attendance drives automatic shortages; '
                   'before/after change counts do not reduce that headcount again.',
                   11,MUTED,wraplength=1030,justify='left').pack(anchor='w',pady=(0,11))
        if self.exception_session is None:
            self._exception_login();return
        session=self.exception_session
        actor=session['username']
        top=ctk.CTkFrame(self.content,fg_color='transparent');top.pack(fill='x',pady=(0,11))
        self.label(top,'●  '+session['display_name']+' ('+actor+')  •  '+session['role'].title(),13,TEAL,True).pack(side='left')
        def logout():
            if self._exception_draft_dirty and not messagebox.askyesno('Unsaved entries',
                     'Unsaved staffing changes will be lost. Sign out anyway?'):
                return
            self._exception_draft_dirty=False
            core.revoke_app_session(session['token'])
            self.exception_session=None;self._render()
        self.button(top,'Sign out',logout,width=105,color='#69424D').pack(side='right')
        date_panel=self.panel(self.content);date_panel.pack(fill='x',pady=(0,12))
        datebar=ctk.CTkFrame(date_panel,fg_color='transparent');datebar.pack(fill='x',padx=14,pady=13)
        self.label(datebar,'Night-shift service date',12,WHITE,True).pack(side='left',padx=(0,9))
        ctk.CTkEntry(datebar,textvariable=self.exception_day,width=155).pack(side='left',padx=(0,10))
        def set_previous():
            if self._exception_draft_dirty and not messagebox.askyesno('Unsaved entries',
                 'Discard unsaved changes and load the previous night?'):
                return
            self._exception_draft_dirty=False
            self._exception_started_date=None
            self.exception_day.set((date.today()-timedelta(days=1)).isoformat());self._render()
        self.button(datebar,'Previous night',set_previous,width=125).pack(side='left',padx=(0,8))
        self.button(datebar,'Load / Refresh',self._exception_load,width=135).pack(side='left')
        self.label(datebar,'One record per group and service date.',11,MUTED).pack(side='right',padx=9)
        try: day=core.parse_date(self.exception_day.get()).isoformat()
        except Exception as exc:
            self.label(self.content,'Invalid service date: '+str(exc),12,RED).pack(anchor='w');return
        if not core.service_date_has_records(day) and getattr(self,'_exception_started_date',None)!=day:
            self._exception_draft_dirty=False
            def start_new_date():
                self._exception_started_date=day
                self._render()
            self._empty_state(self.content,'No records for '+day,
                'There are no saved staffing changes or final WhatsApp attendance reports for this '
                'night. This is an unstarted date, not a zero-shortage result. '
                'Use the action below if you need to enter change counts before attendance arrives.',
                'Start entries for this date',start_new_date,kind='pending')
            self._exception_recent_table()
            return
        active=sorted(core.active_group_names(core.group_registry()),key=str.casefold)
        if not active:
            self._empty_state(self.content,'No active WhatsApp groups',
                'The rapid-entry grid will appear after an administrator adds and enables groups in Settings.',
                kind='pending')
            return
        # Read the entire day's 40+ groups once; no selection dropdown.
        loaded={}
        for g in active:
            record=core.exception_entry(g,day,'Shift 1')
            computed=core.calculate_exception(g,day,'Shift 1')
            loaded[g]={'revision':record['revision'] if record else 0,
                       'before':record['before_count'] if record else None,
                       'after':record['after_count'] if record else None,
                       'computed':computed}
        done_before=sum(v['before'] is not None for v in loaded.values())
        done_after=sum(v['after'] is not None for v in loaded.values())
        attendance=sum(v['computed']['shortage'] is not None for v in loaded.values())
        short_count=sum(v['computed']['shortage'] or 0 for v in loaded.values())
        cards=ctk.CTkFrame(self.content,fg_color='transparent');cards.pack(fill='x',pady=(0,11))
        self._kpi(cards,'Before 15:00',f'{done_before}/{len(active)}','Groups submitted',TEAL)
        self._kpi(cards,'After 15:00',f'{done_after}/{len(active)}','Groups submitted',BLUE)
        self._kpi(cards,'WhatsApp actual',f'{attendance}/{len(active)}','Groups with final report',AMBER)
        self._kpi(cards,'Staff short',str(short_count),'Role-specific shortages',RED)
        workstation=self.panel(self.content);workstation.pack(fill='x',pady=(0,11))
        self.label(workstation,'ALL ACTIVE GROUPS  /  RAPID ENTRY',14,WHITE,True).pack(anchor='w',padx=15,pady=(15,6))
        filters=ctk.CTkFrame(workstation,fg_color='transparent');filters.pack(fill='x',padx=12,pady=(3,11))
        query=ctk.StringVar(value='');state=ctk.StringVar(value='All groups')
        search=ctk.CTkEntry(filters,textvariable=query,width=320,placeholder_text='Search any of the 40+ groups')
        search.pack(side='left',padx=4)
        choice=ctk.CTkOptionMenu(filters,variable=state,width=210,
               values=['All groups','Needs change entries','Awaiting WhatsApp','Staff shortages','Complete'])
        choice.pack(side='left',padx=8)
        self.label(filters,'Double-click Before or After values to edit',11,MUTED).pack(side='right',padx=7)
        frame=ctk.CTkFrame(workstation,fg_color=self._table_colors()['border'],
                           corner_radius=10,border_width=1,
                           border_color=self._table_colors()['border'])
        frame.pack(fill='x',padx=14,pady=(0,10))
        self._style_table('ExceptionEntry',rowheight=40,fontsize=11)
        columns=('name','before','after','attendance','shortage','state')
        table=ttk.Treeview(frame,columns=columns,show='headings',height=12,selectmode='browse',
                          style='ExceptionEntry.Treeview')
        for col,heading,width in [('name','WhatsApp group',235),('before','Before 15:00',137),
               ('after','After 15:00',137),('attendance','Actual attendance',150),
               ('shortage','Shortage',110),('state','Workflow status',225)]:
            table.heading(col,text=heading);table.column(col,width=width,minwidth=95,anchor='center' if col!='name' else 'w')
        scroll=ttk.Scrollbar(frame,orient='vertical',command=table.yview,
                             style='Ops.Vertical.TScrollbar')
        frame.grid_columnconfigure(0,weight=1)
        table.configure(yscrollcommand=scroll.set)
        table.grid(row=0,column=0,sticky='nsew',padx=(6,0),pady=(6,0))
        scroll.grid(row=0,column=1,sticky='ns',padx=(0,5),pady=(6,0))
        horizontal=ttk.Scrollbar(frame,orient='horizontal',command=table.xview,
                                 style='Ops.Horizontal.TScrollbar')
        horizontal.grid(row=1,column=0,sticky='ew',padx=(6,0),pady=(0,6))
        table.configure(xscrollcommand=horizontal.set)
        table.tag_configure('short',foreground=RED)
        table.tag_configure('waiting',foreground=AMBER)
        table.tag_configure('ready',foreground=TEAL)
        # Drafts live in memory until Save All. No background redraw discards keystrokes.
        drafts={g:{} for g in active}
        self._exception_draft_dirty=False
        selected={'group':active[0]}
        def cell_value(g,stage):
            value=drafts[g].get(stage,loaded[g][stage])
            return '-' if value is None else str(value)
        def status(g):
            v=loaded[g];cal=v['computed']
            before=drafts[g].get('before',v['before'])
            after=drafts[g].get('after',v['after'])
            if cal['shortage'] is None:
                return 'Awaiting WhatsApp'
            if before is None or after is None:
                return 'Attendance / changes pending'
            return 'Complete'
        unsaved_message=ctk.StringVar(value='No unsaved entries')
        def draw_grid():
            pending_count=sum(bool(drafts[g]) for g in drafts)
            self._exception_draft_dirty=bool(pending_count)
            unsaved_message.set(f'{pending_count} group(s) with unsaved edits' if pending_count else 'All entered values saved')
            active_selection=selected['group']
            for item in table.get_children():table.delete(item)
            term=query.get().strip().casefold();flt=state.get()
            for g in active:
                v=loaded[g];comp=v['computed'];s=status(g)
                if term not in g.casefold():continue
                if flt=='Needs change entries' and v['before'] is not None and v['after'] is not None:continue
                if flt=='Awaiting WhatsApp' and comp['shortage'] is not None:continue
                if flt=='Staff shortages' and not (comp['shortage'] is not None and comp['shortage']>0):continue
                if flt=='Complete' and s!='Complete':continue
                short='Pending' if comp['shortage'] is None else str(comp['shortage'])
                attendance='Pending' if comp['available'] is None else str(comp['available'])
                tag='short' if comp['shortage'] else ('waiting' if comp['shortage'] is None else 'ready')
                table.insert('','end',iid=g,values=(g,cell_value(g,'before'),cell_value(g,'after'),
                             attendance,short,s+('  •  Unsaved' if drafts[g] else '')),tags=(tag,))
            self._stripe_rows(table)
            if table.exists(active_selection):table.selection_set(active_selection)
            elif table.get_children():
                first=table.get_children()[0];selected['group']=first;table.selection_set(first)
            update_detail()
        detail=self.panel(self.content);detail.pack(fill='x',pady=(0,11))
        def update_detail(_=None):
            for w in detail.winfo_children():w.destroy()
            if not table.get_children():
                self.label(detail,'○  No groups match the current search or status filter',
                           13,WHITE,True).pack(anchor='w',padx=16,pady=(15,5))
                self.label(detail,'Clear the search or switch to All groups. '
                           'No saved entries were deleted by filtering.',
                           11,MUTED).pack(anchor='w',padx=16,pady=(0,15))
                return
            g=selected['group']
            c=loaded[g]['computed']
            self.label(detail,'AUTOMATIC STAFFING SHORTAGE  /  '+g.upper(),13,WHITE,True).pack(anchor='w',padx=16,pady=(13,7))
            if c['shortage'] is None:
                self.label(detail,'PENDING - '+c['reason'],12,AMBER,wraplength=970,justify='left').pack(anchor='w',padx=16,pady=(0,11))
            else:
                self.label(detail,f"{c['shortage']} staff short   •   Planned {c['plan_total']}   •   Actual {c['available']}",
                           19,RED if c['shortage'] else TEAL,True).pack(anchor='w',padx=16,pady=(0,8))
                self.label(detail,'    |    '.join(f"{x['role']}: {x['present']}/{x['required']} present, {x['shortage']} short"
                           for x in c['role_breakdown']),11,WHITE,wraplength=980,
                           justify='left').pack(anchor='w',padx=16,pady=(0,10))
            self.label(detail,'No-shows via WhatsApp: '+(str(c['no_shows']) if c['no_shows'] is not None
                       else 'Not explicitly reported'),11,MUTED).pack(anchor='w',padx=16,pady=(0,10))
            snaps=core.exception_reports(g,day)
            if c['shortage'] is None and len(snaps)>0:
                options={}
                for r in snaps:
                    label=f"{r['event_at'][:16].replace('T',' ')} · report {r['id']} · T:{r['t002_t003']} P:{r['pwt']} S:{r['spl']} PL:{r['pl']}"
                    options[label]=r['id']
                bottom=ctk.CTkFrame(detail,fg_color='transparent');bottom.pack(fill='x',padx=12,pady=(0,12))
                opt=ctk.StringVar(value=next(iter(options)))
                ctk.CTkOptionMenu(bottom,variable=opt,values=list(options),width=470).pack(side='left',padx=4)
                def link():
                    try:
                        if any(drafts[g]):raise ValueError('Save pending group edits before linking attendance.')
                        core.link_final_attendance_as_user(session['token'],g,day,options[opt.get()],loaded[g]['revision'])
                        self._render()
                    except Exception as e:messagebox.showerror('Cannot link report',str(e))
                self.button(bottom,'Link final report',link,width=155,color='#166653').pack(side='left',padx=5)
        def select_row(_=None):
            ids=table.selection()
            if ids:selected['group']=ids[0];update_detail()
        table.bind('<<TreeviewSelect>>',select_row)
        # In-place editor: double-click numeric columns. Enter saves the draft and advances to next group.
        edit=[None]
        edit_commit=[None]
        def edit_cell(group,column,advance=False):
            if edit[0] and edit[0].winfo_exists():edit[0].destroy()
            if not table.exists(group):return
            box=table.bbox(group,column)
            if not box:return
            x,y,w,h=box
            # Native Tk Entry overlays ttk.Treeview reliably on Windows and Tk 8.6.
            entry=tk.Entry(table,bd=1,relief='solid',font=('Segoe UI',11),
                           bg=self._table_colors()['editor'],
                           fg=self._table_colors()['foreground'],
                           insertbackground=self._table_colors()['foreground'],
                           highlightthickness=2,highlightcolor=BLUE,
                           highlightbackground=self._table_colors()['border'],
                           selectbackground=self._table_colors()['selected'],
                           selectforeground=self._table_colors()['selected_ink'])
            old=cell_value(group,'before' if column=='#2' else 'after')
            if old!='-':entry.insert(0,old)
            entry.place(x=x,y=y,width=w,height=h)
            edit[0]=entry;entry.focus_set();entry.select_range(0,'end')
            submitted=[False]
            def commit(next_row=False):
                if submitted[0]:return
                submitted[0]=True
                val=entry.get().strip()
                stage='before' if column=='#2' else 'after'
                previous=loaded[group][stage]
                if val=='':
                    if previous is not None:
                        messagebox.showwarning('Cannot clear saved count','Enter a non-negative count. To correct a saved value, enter the replacement number.')
                    drafts[group].pop(stage,None)
                else:
                    try:
                        value=core._ex_validate_nonneg(val,'Changes '+stage+' 15:00')
                        if value==previous:drafts[group].pop(stage,None)
                        else:drafts[group][stage]=value
                    except ValueError as exc:messagebox.showerror('Invalid number',str(exc))
                entry.destroy();edit[0]=None;edit_commit[0]=None;draw_grid()
                if next_row:
                    rows=list(table.get_children())
                    if group in rows and rows.index(group)+1<len(rows):
                        next_group=rows[rows.index(group)+1]
                        table.see(next_group);table.selection_set(next_group)
                        self.after(30,lambda:edit_cell(next_group,column))
            edit_commit[0]=commit
            entry.bind('<Return>',lambda _:(commit(True),'break'))
            entry.bind('<Escape>',lambda _:(entry.destroy(),edit.__setitem__(0,None),edit_commit.__setitem__(0,None),'break'))
            entry.bind('<FocusOut>',lambda _:commit(False))
        def double_click(event):
            row=table.identify_row(event.y)
            col=table.identify_column(event.x)
            if row and col in ('#2','#3'):edit_cell(row,col)
        table.bind('<Double-1>',double_click)
        def save_all():
            if edit[0] and edit[0].winfo_exists() and edit_commit[0]:
                edit_commit[0](False)
            payload=[]
            for group in active:
                if drafts[group]:
                    payload.append({'group':group,'revision':loaded[group]['revision'],**drafts[group]})
            if not payload:
                messagebox.showinfo('No changes','No unsaved values to submit. Blank means not entered; zero is an explicit entry.')
                return
            try:
                updated=core.bulk_save_change_counts(session['token'],day,payload)
                self.status_text.set(f'{updated} groups saved for {day} by {actor}')
                messagebox.showinfo('Changes saved',f'{updated} group(s) updated. Your entries are timestamped and audited.')
                self._render()
            except Exception as exc:
                messagebox.showerror('No changes saved',str(exc)+'\nThe entire batch was rolled back. Reload to resolve conflicts.')
        toolbar=ctk.CTkFrame(workstation,fg_color='transparent');toolbar.pack(fill='x',padx=13,pady=(1,13))
        self.label(toolbar,'',11,AMBER,textvariable=unsaved_message).pack(side='left')
        self.label(toolbar,'Blank = pending  •  0 = confirmed none',10,MUTED).pack(side='left',padx=14)
        self.button(toolbar,'Save All Changes',save_all,width=185,color='#166653').pack(side='right',padx=5)
        def save_hotkey(event):
            if self.page=='Exceptions':save_all();return 'break'
        table.bind('<Control-s>',save_hotkey)
        search.bind('<Control-s>',save_hotkey)
        query.trace_add('write',lambda *_:draw_grid())
        state.trace_add('write',lambda *_:draw_grid())
        draw_grid()
        # Historical legacy shift labels remain in the DB; new submissions always use one service-date record.
        self._exception_recent_table()

    def _exception_load(self):
        if getattr(self,'_exception_draft_dirty',False):
            if not messagebox.askyesno('Unsaved entries','Discard unsaved edits and reload the selected date?'):
                return
            self._exception_draft_dirty=False
        try:core.parse_date(self.exception_day.get())
        except Exception as exc:
            messagebox.showerror('Invalid date',f'{exc}\\nUse YYYY-MM-DD or DD/MM/YYYY.');return
        self._exception_started_date=None
        self._render()

    def _exception_recent_table(self):
        self._title(self.content,'Recent service-date change and attendance records',
              'This is historical information; select a date above to edit all active groups in one grid.')
        today=date.today();metrics=core.exception_dashboard(today-timedelta(days=14),today)
        p=self.panel(self.content);p.pack(fill='x',pady=(4,8))
        self.label(p,f"{metrics['verified']} attendance calculations   •   {metrics['pending']} pending   •   "
                    f"{metrics['shortage']} combined role-specific shortages",11,TEAL).pack(anchor='w',padx=15,pady=(11,7))
        if not metrics['rows']:
            self.label(p,'○  No recent change or attendance records',13,WHITE,True).pack(
                anchor='w',padx=15,pady=(5,3))
            self.label(p,'The register will populate after operators submit changes or WhatsApp '
                'attendance is captured.',11,MUTED).pack(anchor='w',padx=15,pady=(0,18))
            return
        self._style_table('ExceptionHistory',rowheight=34,fontsize=10)
        tree=ttk.Treeview(p,columns=('date','group','before','after','present','short','status'),
                          show='headings',height=7,style='ExceptionHistory.Treeview')
        for key,name,width in [('date','Service date',110),('group','Group',190),
              ('before','Before 15:00',130),('after','After 15:00',130),
              ('present','Actual staff',110),('short','Shortage',85),('status','Status',160)]:
            tree.heading(key,text=name);tree.column(key,width=width,minwidth=75,anchor='center')
        for row in metrics['rows'][:100]:
            tree.insert('','end',values=(row['service_date'],row['group'],
               '-' if row['before_count'] is None else row['before_count'],
               '-' if row['after_count'] is None else row['after_count'],
               '-' if row['available'] is None else row['available'],
               'Pending' if row['shortage'] is None else row['shortage'],row['status']))
        self._stripe_rows(tree)
        tree.pack(fill='x',padx=15,pady=(2,13))

    def _exception_executive_panel(self):
        self._title(self.content,'Workforce changes, absences and shortages',
                    'Final WhatsApp attendance drives the shortage KPI; the single operator records both change counts.')
        k=ctk.CTkFrame(self.content,fg_color='transparent');k.pack(fill='x',pady=(4,10))
        self._live_widgets['ex_before']=self._kpi(k,'Changes before 15:00','-','Changes coordinator',BLUE)
        self._live_widgets['ex_after']=self._kpi(k,'Changes after 15:00','-','Same coordinator',AMBER)
        self._live_widgets['ex_no_shows']=self._kpi(k,'No shows','-','Only if explicitly reported in WhatsApp',RED)
        self._live_widgets['ex_shortage']=self._kpi(k,'Staff short','-','Confirmed role-specific vacancies',RED)
        outer=self.panel(self.content);outer.pack(fill='x',pady=8)
        self._live_widgets['ex_review']=self.label(outer,'',11,AMBER)
        self._live_widgets['ex_review'].pack(anchor='w',padx=15,pady=(13,5))
        self.label(outer,'CHART: Four colours = changes before 15:00, changes after 15:00, '
                   'explicit WhatsApp no-shows, and staffing shortages. Values are event counts, '
                   'not unique employees; unreported no-shows are unknown.',
                   10,MUTED,wraplength=1000,justify='left').pack(anchor='w',padx=15,pady=(0,5))
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg as _ExCanvas
        fig=Figure(figsize=(10.6,4.1),dpi=90,facecolor=PANEL)
        ax=fig.add_subplot(111);ax.set_facecolor(PANEL)
        canvas=_ExCanvas(fig,master=outer)
        canvas.get_tk_widget().pack(fill='x',padx=12,pady=(0,8))
        self._live_widgets['ex_chart']=(fig,ax,canvas)
        self._style_table('ExceptionReview',rowheight=34,fontsize=10)
        tbl=ttk.Treeview(outer,columns=('day','gang','change1','change2','no','short','state'),
                         show='headings',height=7,style='ExceptionReview.Treeview')
        for col,heading,width in [('day','Date',105),('gang','Gang',160),
              ('change1','Before 15:00',120),('change2','After 15:00',120),
              ('no','No shows',98),('short','Shortage',90),('state','State',104)]:
            tbl.heading(col,text=heading);tbl.column(col,width=width,minwidth=65,anchor='center')
        tbl.pack(fill='x',padx=15,pady=(4,16))
        self._live_widgets['ex_tbl']=tbl
        self._live_widgets['ex_signature']=None
        def refresh():
            a,b=self._date_bounds()
            data=core.exception_dashboard(a,b,self.group.get())
            self._update_kpi_in_place('ex_before',data['changes_before'] if data['changes_before_known'] else '-')
            self._update_kpi_in_place('ex_after',data['changes_after'] if data['changes_after_known'] else '-')
            self._update_kpi_in_place('ex_no_shows',data['no_shows'] if data['no_shows_known'] else '-',
                 f"Explicit WhatsApp counts for {data['no_shows_known']} shift(s)")
            self._update_kpi_in_place('ex_shortage',data['shortage'],
                         f"{data['short_shifts']} shift(s) below plan")
            self._configure_text(self._live_widgets['ex_review'],
                f"Attendance-based calculations: {data['verified']}   •   Pending: {data['pending']}   •   "
                f"Waiting for WhatsApp: {data['awaiting_whatsapp']}   •   Awaiting change entries: {data['awaiting_manual_changes']}   •   Change logs completed: {data['change_logs_complete']}")
            signature=tuple((r['group'],r['service_date'],r['shift'],r['revision'],
                 r['before_count'],r['after_count'],r['no_shows'],
                 r['report_id'],r['shortage'],r['status'],r['reason']) for r in data['rows'])
            if signature==self._live_widgets.get('ex_signature'):
                return
            self._live_widgets['ex_signature']=signature
            for item in tbl.get_children():tbl.delete(item)
            for r in data['rows'][:80]:
                tbl.insert('','end',values=(r['service_date'],r['group'],
                  '-' if r['before_count'] is None else r['before_count'],
                  '-' if r['after_count'] is None else r['after_count'],
                  'Not reported' if r['no_shows'] is None else r['no_shows'],
                  '-' if r['shortage'] is None else r['shortage'],r['status']))
            self._stripe_rows(tbl)
            # The category chart has four distinct colours like the stacked
            # workforce chart. Each category is an independent KPI event count;
            # stacked width is NOT a number of unique employees.
            chart_rows=core.workforce_exception_chart_rows(data['rows'],limit=10)
            ax.clear();ax.set_facecolor(PANEL)
            category_colors=['#2980B9','#16A085','#9B59B6','#E74C3C']
            specs=[('before','Changes before 15:00'),('after','Changes after 15:00'),
                   ('no_shows','No Shows'),('shortage','Shortages')]
            if chart_rows:
                names=[r['group'] for r in chart_rows]
                y=list(range(len(names)))
                left=[0]*len(names)
                for (field,label),color in zip(specs,category_colors):
                    values=[r[field] for r in chart_rows]
                    ax.barh(y,values,left=left,height=.66,label=label,color=color,
                            edgecolor=PANEL,linewidth=.5)
                    left=[a+b for a,b in zip(left,values)]
                ax.set_yticks(y,labels=names)
                ax.invert_yaxis()
                ax.set_xlim(left=0)
                ax.set_xlabel('Reported category counts - not unique employees',color=WHITE,fontsize=9)
                ax.legend(loc='lower center',bbox_to_anchor=(0.5,1.015),
                          ncol=4,fontsize=8,frameon=False,labelcolor=WHITE)
                ax.tick_params(colors=WHITE,labelsize=9)
                ax.xaxis.grid(True,color=BORDER,alpha=.45)
                ax.set_axisbelow(True)
                for side in ('top','right'):ax.spines[side].set_visible(False)
                ax.spines['bottom'].set_color(MUTED)
                ax.spines['left'].set_color(MUTED)
            else:
                ax.text(.5,.5,'No staffing events for this reporting period',
                        color=MUTED,ha='center',va='center',transform=ax.transAxes)
                ax.set_xticks([]);ax.set_yticks([])
            fig.tight_layout(pad=1.5)
            fig.subplots_adjust(top=.80,left=.18,bottom=.17)
            canvas.draw_idle()
        self._live_widgets['ex_refresh']=refresh
        refresh()

    def _staffing_plan_settings(self):
        """Dedicated Settings sub-page for group-specific role requirements."""
        self.label(self.content,'STAFFING PLANS / GROUP REQUIREMENTS',12,TEAL,True).pack(anchor='w',pady=(0,8))
        self.label(self.content,'Set the staff required for each registered WhatsApp group. '                   'The total is calculated automatically and used for shortage monitoring. '                   'A manually saved plan overrides Excel from its effective date onward.',
                   12,MUTED,wraplength=960,justify='left').pack(anchor='w',pady=(0,15))
        self.button(self.content,'← Back to Settings',lambda:self._navigate('Settings'),width=170).pack(anchor='w',pady=(0,12))
        registry=core.group_registry()
        names=[record['name'] for record in registry]
        if not names:
            self.label(self.content,'No saved WhatsApp groups. Add and save one in Settings first.',12,AMBER).pack(anchor='w',pady=10)
            return
        if not hasattr(self,'plan_group'):
            self.plan_group=ctk.StringVar(value=names[0])
        if not hasattr(self,'plan_effective_day'):
            self.plan_effective_day=ctk.StringVar(value=date.today().isoformat())
        if self.plan_group.get() not in names:
            self.plan_group.set(names[0])
        box=self.panel(self.content);box.pack(fill='x',pady=(0,14))
        self.label(box,'SELECT WHATSAPP GROUP AND EFFECTIVE DATE',13,WHITE,True).pack(anchor='w',padx=16,pady=(15,12))
        row=ctk.CTkFrame(box,fg_color='transparent');row.pack(fill='x',padx=16,pady=(0,14))
        self.label(row,'WhatsApp group',12,MUTED).pack(side='left',padx=(0,9))
        ctk.CTkOptionMenu(row,variable=self.plan_group,values=names,width=270,
              command=lambda _:self._render(),fg_color=PANEL,text_color=WHITE,
              button_color=('#B8D4ED' if self.theme_mode=='Light' else '#254C72')).pack(side='left',padx=(0,20))
        self.label(row,'Effective from',12,MUTED).pack(side='left',padx=(0,8))
        ctk.CTkEntry(row,textvariable=self.plan_effective_day,width=134).pack(side='left',padx=(0,12))
        self.button(row,'Load plan',self._render,width=118).pack(side='left')
        self.label(box,'Use YYYY-MM-DD. Save new dates to retain previous plans for historical reports. '                   'A group added in Settings appears here after Save settings.',
                   11,MUTED,wraplength=950,justify='left').pack(anchor='w',padx=16,pady=(0,14))
        group=self.plan_group.get()
        try:
            day=core.parse_date(self.plan_effective_day.get()).isoformat()
            current,error=core.exception_plan(group,day)
        except Exception as exc:
            self.label(self.content,'Invalid effective date: '+str(exc),12,RED).pack(anchor='w',pady=9)
            return
        plan_panel=self.panel(self.content);plan_panel.pack(fill='x',pady=(0,15))
        if current:
            self.label(plan_panel, f"CURRENT PLAN  ·  {current['planned_total']} people required",17,BLUE,True).pack(anchor='w',padx=16,pady=(14,4))
            self.label(plan_panel, f"Source: {current.get('source','Excel')}  |  "
                       f"From: {current['effective_from']}  |  Through: {current['effective_to']}",
                       11,MUTED,wraplength=930).pack(anchor='w',padx=16,pady=(0,12))
        else:
            self.label(plan_panel,'NO STAFFING PLAN AVAILABLE',16,AMBER,True).pack(anchor='w',padx=16,pady=(14,5))
            self.label(plan_panel,error,11,MUTED,wraplength=930).pack(anchor='w',padx=16,pady=(0,12))
        edit=self.panel(self.content);edit.pack(fill='x',pady=(0,12))
        self.label(edit,'REQUIRED STAFF PER ROLE',14,WHITE,True).pack(anchor='w',padx=16,pady=(15,5))
        self.label(edit,'Enter 0 where a role is not required. Values represent required headcount, not actual attendance.',
                   11,MUTED,wraplength=930).pack(anchor='w',padx=16,pady=(0,12))
        inputs={}
        total_var=ctk.StringVar(value='')
        roles=list(core.EX_METRIC_FIELDS)
        fields_row=ctk.CTkFrame(edit,fg_color='transparent');fields_row.pack(fill='x',padx=16,pady=8)
        for role in roles:
            block=ctk.CTkFrame(fields_row,fg_color=PANEL,corner_radius=8)
            block.pack(side='left',padx=(0,15))
            self.label(block,role,12,WHITE,True).pack(anchor='w',padx=10,pady=(8,3))
            field=ctk.CTkEntry(block,width=130,placeholder_text='0')
            value=(current['components'].get(role,0) if current else 0)
            field.insert(0,str(value))
            field.pack(padx=10,pady=(0,10))
            inputs[role]=field
        def update_preview(*_):
            try:
                values=[int(inputs[role].get()) for role in roles]
                if any(x<0 or x>10000 for x in values):raise ValueError()
                total_var.set(f'TOTAL REQUIRED PER SHIFT: {sum(values)} staff')
            except ValueError:
                total_var.set('Enter valid whole numbers (0-10000).')
        for field in inputs.values():
            field.bind('<KeyRelease>',update_preview)
        update_preview()
        self.label(edit,'',16,TEAL,True,textvariable=total_var).pack(anchor='w',padx=16,pady=(9,13))
        action_row=ctk.CTkFrame(edit,fg_color='transparent');action_row.pack(fill='x',padx=16,pady=(0,16))
        def save_plan():
            try:
                data={role:field.get() for role,field in inputs.items()}
                saved=core.save_manual_staffing_plan(group,day,data)
                self.status_text.set(f'Staffing plan saved: {group}, {saved["planned_total"]} required from {day}')
                self._last_sig=None
                messagebox.showinfo('Plan saved',f'{group}: {saved["planned_total"]} required staff from {day}.\n'
                    'All shortage calculations for dates covered by this plan will now use it.')
                self._render()
            except Exception as exc:
                messagebox.showerror('Cannot save staffing plan',str(exc))
        self.button(action_row,'Save staffing plan',save_plan,width=200,color='#166653').pack(side='left',padx=(0,12))
        versions=core.manual_plan_versions(group)
        matching=next((v for v in versions if v['effective_from']==day),None)
        if matching:
            def remove_version():
                if not messagebox.askyesno('Remove staffing plan version',
                    f'Remove the manual plan for {group} starting {day}?\n\n'
                    'Previous manual plans or the Excel tracker may become the active baseline. This can change historical shortages.'):
                    return
                try:
                    core.delete_manual_staffing_plan(group,day)
                    self._last_sig=None;self._render()
                except Exception as exc:
                    messagebox.showerror('Cannot remove version',str(exc))
            self.button(action_row,'Delete this date’s version',remove_version,width=210,color='#69424D').pack(side='left')
        history=self.panel(self.content);history.pack(fill='x',pady=(0,15))
        self.label(history,'SAVED PLAN HISTORY  /  '+group,13,WHITE,True).pack(anchor='w',padx=16,pady=(13,8))
        if not versions:
            self.label(history,'No manual versions saved yet. Excel is used only where a valid imported plan exists.',
                       11,MUTED).pack(anchor='w',padx=16,pady=(0,14))
        else:
            for item in versions[:12]:
                parts='  ·  '.join(f'{key}: {item["components"].get(key,0)}' for key in roles)
                self.label(history,f"{item['effective_from']}  |  {item['planned_total']} required  |  {parts}",
                           11,WHITE,wraplength=940,justify='left').pack(anchor='w',padx=16,pady=5)
            if len(versions)>12:
                self.label(history,f'{len(versions)-12} older versions not displayed.',11,MUTED).pack(anchor='w',padx=16)
            self.label(history,'Plan edits and deletions are saved in the database audit history.',
                       10,MUTED).pack(anchor='w',padx=16,pady=(4,14))

    def _import_planned_tracker(self):
        source=filedialog.askopenfilename(title='Import visible planned staffing from the KPI tracker',
                filetypes=[('Excel workbook','*.xlsm *.xlsx')])
        if not source:return
        try:
            plans,warnings=core.import_visible_staffing_plans(source)
            cfg=core.configuration();cfg['staffing_tracker_path']=str(Path(source).resolve())
            core.save_configuration(cfg)
            self._plan_source_mtime=Path(source).stat().st_mtime_ns
            messagebox.showinfo('Planned staffing imported',
                f'{len(plans)} non-empty staffing baselines imported from visible sheets.\n'
                f'{len(warnings)} skipped/invalid blocks (see log).\n\n'
                'Only dates covered by the source workbook will be used.')
            self._render()
        except Exception as exc:
            messagebox.showerror('Import failed',str(exc))

    def _safety(self):
        try:core.evaluate_alerts()
        except Exception as e:logging.exception('Alert check: %s',e)
        s=core.summary(self._rows)
        cards=ctk.CTkFrame(self.content,fg_color='transparent');cards.pack(fill='x')
        self._kpi(cards,'Reported near-miss',s['near_miss'],'Not verified distinct incidents',RED)
        # Display Late reports alongside Near Miss in Safety & Alerts.
        # Late is a reported attendance exception, not a safety incident.
        self._kpi(cards,'Late reported',s['late'],'Reported attendance exception values',AMBER)
        # Missing is distinct from a reported zero. Show a completeness rate
        # and the underlying numerator so executives can judge data quality.
        reports = s['reports']
        complete = reports - s['missing_safety']
        completeness = f'{complete / reports:.0%}' if reports else '-'
        completeness_detail = (f'{complete:,} of {reports:,} reports include both safety fields'
                               if reports else 'No reports in selected period')
        self._kpi(cards, 'Safety data completeness', completeness,
                  completeness_detail, TEAL if reports and complete == reports else AMBER)
        self._title(self.content,'Alert queue','Acknowledge or close locally; alert rules use rolling 2-day monitoring windows.')
        # Include Late alerts from the existing configurable threshold rule,
        # alongside Near Miss, quality and missing-report alerts.
        # Alert rules use the existing rolling two-day evaluation period.
        items=core.alerts();p=self.panel(self.content);p.pack(fill='x',pady=8)
        for r in items[:25]:
            row=ctk.CTkFrame(p,fg_color=BG,corner_radius=9);row.pack(fill='x',padx=9,pady=5)
            color=RED if r['level']=='High' else AMBER if r['level']=='Medium' else BLUE
            self.label(row,f"●  {r['title']}  •  {r['status']}",12,color,True).pack(anchor='w',padx=12,pady=(7,0))
            self.label(row,r['detail'],11,MUTED,wraplength=850,justify='left').pack(anchor='w',padx=12,pady=(2,8))
            ctrl=ctk.CTkFrame(row,fg_color='transparent');ctrl.pack(anchor='e',padx=12,pady=(0,7))
            owner=ctk.CTkEntry(ctrl,placeholder_text='Owner',width=100);owner.insert(0,r['owner'] or '');owner.pack(side='left',padx=4)
            self.button(ctrl,'Acknowledge',lambda id=r['id'],v=owner:self._change_alert(id,'Acknowledged',v.get()),width=105).pack(side='left',padx=3)
            self.button(ctrl,'Close',lambda id=r['id'],v=owner:self._change_alert(id,'Closed',v.get()),width=70,color='#286D5B').pack(side='left',padx=3)
        if not items:
            self.label(p,'✓  No alerts recorded',14,TEAL,True).pack(anchor='w',padx=16,pady=(15,6))
            self.label(p,'Alerts will appear here after WhatsApp collection or file import. '
                       'No alerts recorded does not prove all operational risks are absent.',
                       11,MUTED,wraplength=900).pack(anchor='w',padx=16,pady=(0,18))
        # The evidence section must always render something visible. An empty
        # section previously looked broken, particularly with date filters.
        # Show only reports with a positive Near Miss or Late numeric value.
        evidence_rows = [r for r in self._rows
                         if (r.get('near_miss') or 0) > 0
                         or (r.get('late') or 0) > 0]
        self._title(self.content, 'Near-miss and Late report evidence',
                    'Filtered to reports containing positive Near Miss or Late values.')
        self.label(self.content,
                   f'{len(evidence_rows):,} matching evidence report(s) from '
                   f'{len(self._rows):,} records in the selected period and group.',
                   12, TEAL if evidence_rows else AMBER).pack(
                       anchor='w', pady=(2, 10))
        if evidence_rows:
            # A scrollable table also works for >80 evidence records.
            self._report_table(self.content, evidence_rows, limit=200,
                               title='Report evidence')
        else:
            self._empty_state(self.content,'No positive Near Miss or Late evidence in this view',
                'No matching report contains a positive numeric Near Miss or Late value. '
                'This does not confirm zero incidents: check missing fields and widen the period if needed.',
                'Show all dates & groups',self._reset_view_filters,kind='neutral',compact=True)

    def _change_alert(self,id,status,owner):
        core.update_alert(id,status,owner);self._render()

    def _health(self):
        state=core.monitor_state()
        self._title(self.content,'Collection and persistence status')
        self.label(self.content,'The connection indicator turns green only after authenticated WhatsApp chat access is confirmed.',
                   11,MUTED).pack(anchor='w',pady=(0,9))
        k=self.panel(self.content);k.pack(fill='x')
        for name,value in [('Monitor',state['status']),('Last scan',state['last_checked'] or '-'),
                           ('Last success',state['last_success'] or '-'),('Last error',state['last_error'] or 'None'),
                           ('SQLite file',str(core.DB)),('Database size',f"{core.DB.stat().st_size/1024:.1f} KB")]:
            self.label(k,f'{name}:  {value}',12,TEAL if name=='Monitor' and value=='Connected' else MUTED,wraplength=920,justify='left').pack(anchor='w',padx=16,pady=7)
        diag=collector_diagnostics()
        self._title(self.content,'Live capture diagnostics (most recent group scan)')
        for name,key in [('Group','group_name'),('Last capture scan','updated_at'),
                         ('Message DOM containers','visible_messages'),('Metric-bearing messages','metric_messages'),
                         ('Newly saved','saved'),('Already stored','duplicate'),
                         ('Unrecognised timestamps','missing_timestamp'),('Collector note','last_note')]:
            self.label(self.content,f'{name}: {diag.get(key) or 0}',12,MUTED,
                       wraplength=920,justify='left').pack(anchor='w',padx=12,pady=3)
        self.label(self.content,'If the collector says No groups matched, set the exact group names in Settings. '
                   'If metrics stay at 0, review your WhatsApp message text against the parsing patterns.',
                   12,AMBER,wraplength=900).pack(anchor='w',pady=8)
        self._title(self.content,'Data tools')
        buttons=ctk.CTkFrame(self.content,fg_color='transparent');buttons.pack(fill='x',pady=6)
        # Keep every data action on one toolbar. The backup button sits
        # immediately beside Load demo as requested; it does not move data.
        for name,method in [('Import CSV',self._import_csv),('Import Excel',self._import_excel),
                            ('Export CSV',self._csv),('Export Excel',self._excel),
                            ('Load demo',self._demo)]:
            self.button(buttons,name,method,width=118).pack(side='left',padx=4)
        self.button(buttons,'Backup SQLite',self._backup,width=135,color='#286D5B').pack(side='left',padx=4)
        self._title(self.content,'Local audit trail (latest 80 entries)')
        p=self.panel(self.content);p.pack(fill='x')
        with core.connect() as c:logs=c.execute('SELECT at,action,detail FROM audit ORDER BY id DESC LIMIT 80').fetchall()
        if not logs:self.label(p,'No audit events yet',12,MUTED).pack(padx=15,pady=18)
        for row in logs:
            self.label(p,f"{row['at']}  |  {row['action']}  |  {row['detail']}",11,MUTED,wraplength=970,justify='left').pack(anchor='w',padx=15,pady=5)
        self._title(self.content,'Collection log')
        self.logs=ctk.CTkTextbox(self.content,height=150,fg_color=PANEL);self.logs.pack(fill='x',pady=(0,14))

    def _settings(self):
        """Persist user preferences and the structured group registry.

        All group edits are staged in the UI. Save settings commits registry
        metadata AND updates the backwards-compatible flat list consumed by
        WhatsAppMonitor.start(). Existing chat parsing remains untouched.
        """
        cfg=core.configuration()
        self._title(self.content,'Account Management',
                    'Create administrator/operator logins and manage access from Settings, not Exceptions.')
        account_controls=self.panel(self.content);account_controls.pack(fill='x',pady=(0,12))
        account_row=ctk.CTkFrame(account_controls,fg_color='transparent')
        account_row.pack(fill='x',padx=15,pady=13)
        self.label(account_row,'Signed in: '+self.exception_session['username']+'  •  Administrator',
                   12,TEAL,True).pack(side='left',padx=(0,18))
        # Expanding/collapsing accounts does not rebuild the Settings form,
        # protecting unsaved registry changes from accidental loss.
        account_details=ctk.CTkFrame(self.content,fg_color='transparent')
        is_open=getattr(self,'_show_settings_accounts',False)
        def toggle_account_management():
            self._show_settings_accounts=not self._show_settings_accounts
            if self._show_settings_accounts:
                account_details.pack(fill='x',after=account_controls)
                if not account_details.winfo_children():
                    self._exception_manage_users(parent=account_details)
            else:
                account_details.pack_forget()
            accounts_button.configure(text='Hide accounts' if self._show_settings_accounts
                                      else 'Manage accounts')
        accounts_button=self.button(account_row,
                  'Hide accounts' if is_open else 'Manage accounts',
                  toggle_account_management,width=150)
        accounts_button.pack(side='right')
        if is_open:
            account_details.pack(fill='x',after=account_controls)
            self._exception_manage_users(parent=account_details)
        self.label(self.content,'Monitoring and group settings are saved locally in data/settings.json.',
                   12,MUTED).pack(anchor='w',pady=8)
        p=self.panel(self.content);p.pack(fill='x',pady=12)
        # Structured group management replaces the old free-text textarea.
        # A group can be PAUSED without deleting its history or changing the
        # exact-match logic used by the original WhatsApp monitoring engine.
        self.label(p,'WHATSAPP MONITORING  /  GROUP REGISTRY',14,WHITE,True).pack(anchor='w',padx=16,pady=(16,4))
        self.label(p,'Search and manage exact WhatsApp group titles. Paused groups are not monitored; historical reports remain available.',
                   11,MUTED,wraplength=970,justify='left').pack(anchor='w',padx=16,pady=(0,12))
        registry = core.group_registry(cfg)  # Local staged edits until Save settings.
        search = ctk.StringVar(value='')
        entry_name = ctk.StringVar(value='')
        status_text = ctk.StringVar(value='')
        search_row=ctk.CTkFrame(p,fg_color='transparent')
        search_row.pack(fill='x',padx=16,pady=(0,8))
        self.label(search_row,'Search groups',11,MUTED).pack(side='left',padx=(0,8))
        ctk.CTkEntry(search_row,textvariable=search,width=275,placeholder_text='Filter by group name').pack(side='left')
        self.label(search_row,'Monitoring state: Active / Paused',11,MUTED).pack(side='right',padx=6)
        # The last report is read from the database, not inferred from online
        # status; Active means selected for monitoring, not connected.
        with core.connect() as connection:
            last_reports = {r['group_name'].casefold(): r['last_at']
                            for r in connection.execute('SELECT group_name, MAX(event_at) AS last_at FROM reports GROUP BY group_name')}
        self._style_table('GroupRegistry',rowheight=34,fontsize=10)
        registry_frame=ctk.CTkFrame(p,fg_color='transparent')
        registry_frame.pack(fill='x',padx=16,pady=(0,8))
        tree=ttk.Treeview(registry_frame,columns=('name','state','last'),show='headings',
                          selectmode='browse',height=7,style='GroupRegistry.Treeview')
        for col,title,width in [('name','Group name (exact WhatsApp title)',400),
                                ('state','Monitoring',110),('last','Last stored report',185)]:
            tree.heading(col,text=title)
            tree.column(col,width=width,minwidth=90,anchor='w',stretch=(col=='name'))
        scrollbar=ttk.Scrollbar(registry_frame,orient='vertical',command=tree.yview,
                                style='Ops.Vertical.TScrollbar')
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side='left',fill='x',expand=True)
        scrollbar.pack(side='right',fill='y')
        tree.tag_configure('paused', foreground=MUTED)
        tree.tag_configure('active', foreground=WHITE)

        def selected_group_index():
            selected = tree.selection()
            return int(selected[0]) if selected else None

        def redraw_registry(select_index=None):
            current_index = select_index if select_index is not None else selected_group_index()
            for item in tree.get_children():
                tree.delete(item)
            query = search.get().strip().casefold()
            for index,item in enumerate(registry):
                if query and query not in item['name'].casefold():
                    continue
                state = 'ACTIVE' if item['enabled'] else 'PAUSED'
                last = last_reports.get(item['name'].casefold())
                displayed = last.replace('T',' ')[:16] if last else 'No reports stored'
                tree.insert('',tk.END,iid=str(index),
                            values=(item['name'],state,displayed),
                            tags=('active' if item['enabled'] else 'paused',))
            self._stripe_rows(tree)
            active = len(core.active_group_names(registry))
            status_text.set(f'{len(registry)} registered  •  {active} active  •  {len(registry)-active} paused'
                            '  •  Changes take effect after saving and restarting monitoring')
            if current_index is not None and tree.exists(str(current_index)):
                tree.selection_set(str(current_index));tree.focus(str(current_index))

        def pick_group(_=None):
            index=selected_group_index()
            if index is not None:
                entry_name.set(registry[index]['name'])

        tree.bind('<<TreeviewSelect>>',pick_group)
        search.trace_add('write',lambda *_:redraw_registry())
        editor=ctk.CTkFrame(p,fg_color='transparent')
        editor.pack(fill='x',padx=16,pady=(5,4))
        self.label(editor,'Group title',11,MUTED).pack(side='left',padx=(0,8))
        ctk.CTkEntry(editor,textvariable=entry_name,placeholder_text='Exact name displayed in WhatsApp',
                     width=345).pack(side='left',padx=(0,8))

        def add_group():
            try:
                name=core.validated_group_title(entry_name.get())
                if any(x['name'].casefold()==name.casefold() for x in registry):
                    raise ValueError('This group is already registered. Select it to edit or reactivate.')
                registry.append({'name':name,'enabled':True})
                search.set('');redraw_registry(len(registry)-1)
                entry_name.set('')
            except ValueError as exc:messagebox.showwarning('Cannot add group',str(exc))

        def rename_group():
            index=selected_group_index()
            if index is None:
                messagebox.showinfo('Select a group','Select a row to rename.');return
            try:
                name=core.validated_group_title(entry_name.get())
                if any(i!=index and x['name'].casefold()==name.casefold() for i,x in enumerate(registry)):
                    raise ValueError('A group with this name already exists.')
                previous=registry[index]['name']
                if name != previous and not messagebox.askyesno('Change monitored group name',
                    f'Change the monitored title from:\n{previous}\n\nto:\n{name}\n\n'
                    'Existing reports retain their original group name. Continue?'):
                    return
                registry[index]['name']=name
                redraw_registry(index)
            except ValueError as exc:messagebox.showwarning('Cannot rename group',str(exc))

        def toggle_group():
            index=selected_group_index()
            if index is None:
                messagebox.showinfo('Select a group','Select the row to enable or pause.');return
            registry[index]['enabled']=not registry[index]['enabled']
            redraw_registry(index)

        def remove_group():
            index=selected_group_index()
            if index is None:
                messagebox.showinfo('Select a group','Select a row to remove.');return
            name=registry[index]['name']
            if messagebox.askyesno('Remove group from monitoring',
                    f'Remove "{name}" from the monitoring register?\n'
                    'Previously collected reports will NOT be deleted.'):
                registry.pop(index)
                entry_name.set('');redraw_registry()

        self.button(editor,'Add group',add_group,width=106,color='#166653').pack(side='left',padx=3)
        action_row=ctk.CTkFrame(p,fg_color='transparent');action_row.pack(fill='x',padx=16,pady=(5,7))
        self.button(action_row,'Rename selected',rename_group,width=145).pack(side='left',padx=(0,8))
        self.button(action_row,'Enable / Pause',toggle_group,width=135).pack(side='left',padx=(0,8))
        self.button(action_row,'Remove selected',remove_group,width=145,color='#69424D').pack(side='left')
        self.label(p,'Changes are staged until you select Save settings below. Stop and restart WhatsApp monitoring to apply a changed group list.',
                   11,AMBER,wraplength=965,justify='left').pack(anchor='w',padx=16,pady=(4,6))
        self.label(p,'',11,MUTED,textvariable=status_text).pack(anchor='w',padx=16,pady=(0,12))
        redraw_registry()
        fields=[]
        for key,title in [('sweep_seconds','Rescan interval (seconds)'),('alert_late_threshold','Late count alert threshold'),
                           ('alert_near_miss_threshold','Near-miss alert threshold'),('alert_missing_hours','Team silence alert after hours')]:
            row=ctk.CTkFrame(p,fg_color='transparent');row.pack(fill='x',padx=16,pady=8)
            self.label(row,title,12,MUTED).pack(side='left')
            inp=ctk.CTkEntry(row,width=110);inp.insert(0,str(cfg[key]));inp.pack(side='right');fields.append((key,inp))
        automatic=ctk.CTkCheckBox(p,text='Create a local daily executive PDF when this desktop app is running',text_color=WHITE)
        automatic.pack(anchor='w',padx=16,pady=10)
        if cfg.get('auto_pdf_daily',False):automatic.select()
        def save():
            try:
                cfg['auto_pdf_daily']=bool(automatic.get())
                # New structured registry remains backwards compatible with the
                # original collector's list of exact enabled group names.
                enabled=core.active_group_names(registry)
                if not enabled:raise ValueError('Enable at least one group before saving.')
                cfg['group_registry']=[dict(item) for item in registry]
                cfg['groups']=enabled
                for key,inp in fields:
                    val=int(inp.get());
                    if val<1:raise ValueError(f'{key} must be at least 1')
                    cfg[key]=val
                core.save_configuration(cfg);core.audit('SETTINGS_CHANGED','Updated local monitoring and alert thresholds')
                messagebox.showinfo('Settings saved',
                    'Monitoring settings and group register saved.\n\n'
                    'Stop and restart WhatsApp monitoring to apply group additions, pauses or renames.'
                    if self.monitor.active else
                    'Settings saved. The next WhatsApp monitoring session will use the enabled group list.')
            except Exception as e:messagebox.showerror('Invalid setting',str(e))
        self.button(p,'Save settings',save,width=170,color='#166653').pack(anchor='w',padx=16,pady=18)
        self._title(self.content,'Staffing plans per WhatsApp group',
                    'Manage each group’s required T002/T003, PWT, SPL and PL headcount in a separate planning screen.')
        manual_box=self.panel(self.content);manual_box.pack(fill='x',pady=(0,16))
        self.label(manual_box,'Set staffing requirements without editing Excel. New saved WhatsApp groups appear automatically.',
                   12,MUTED,wraplength=930).pack(anchor='w',padx=16,pady=(13,8))
        self.button(manual_box,'Manage Staffing Plans',lambda:self._navigate('Staffing Plans'),
                    color='#166653',width=230).pack(anchor='w',padx=16,pady=(0,14))
        self._title(self.content,'Optional Excel planning import - visible worksheets only',
                    'Workbook column C remains a fallback for dates without an active manually configured staffing plan.')
        plan_box=self.panel(self.content);plan_box.pack(fill='x',pady=(0,15))
        with core.connect() as c:
            plan_count=c.execute('SELECT COUNT(*) FROM staffing_plans').fetchone()[0]
        self.label(plan_box,f"{plan_count} dated gang plans available. Workbook: {cfg.get('staffing_tracker_path') or 'not connected'}",
                   12,MUTED,wraplength=920).pack(anchor='w',padx=16,pady=(13,9))
        self.button(plan_box,'Import visible planned staffing (.xlsm)',
                    self._import_planned_tracker,color='#166653',width=300).pack(anchor='w',padx=16,pady=(0,16))
        self._title(self.content, 'Appearance', 'The setting applies to every desktop tab and chart.')
        theme_panel = self.panel(self.content)
        theme_panel.pack(fill='x',pady=(0,15))
        theme_choice = ctk.StringVar(value=self.theme_mode)
        self.label(theme_panel,'Theme',12,MUTED).pack(side='left',padx=16,pady=14)
        ctk.CTkOptionMenu(theme_panel,values=['Light','Dark'],variable=theme_choice,
                          command=self._set_theme,width=120,
                          fg_color=PANEL,text_color=WHITE,
                          button_color=('#B8D4ED' if self.theme_mode == 'Light' else '#254C72')).pack(side='left',padx=8,pady=12)
        # Keep configuration and session in the project; only operations.db moves.
        self._title(self.content, 'Main database directory',
                    'Move the active SQLite file safely to a different local folder. The old copy is retained as a backup.')
        storage = self.panel(self.content)
        storage.pack(fill='x',pady=(0,15))
        self.db_location_label = self.label(storage, str(core.DB.resolve()),12,MUTED,wraplength=920,justify='left')
        self.db_location_label.pack(anchor='w',padx=16,pady=(14,7))
        self.button(storage,'Change database folder',self._choose_database_directory,width=230).pack(anchor='w',padx=16,pady=(0,15))
        self.label(self.content,'WhatsApp browser login data is stored locally in the project data folder. Restrict access to this folder.',12,AMBER,wraplength=930).pack(anchor='w',pady=12)

    def _choose_database_directory(self):
        """Move operations.db with a verified SQLite backup, never a raw file copy.

        Changing database paths while the browser monitor is running could
        split an incoming batch across two files, so require a stopped monitor.
        """
        if (self.monitor.active or self._reconcile_busy or
                (collector.worker_loop is not None and collector.worker_loop.is_running())):
            messagebox.showwarning('Stop monitoring first',
                'Stop WhatsApp monitoring and let CSV reconciliation and browser shutdown finish before moving the database.')
            return
        directory = filedialog.askdirectory(title='Choose a new folder for operations.db',
                                             initialdir=str(core.DB.parent))
        if not directory:
            return
        new_file = (Path(directory).expanduser().resolve() / 'operations.db')
        if new_file == core.DB.resolve():
            return
        if not messagebox.askyesno('Move main database',
                f'Create a verified copy of the active database at:\n{new_file}\n\n'
                f'The original will remain at:\n{core.DB}\n\nProceed?'):
            return
        try:
            result = core.relocate_database(directory)
            self._last_sig = None
            self.db_location_label.configure(text=str(result))
            messagebox.showinfo('Database relocated',
                                f'Now using:\n{result}\n\nThe previous database remains as a backup.')
        except Exception as exc:
            messagebox.showerror('Unable to change database', str(exc))

    def _start(self):
        if self.monitor.start():messagebox.showinfo('Monitoring','The local WhatsApp Web browser will open. Scan QR if prompted.')
        else:messagebox.showinfo('Monitoring','Collector is already running.')
    def _stop(self):self.monitor.stop()
    @staticmethod
    def _configure_text(label, text):
        if label is not None and label.winfo_exists() and str(label.cget('text'))!=str(text):
            label.configure(text=str(text))

    def _update_kpi_in_place(self,key,value,sub=None):
        pair=self._live_widgets.get(key)
        if pair:
            self._configure_text(pair[0],value)
            if sub is not None:self._configure_text(pair[1],sub)

    def _update_executive_in_place(self):
        """Apply newly committed SQLite data without destroying any Tk widgets.

        Called on Tk's event loop only. Preserve scroll position, focus, chart
        canvas, table selection, and the existing window geometry.
        """
        if self.page!='Executive' or 'counter' not in self._live_widgets:return
        rows=self._selected_rows()
        self._rows=rows
        if rows and self._live_widgets.get('report_empty_titles'):
            for label in self._live_widgets.pop('report_empty_titles'):
                if label.winfo_exists():label.pack_forget()
        if rows and self._live_widgets.get('executive_empty'):
            placeholder=self._live_widgets.pop('executive_empty')
            if placeholder.winfo_exists():placeholder.pack_forget()
        s=core.summary(rows,core.configuration()['groups'])
        prior=core.compare(rows,*self._date_bounds())
        with core.connect() as con:
            all_count=con.execute('SELECT COUNT(*) FROM reports').fetchone()[0]
        with core.connect() as con:
            sig_row=con.execute('SELECT COUNT(*),MAX(id) FROM reports').fetchone()
        self._last_sig=(tuple(sig_row),core.exception_data_signature(),self.page)
        self._configure_text(self._live_widgets['counter'],
            f'LIVE DATA  •  {s["reports"]:,} displayed / {all_count:,} in database  •  Period: {self.period.get()}  •  Team: {self.group.get()}')
        note=(f"{prior['reports']:+.1f}% vs previous period" if prior and prior['reports'] is not None else 'Comparison unavailable')
        self._update_kpi_in_place('report_kpi',f"{s['reports']:,}",note)
        self._update_kpi_in_place('team_kpi',s['teams'],f"{len(s['missing_groups'])} configured team(s) not represented")
        self._update_kpi_in_place('near_kpi',s['near_miss'])
        complete,total,pct=core.safety_completeness(rows)
        self._update_kpi_in_place('complete_kpi',f'{pct:.1f}%' if pct is not None else '-',
                                  f'{complete} of {total} reports have both fields')
        issues=[f"{len(core.alerts('Open'))} open alerts",
                f"{s['missing_safety']} reports missing safety values" if s['missing_safety'] else 'Safety values complete in received reports',
                f"{len(s['missing_groups'])} teams without a captured report",
                f"Last captured: {s['last_event'][:16] if s['last_event'] else 'No records'}"]
        for label,text in zip(self._live_widgets.get('issues',[]),issues):
            self._configure_text(label,'●  '+text)
        signature=tuple((r['id'],r['near_miss'],r['group_name']) for r in rows)
        if signature!=self._live_widgets.get('safety_signature'):
            self._live_widgets['safety_signature']=signature
            bar=self._live_widgets.get('safety_chart')
            if bar:
                fig,ax,canvas=bar
                self._draw_safety_bars(ax,rows)
                fig.tight_layout(pad=1.5);canvas.draw_idle()
            pie=self._live_widgets.get('safety_pie')
            if pie:
                fig,ax,canvas=pie
                per_gang=core.near_miss_by_group(rows)
                core.original_style_pie(ax,list(per_gang),list(per_gang.values()),
                    (['#d35400','#2980b9','#27ae60','#8e44ad','#f39c12']*max(1,len(per_gang))),
                    dark=self.theme_mode == 'Dark')
                fig.tight_layout(pad=1.2);canvas.draw_idle()
        exception_refresh=self._live_widgets.get('ex_refresh')
        if exception_refresh: exception_refresh()
        tree=self._live_widgets.get('reports_tree')
        if tree and tree.winfo_exists():
            wanted=rows[:80]
            signature=tuple(r['id'] for r in wanted)
            if signature!=self._live_widgets.get('table_signature'):
                self._live_widgets['table_signature']=signature
                mapping=self._live_widgets['reports_mapping']
                # Reuse row widgets whenever possible, and keep the selected row.
                existing={r['id']:item for item,r in mapping.items()}
                selected_ids={mapping[item]['id'] for item in tree.selection() if item in mapping}
                for item,r in list(mapping.items()):
                    if r['id'] not in signature:
                        tree.delete(item);mapping.pop(item,None)
                for i,r in enumerate(wanted):
                    vals=[r['event_at'].replace('T',' '),r['group_name'],r.get('sender') or '-']+[r[k] if r[k] is not None else '-' for k in ('t002_t003','pwt','spl','pl','near_miss')]
                    item=existing.get(r['id'])
                    if item and tree.exists(item):
                        if tuple(str(v) for v in tree.item(item,'values'))!=tuple(str(v) for v in vals):tree.item(item,values=vals)
                        tree.move(item,'',i)
                    else:
                        item=tree.insert('',i,values=vals)
                        mapping[item]=r
                self._stripe_rows(tree)
                tree.selection_set([item for item,r in mapping.items() if r['id'] in selected_ids])

    def _schedule_dashboard_refresh(self):
        """Debounce bursts, and always update Tk from the main GUI thread."""
        if self._refresh_pending:
            return
        self._refresh_pending=True
        self.after(75, self._apply_dashboard_refresh)

    def _apply_dashboard_refresh(self):
        self._refresh_pending=False
        if self.page=='Overview':
            self._update_overview_in_place()
        elif self.page=='Executive':
            self._update_executive_in_place()
        elif self.page=='Workforce':
            # Transition once from the empty view to a live chart when the
            # first matching report arrives; otherwise keep no-flicker updates.
            if not self._rows and self._selected_rows():
                self._render()
            else:
                refresh = self._live_widgets.get('analysis_refresh')
                if refresh: refresh()
        # Other views refresh when revisited. Avoid rebuilding the screen on
        # every insert (the previous behaviour caused severe visual flicker).

    def _poll_planned_tracker(self):
        source = core.configuration().get('staffing_tracker_path')
        if not source or self._plan_import_busy:
            return
        path = Path(source)
        if not path.is_file():
            return
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            return
        if modified == self._plan_source_mtime:
            return
        self._plan_import_busy=True
        def ingest():
            try:
                plans,warnings=core.import_visible_staffing_plans(path)
                self._plan_source_mtime=modified
                self.queue.put(('planned_imported',len(plans),len(warnings)))
            except Exception as exc:
                logging.warning('Unable to refresh workbook staffing plans: %s',exc)
                self.queue.put('Planned staffing update failed: '+str(exc))
            finally:
                self._plan_import_busy=False
        threading.Thread(target=ingest,daemon=True).start()

    def _poll(self):
        """Run message notifications, status LED and backup checks on the Tk thread."""
        import time
        new_reports=False
        while True:
            try: msg=self.queue.get_nowait()
            except queue.Empty: break
            if isinstance(msg, tuple) and msg and msg[0]=='planned_imported':
                new_reports=True
                self.status_text.set(f'{msg[1]} planned staffing baselines refreshed ({msg[2]} notes)')
                continue
            if isinstance(msg, tuple) and msg and msg[0]=='reports_saved':
                new_reports=True
                self.status_text.set(f'{msg[1]} new records saved from {msg[2]}')
                continue
            self.status_text.set(str(msg))
            if self.page=='System Health' and hasattr(self,'logs') and self.logs.winfo_exists():
                self.logs.insert('end',f'{datetime.now():%H:%M:%S}  {msg}\n')
                self.logs.see('end')
        if new_reports:
            if self.page=='Exceptions':
                if not getattr(self,'_exception_draft_dirty',False):
                    self._render()  # New actual attendance shown immediately without losing edits.
                else:
                    self.status_text.set('WhatsApp updates received. Save group changes before refreshing Exceptions.')
            else:
                self._schedule_dashboard_refresh()
        # Fallback for imports/external database writers. This also detects
        # changes when the collector cannot send an event to this GUI process.
        now=time.monotonic()
        if now >= self._next_fallback_check:
            self._next_fallback_check=now+0.8
            if self.page in ('Overview','Executive','Workforce','Safety & Alerts'):
                with core.connect() as c:
                    row=c.execute('SELECT COUNT(*), MAX(id) FROM reports').fetchone()
                sig=(tuple(row),core.exception_data_signature(),self.page)
                if self._last_sig != sig:
                    self._last_sig=sig
                    self._schedule_dashboard_refresh()
        if now >= self._next_plan_check:
            self._next_plan_check=now+30.0
            self._poll_planned_tracker()
        if now >= self._next_connection_led_check:
            self._next_connection_led_check = now + 0.5
            self._update_connection_led()
        if now >= self._next_daily_check:
            self._next_daily_check=now+30.0
            self._maybe_daily_report()
        self.after(100,self._poll)

    def _backup(self):
        import sqlite3
        path=filedialog.asksaveasfilename(defaultextension='.db',filetypes=[('SQLite database','*.db')])
        if not path:return
        try:
            with core.connect() as source, sqlite3.connect(path) as target:
                source.backup(target)
            core.audit('DB_BACKUP',Path(path).name)
            messagebox.showinfo('Backup saved',f'Consistent SQLite snapshot created at:\n{path}')
        except Exception as e:messagebox.showerror('Backup failed',str(e))

    def _maybe_daily_report(self):
        cfg=core.configuration()
        if not cfg.get('auto_pdf_daily'):return
        now=datetime.now()
        if now.hour < 18:return # generate at first poll at/after 18:00 local time
        day=now.date().isoformat()
        if self._last_report_day==day:return
        target=core.DATA / 'reports';target.mkdir(exist_ok=True)
        destination=target / f'executive_{day}.pdf'
        if destination.exists():
            self._last_report_day=day;return
        try:
            rows=core.filtered(now.date(),now.date())
            core.export_pdf(destination,rows,'All groups - daily',
                exception_rows=core.exception_dashboard(now.date(),now.date(),'All Groups')['rows'])
            self._last_report_day=day
        except Exception as e:logging.exception('Automatic local PDF report failed: %s',e)

    def _import(self,extension):
        path=filedialog.askopenfilename(filetypes=[('Reporting file',f'*.{extension}')]);
        if not path:return
        try:
            result=core.import_csv(path) if extension=='csv' else core.import_excel(path)
            core.evaluate_alerts();added,dupes,errors=result
            self.status_text.set(f'Import complete · {added} new · {dupes} duplicates · {len(errors)} rejected')
            messagebox.showinfo('Import complete',f'Added: {added}\nDuplicates skipped: {dupes}\nRejected: {len(errors)}'+(f'\n\nFirst errors:\n'+ '\n'.join(errors[:5]) if errors else ''))
            self._last_sig=None;self._render()
        except Exception as e:messagebox.showerror('Import failed',str(e))
    def _import_csv(self):self._import('csv')
    def _import_excel(self):self._import('xlsx')
    def _save(self,ext,method):
        path=filedialog.asksaveasfilename(defaultextension='.'+ext,filetypes=[(ext.upper(),f'*.{ext}')]);
        if not path:return
        try:
            self.status_text.set('Preparing '+ext.upper()+' export…')
            self.update_idletasks()
            rows=self._selected_rows()
            method(path,rows)
            try:self._register_local_report(path)
            except Exception as catalog_exc:
                logging.warning('Export completed but report was not indexed: %s',catalog_exc)
            self.status_text.set('Export completed · '+Path(path).name)
            messagebox.showinfo('Export successful',f'Executive report saved to:\n{path}\n\nIncludes WhatsApp and staffing exception records for the selected scope.')
        except Exception as e:messagebox.showerror('Export failed',str(e))
    def _csv(self):self._save('csv',core.export_csv)
    def _excel(self):self._save('xlsx',core.export_excel)
    def _pdf(self):
        # WhatsApp reports are filtered by message date; exceptions by the
        # corresponding night-shift service date. Include early entries even
        # where no WhatsApp attendance report has arrived yet.
        start,end=self._date_bounds()
        group=self.group.get()
        def run(path,rows):
            exceptions=core.exception_dashboard(start,end,group)['rows']
            core.export_pdf(path,rows,scope=f'{group} / {self.period.get()}',exception_rows=exceptions)
        self._save('pdf',run)
    def _html(self):
        start,end=self._date_bounds()
        group=self.group.get()
        def run(path,rows):
            exceptions=core.exception_dashboard(start,end,group)['rows']
            core.export_interactive_html(path,rows,scope=f'{group} / {self.period.get()}',exception_rows=exceptions)
        self._save('html',run)
    def _demo(self):
        if not messagebox.askyesno('Synthetic demonstration','Add labelled SYNTHETIC DEMO records to this local database?\n\nUse a new empty database for production.'):
            return
        count=core.demo_data();core.evaluate_alerts()
        messagebox.showinfo('Demo data',f'Added {count} labelled synthetic reports.');self._last_sig=None;self._navigate('Executive')
    def _close(self):
        if self.monitor.active:
            if not messagebox.askyesno('Close monitoring','Stopping the application will also stop monitoring. Continue?'):return
            self.monitor.stop()
        self.destroy()
