"""CustomTkinter desktop GUI for Operational Intelligence."""
from collector import *  # Original collector/UI integration contract
import collector  # Mutable worker state must be read from this module, not a star-import copy.
import core

# Local CustomTkinter executive desktop UI

"""Local desktop executive dashboard. Run: python app.py"""
import logging, queue, tkinter as tk
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
        self.title('Operational Intelligence | Executive Command Centre')
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
        if not is_running:
            core.set_monitor_state('Disconnected')
        self.page='Workforce';self._chart=None;self._rows=[];self._last_sig=None;self._last_report_day=None;self._refresh_pending=False;self._next_fallback_check=0.0;self._next_daily_check=0.0;self._next_connection_led_check=0.0;self._live_widgets={};self._visible_record_ids=();self._last_live_render=0.0
        self.period=ctk.StringVar(value='All time');self.group=ctk.StringVar(value='All Groups')
        self.custom_start=ctk.StringVar(value=(date.today()-timedelta(days=6)).isoformat());self.custom_end=ctk.StringVar(value=date.today().isoformat())
        self.status_text=ctk.StringVar(value='Local database ready')
        self._reconcile_busy=False
        self._layout();self._navigate('Workforce');self.after(100,self._poll);self.after(250,self._reconcile_legacy)
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
                             corner_radius=9,width=width,height=34,**kw)
    def panel(self,parent,**kwargs):return ctk.CTkFrame(parent,fg_color=PANEL,corner_radius=14,border_width=1,border_color=BORDER,**kwargs)
    def _layout(self):
        self.grid_columnconfigure(1,weight=1);self.grid_rowconfigure(0,weight=1)
        side=ctk.CTkFrame(self,fg_color=NAV,corner_radius=0,width=228)
        side.grid(row=0,column=0,sticky='nsew');side.grid_propagate(False)
        self.label(side,'◈   OPERATIONS',19,BLUE,True).pack(anchor='w',padx=21,pady=(30,4))
        self.label(side,'INTELLIGENCE  /  DESKTOP',10,MUTED,True).pack(anchor='w',padx=22,pady=(0,32))
        self.nav={}
        for name,ico in [('Workforce','♙'),('Executive','▦'),('Safety & Alerts','⚑'),('System Health','◉'),('Settings','⚙')]:
            b=self.button(side,f'{ico}    {name}',lambda n=name:self._navigate(n),width=190,color='transparent',anchor='w')
            b.pack(padx=17,pady=5);self.nav[name]=b
        self.label(side,'COLLECTION CONTROL',10,MUTED,True).pack(anchor='w',padx=22,pady=(35,8))
        self.button(side,'▶  Start WhatsApp',self._start,color='#166653',width=188).pack(pady=5)
        self.button(side,'■  Stop monitoring',self._stop,color='#69424D',width=188).pack(pady=5)
        self.label(side,'Requires WhatsApp Web sign-in',10,MUTED,wraplength=180).pack(padx=16,pady=9)
        self.main=ctk.CTkFrame(self,fg_color=BG,corner_radius=0)
        self.main.grid(row=0,column=1,sticky='nsew');self.main.grid_columnconfigure(0,weight=1)
        bar=ctk.CTkFrame(self.main,fg_color='transparent')
        bar.grid(row=0,column=0,sticky='ew',padx=22,pady=(20,8))
        self.heading=self.label(bar,'Workforce & Attendance',26,WHITE,True);self.heading.pack(side='left')
        # Top-right connectivity uses live monitor state, never database state.
        self.connection_led=self.label(bar,'●  DISCONNECTED',11,RED,True)
        self.connection_led.pack(side='right',padx=8)
        self.button(bar, '☀  Light' if self.theme_mode == 'Dark' else '☾  Dark',
                    self._toggle_theme,width=90).pack(side='right',padx=10)
        self._update_connection_led()
        self.content=ctk.CTkScrollableFrame(self.main,fg_color='transparent')
        self.content.grid(row=1,column=0,sticky='nsew',padx=20,pady=8)
        self.main.grid_rowconfigure(1,weight=1)
        foot=ctk.CTkFrame(self.main,fg_color=NAV,height=29,corner_radius=0)
        foot.grid(row=2,column=0,sticky='ew')
        self.label(foot,'Operational metrics are unverified message reports, not confirmed safety incidents.',10,MUTED).pack(side='left',padx=16,pady=6)
        self.label(foot,'SQLite • Local-first',10,TEAL).pack(side='right',padx=16)

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
        self.page=page;self.heading.configure(text={'Executive':'Executive Command Centre','Workforce':'Workforce & Attendance',
          'Safety & Alerts':'Safety & Risk Intelligence','System Health':'System Health & Audit',
          'Settings':'Local Settings'}[page])
        selected = '#D1E5F7' if self.theme_mode == 'Light' else '#23496E'
        for n,b in self.nav.items():b.configure(fg_color=selected if n==page else 'transparent')
        self._render()
        with core.connect() as con:
            self._last_sig=(tuple(con.execute('SELECT COUNT(*),MAX(id) FROM reports').fetchone()),self.page)

    def _scope(self,parent):
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
        self.button(row,'Refresh',self._render,width=82).pack(side='right',padx=5)
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

    def _kpi(self,parent,title,value,sub,color=BLUE):
        p=self.panel(parent);p.pack(side='left',fill='both',expand=True,padx=5)
        self.label(p,title.upper(),11,MUTED,True).pack(anchor='w',padx=16,pady=(14,5))
        value_label=self.label(p,str(value),30,color,True)
        value_label.pack(anchor='w',padx=16)
        sub_label=self.label(p,sub,11,MUTED)
        sub_label.pack(anchor='w',padx=16,pady=(0,14))
        return value_label,sub_label
    def _title(self,parent,name,detail=None):
        self.label(parent,name,17,WHITE,True).pack(anchor='w',pady=(18,4))
        if detail:self.label(parent,detail,11,MUTED).pack(anchor='w',pady=(0,7))

    def _render(self):
        """Rebuild only on navigation/filter changes, never on live-message events."""
        # Explicit page/filter navigation may rebuild; automatic updates never do.
        self._live_widgets={}
        for w in self.content.winfo_children():w.destroy()
        self._chart=None
        if self.page in ('Executive','Workforce','Safety & Alerts'):
            self._scope(self.content)
            self._rows=self._selected_rows()
        if self.page=='Executive':self._executive()
        elif self.page=='Workforce':self._workforce()
        elif self.page=='Safety & Alerts':self._safety()
        elif self.page=='System Health':self._health()
        elif self.page=='Settings':self._settings()

    def _executive(self):
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
        self._live_widgets['complete_kpi']=self._kpi(k,'Safety data completeness',f'{pct:.1f}%' if pct is not None else '—',f'{complete} of {total} reports have both fields',TEAL)
        second=ctk.CTkFrame(self.content,fg_color='transparent');second.pack(fill='both',pady=(18,0))
        chart=self.panel(second);chart.pack(side='left',fill='both',expand=True,padx=(5,8))
        self.label(chart,'NEAR-MISS REPORTS — DAILY TREND',13,WHITE,True).pack(anchor='w',padx=14,pady=(12,0))
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
        self._report_table(self.content,self._rows[:80],limit=80)

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
        self.label(holder, 'GANG-WISE ATTENDANCE — STACKED HORIZONTAL COMPARISON',
                   13, WHITE, True).pack(anchor='w', padx=14, pady=(12, 0))
        self.label(holder,
                   'Segments show reported values; Late is an exception count, not unique headcount.',
                   11, MUTED).pack(anchor='w', padx=14, pady=(2, 8))
        names = sorted(groups, key=str.casefold)
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
        # Sender already exists in SQLite; show it alongside report metrics.
        show_late=self.page in ('Workforce','Safety & Alerts')
        cols=('Date / time','Team','Sender','T002/T003','PWT','SPL','PL','Near Miss') + (('Late',) if show_late else ())
        style=ttk.Style();style.theme_use('clam')
        table_bg = '#FFFFFF' if self.theme_mode == 'Light' else '#17253A'
        selected_bg = '#B3DBF7' if self.theme_mode == 'Light' else '#2F6C9C'
        style.configure('Exec.Treeview',background=table_bg,foreground=WHITE,fieldbackground=table_bg,
                        rowheight=28,font=('Segoe UI',10),borderwidth=0)
        style.configure('Exec.Treeview.Heading',background=PANEL,foreground=WHITE,font=('Segoe UI',10,'bold'))
        style.map('Exec.Treeview',background=[('selected',selected_bg)],
                  foreground=[('selected',WHITE)])
        tree=ttk.Treeview(frame,columns=cols,show='headings',height=min(11,max(4,len(rows))),style='Exec.Treeview')
        for c in cols:
            tree.heading(c,text=c)
            tree.column(c,width=175 if c in ('Date / time','Team','Sender') else 103,anchor='w')
        # Scrollbars ensure evidence remains accessible on smaller screens.
        tree.pack(fill='x', padx=(12, 0), pady=12, side='left', expand=True)
        scrollbar = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        scrollbar.pack(side='right', fill='y', padx=(0, 12), pady=12)
        tree.configure(yscrollcommand=scrollbar.set)
        mapping={}
        for r in rows[:limit]:
            vals=[r['event_at'].replace('T',' '),r['group_name'],r.get('sender') or '—']+[r[k] if r[k] is not None else '—' for k in (('t002_t003','pwt','spl','pl','near_miss','late') if show_late else ('t002_t003','pwt','spl','pl','near_miss'))]
            item=tree.insert('',tk.END,values=vals);mapping[item]=r
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
            except Exception:result.configure(text='Enter valid non-negative numbers; absence must be 0–100%.',text_color=RED)
        self.button(sc,'Calculate scenario',update,width=180).pack(anchor='w',padx=22,pady=(0,18));update()
        self._report_table(self.content,self._rows,limit=100)

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
        completeness = f'{complete / reports:.0%}' if reports else '—'
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
        if not items:self.label(p,'No alerts recorded. Alerts appear after collection or import.',12,MUTED).pack(padx=14,pady=20)
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
            empty = self.panel(self.content)
            empty.pack(fill='x', pady=(0, 16))
            self.label(empty,
                       'No reports with a positive Near Miss or Late value '
                       'match the current reporting period and group.',
                       12, MUTED, wraplength=900, justify='left').pack(
                           anchor='w', padx=18, pady=(16, 5))
            self.label(empty,
                       'Try All time / All Groups or check that the imported '
                       'Near Miss and Late values are numeric. A zero is not '
                       'the same as missing data.',
                       11, MUTED, wraplength=900, justify='left').pack(
                           anchor='w', padx=18, pady=(0, 16))

    def _change_alert(self,id,status,owner):
        core.update_alert(id,status,owner);self._render()

    def _health(self):
        state=core.monitor_state()
        self._title(self.content,'Collection and persistence status')
        self.label(self.content,'The connection indicator turns green only after authenticated WhatsApp chat access is confirmed.',
                   11,MUTED).pack(anchor='w',pady=(0,9))
        k=self.panel(self.content);k.pack(fill='x')
        for name,value in [('Monitor',state['status']),('Last scan',state['last_checked'] or '—'),
                           ('Last success',state['last_success'] or '—'),('Last error',state['last_error'] or 'None'),
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
        self.label(self.content,'These settings are saved locally in data/settings.json.',12,MUTED).pack(anchor='w',pady=8)
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
        tbl_style=ttk.Style()
        tbl_style.theme_use('clam')  # Keep table headers legible in light and dark mode.
        tbl_style.configure('GroupRegistry.Treeview',font=('Segoe UI',10),rowheight=29,
                            background=('#FFFFFF' if self.theme_mode == 'Light' else '#17253A'),
                            foreground=WHITE,fieldbackground=PANEL)
        tbl_style.configure('GroupRegistry.Treeview.Heading',background=PANEL,foreground=WHITE,
                            font=('Segoe UI',10,'bold'))
        tbl_style.map('GroupRegistry.Treeview',
                      background=[('selected','#B3DBF7' if self.theme_mode == 'Light' else '#2F6C9C')],
                      foreground=[('selected',WHITE)])
        registry_frame=ctk.CTkFrame(p,fg_color='transparent')
        registry_frame.pack(fill='x',padx=16,pady=(0,8))
        tree=ttk.Treeview(registry_frame,columns=('name','state','last'),show='headings',
                          selectmode='browse',height=7,style='GroupRegistry.Treeview')
        for col,title,width in [('name','Group name (exact WhatsApp title)',400),
                                ('state','Monitoring',110),('last','Last stored report',185)]:
            tree.heading(col,text=title)
            tree.column(col,width=width,minwidth=90,anchor='w',stretch=(col=='name'))
        scrollbar=ttk.Scrollbar(registry_frame,orient='vertical',command=tree.yview)
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
        self.label(self.content,'WhatsApp browser login data is stored locally under whatsapp_session/ in the project folder. Restrict access to this folder.',12,AMBER,wraplength=930).pack(anchor='w',pady=12)

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
        s=core.summary(rows,core.configuration()['groups'])
        prior=core.compare(rows,*self._date_bounds())
        with core.connect() as con:
            all_count=con.execute('SELECT COUNT(*) FROM reports').fetchone()[0]
        with core.connect() as con:
            sig_row=con.execute('SELECT COUNT(*),MAX(id) FROM reports').fetchone()
        self._last_sig=(tuple(sig_row),self.page)
        self._configure_text(self._live_widgets['counter'],
            f'LIVE DATA  •  {s["reports"]:,} displayed / {all_count:,} in database  •  Period: {self.period.get()}  •  Team: {self.group.get()}')
        note=(f"{prior['reports']:+.1f}% vs previous period" if prior and prior['reports'] is not None else 'Comparison unavailable')
        self._update_kpi_in_place('report_kpi',f"{s['reports']:,}",note)
        self._update_kpi_in_place('team_kpi',s['teams'],f"{len(s['missing_groups'])} configured team(s) not represented")
        self._update_kpi_in_place('near_kpi',s['near_miss'])
        complete,total,pct=core.safety_completeness(rows)
        self._update_kpi_in_place('complete_kpi',f'{pct:.1f}%' if pct is not None else '—',
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
                    vals=[r['event_at'].replace('T',' '),r['group_name'],r.get('sender') or '—']+[r[k] if r[k] is not None else '—' for k in ('t002_t003','pwt','spl','pl','near_miss')]
                    item=existing.get(r['id'])
                    if item and tree.exists(item):
                        if tuple(str(v) for v in tree.item(item,'values'))!=tuple(str(v) for v in vals):tree.item(item,values=vals)
                        tree.move(item,'',i)
                    else:
                        item=tree.insert('',i,values=vals)
                        mapping[item]=r
                tree.selection_set([item for item,r in mapping.items() if r['id'] in selected_ids])

    def _schedule_dashboard_refresh(self):
        """Debounce bursts, and always update Tk from the main GUI thread."""
        if self._refresh_pending:
            return
        self._refresh_pending=True
        self.after(75, self._apply_dashboard_refresh)

    def _apply_dashboard_refresh(self):
        self._refresh_pending=False
        if self.page=='Executive':
            self._update_executive_in_place()
        elif self.page=='Workforce':
            # Refresh only the new analytical panel, not the existing charts.
            refresh = self._live_widgets.get('analysis_refresh')
            if refresh: refresh()
        # Other views refresh when revisited. Avoid rebuilding the screen on
        # every insert (the previous behaviour caused severe visual flicker).

    def _poll(self):
        """Run message notifications, status LED and backup checks on the Tk thread."""
        import time
        new_reports=False
        while True:
            try: msg=self.queue.get_nowait()
            except queue.Empty: break
            if isinstance(msg, tuple) and msg and msg[0]=='reports_saved':
                new_reports=True
                self.status_text.set(f'{msg[1]} new records saved from {msg[2]}')
                continue
            self.status_text.set(str(msg))
            if self.page=='System Health' and hasattr(self,'logs') and self.logs.winfo_exists():
                self.logs.insert('end',f'{datetime.now():%H:%M:%S}  {msg}\n')
                self.logs.see('end')
        if new_reports:
            self._schedule_dashboard_refresh()
        # Fallback for imports/external database writers. This also detects
        # changes when the collector cannot send an event to this GUI process.
        now=time.monotonic()
        if now >= self._next_fallback_check:
            self._next_fallback_check=now+0.8
            if self.page in ('Executive','Workforce','Safety & Alerts'):
                with core.connect() as c:
                    row=c.execute('SELECT COUNT(*), MAX(id) FROM reports').fetchone()
                sig=(tuple(row),self.page)
                if self._last_sig != sig:
                    self._last_sig=sig
                    self._schedule_dashboard_refresh()
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
            core.export_pdf(destination,rows,'All groups - daily')
            self._last_report_day=day
        except Exception as e:logging.exception('Automatic local PDF report failed: %s',e)

    def _import(self,extension):
        path=filedialog.askopenfilename(filetypes=[('Reporting file',f'*.{extension}')]);
        if not path:return
        try:
            result=core.import_csv(path) if extension=='csv' else core.import_excel(path)
            core.evaluate_alerts();added,dupes,errors=result
            messagebox.showinfo('Import complete',f'Added: {added}\nDuplicates skipped: {dupes}\nRejected: {len(errors)}'+(f'\n\nFirst errors:\n'+ '\n'.join(errors[:5]) if errors else ''))
            self._last_sig=None;self._render()
        except Exception as e:messagebox.showerror('Import failed',str(e))
    def _import_csv(self):self._import('csv')
    def _import_excel(self):self._import('xlsx')
    def _save(self,ext,method):
        path=filedialog.asksaveasfilename(defaultextension='.'+ext,filetypes=[(ext.upper(),f'*.{ext}')]);
        if not path:return
        try:
            rows=self._selected_rows()
            method(path,rows)
            messagebox.showinfo('Export successful',f'Saved {len(rows)} filtered reports to:\n{path}')
        except Exception as e:messagebox.showerror('Export failed',str(e))
    def _csv(self):self._save('csv',core.export_csv)
    def _excel(self):self._save('xlsx',core.export_excel)
    def _pdf(self):
        def run(path,rows):core.export_pdf(path,rows,scope=f'{self.group.get()} / {self.period.get()}')
        self._save('pdf',run)
    def _html(self):
        def run(path,rows):core.export_interactive_html(path,rows,scope=f'{self.group.get()} / {self.period.get()}')
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
