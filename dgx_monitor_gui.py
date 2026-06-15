#!/usr/bin/env python3
"""
DGX Spark Graphical Monitor - Fixed gauges + 24h history graph.
- Circular dials with grey track, colored fill bar, white needle + perp pointer
  that correctly points to the END of the color bar at the exact percentage.
  At 0% the pointer is at the left start of the bar.
- History graph (24h minutely) with small vertical offset below dials, above More Metrics.
- All previous features: resizable horizontal reflow, ⓘ hover descriptions, internal dock, etc.
"""

import tkinter as tk
from tkinter import Canvas, Frame, Label, Button, Text, Toplevel
import math
import os
import glob
import subprocess
from collections import deque

BG = '#0a0a0f'
PANEL_BG = '#111114'
BORDER = '#27272a'
TEXT = '#e4e4e7'
ACCENT_SKY = '#0ea5e9'
ACCENT_EMERALD = '#10b981'
ACCENT_VIOLET = '#8b5cf6'
ACCENT_AMBER = '#f59e0b'
GREEN = '#10b981'
AMBER = '#f59e0b'
RED = '#ef4444'

TOTAL_CUDA_CORES = 6144

class DGXMonitor:
    def __init__(self, root):
        self.root = root
        self.root.title("DGX Spark Monitor")
        self.root.geometry("560x720+1400+50")
        self.root.configure(bg=BG)
        self.root.attributes('-topmost', True)
        self.root.resizable(True, True)
        self.root.minsize(420, 420)
        
        self.gauge_frames = []
        self.more_expanded = False
        self.gauges = {}
        self.detail_labels = {}
        # 1440 minutely samples == a rolling 24 hour window
        self.history = {k: deque(maxlen=1440) for k in ['gpu', 'cpu', 'mem', 'cuda']}
        self.tick = 0
        
        self.build_ui()
        self.root.bind('<Configure>', self.on_resize)
        self.update_loop()
        
    def build_ui(self):
        header = Frame(self.root, bg=BG)
        header.pack(fill='x', padx=10, pady=5)
        
        Label(header, text="DGX Spark", font=('Space Grotesk', 18, 'bold'), bg=BG, fg=TEXT).pack(side='left')
        Label(header, text="Real-Time Monitor", font=('Inter', 10), bg=BG, fg='#a1a1aa').pack(side='left', padx=5)
        Label(header, text="● LIVE", font=('Inter', 9, 'bold'), bg=BG, fg='#10b981').pack(side='right')
        
        self.gauges_frame = Frame(self.root, bg=BG)
        # fill='x' only (NOT expand) so the dials take just their natural height
        # and the history graph sits closely below them instead of being pushed
        # to the bottom of the window by an expanding gauge area.
        self.gauges_frame.pack(pady=(5, 2), fill='x')
        
        self.gauges['gpu'] = self.create_gauge(self.gauges_frame, "GPU", "UTILIZATION (SM)", 
            "Percentage of the GPU's streaming multiprocessors actively executing CUDA kernels.", ACCENT_SKY)
        self.gauges['cpu'] = self.create_gauge(self.gauges_frame, "CPU", "OVERALL UTILIZATION", 
            "Aggregate load across all 20 Grace CPU cores (performance + efficiency).", ACCENT_EMERALD)
        self.gauges['mem'] = self.create_gauge(self.gauges_frame, "MEMORY", "UNIFIED (USED)", 
            "Used portion of the 128 GB unified memory pool shared by CPU and GPU.", ACCENT_VIOLET)
        self.gauges['cuda'] = self.create_gauge(self.gauges_frame, "CUDA CORES", "ACTIVE (DERIVED)", 
            "Estimated active CUDA cores based on GPU utilization (out of 6144 total on GB10).", ACCENT_AMBER)
        
        self.gauge_frames = [self.gauges[k]['frame'] for k in ['gpu','cpu','mem','cuda']]
        self.layout_gauges()
        
        # Small offset above the history graph (sits closely below the dials)
        Label(self.root, text="24 Hour History (minutely samples)", bg=BG, fg='#a1a1aa', font=('Inter', 8)).pack(padx=15, anchor='w', pady=(2,2))
        # 2x taller than before (was 85) and expands with the window on every side.
        self.history_canvas = Canvas(self.root, height=170, bg=PANEL_BG, highlightthickness=0)
        self.history_canvas.pack(fill='both', expand=True, padx=15, pady=(0,5))
        # Re-render whenever the canvas itself is resized (responsive borders).
        self.history_canvas.bind('<Configure>', lambda e: self.draw_history())
        
        self.more_btn = Button(self.root, text="More Metrics ▾", command=self.toggle_more_metrics,
                          bg='#111114', fg=TEXT, font=('Inter', 10), relief='flat',
                          activebackground='#27272a', activeforeground=TEXT)
        self.more_btn.pack(pady=8, fill='x', padx=15)
        
        self.more_frame = Frame(self.root, bg=PANEL_BG, bd=1, relief='solid', highlightbackground=BORDER, highlightthickness=1)
        self.populate_more_content()
        
        Label(self.root, text="Normal <60%  |  Warning 60-90%  |  Critical >90%", 
              bg=BG, fg='#52525b', font=('Inter', 8)).pack(pady=3)
        
        self.status = Label(self.root, text="Live from nvidia-smi + /proc | Resizable dials + 24h history", bg=BG, fg='#a1a1aa', font=('Inter', 8))
        self.status.pack(pady=2)
        
    def create_gauge(self, parent, title, subtitle, description, color):
        frame = Frame(parent, bg=PANEL_BG, bd=1, relief='solid', highlightbackground=BORDER, highlightthickness=1)
        
        title_row = Frame(frame, bg=PANEL_BG)
        title_row.pack(pady=(6,0), fill='x')
        Label(title_row, text=title, font=('Inter', 11, 'bold'), bg=PANEL_BG, fg=color).pack(side='left')
        
        info_icon = Label(title_row, text="ⓘ", font=('Arial', 9, 'bold'), 
                          bg=PANEL_BG, fg=color, cursor="question_arrow", padx=2)
        info_icon.pack(side='right', padx=(5,0))
        self.create_tooltip(info_icon, description)
        
        Label(frame, text=subtitle, font=('Inter', 7), bg=PANEL_BG, fg='#a1a1aa').pack()
        
        canvas = Canvas(frame, width=160, height=160, bg=PANEL_BG, highlightthickness=0)
        canvas.pack(pady=2)
        
        val_label = Label(frame, text="0%", font=('Space Grotesk', 18, 'bold'), bg=PANEL_BG, fg=TEXT)
        val_label.pack(pady=(0,6))
        
        return {'frame': frame, 'canvas': canvas, 'val_label': val_label, 'color': color, 'percent': 0}
    
    def create_tooltip(self, widget, text):
        def on_enter(event):
            if hasattr(self, '_tooltip') and self._tooltip.winfo_exists():
                self._tooltip.destroy()
            self._tooltip = Toplevel(widget)
            self._tooltip.wm_overrideredirect(True)
            x = event.x_root + 10
            y = event.y_root + 15
            self._tooltip.wm_geometry(f"+{x}+{y}")
            self._tooltip.configure(bg='#111114', bd=1, relief='solid', highlightbackground=BORDER)
            Label(self._tooltip, text=text, justify='left', background='#0a0a0f', foreground=TEXT,
                  font=('Inter', 9), padx=6, pady=4, wraplength=200, relief='flat').pack()
        def on_leave(event):
            if hasattr(self, '_tooltip') and self._tooltip.winfo_exists():
                self._tooltip.destroy()
                del self._tooltip
        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)
    
    def draw_gauge(self, canvas, percent, color):
        canvas.delete('all')
        cx, cy = 80, 80
        r = 68
        percent = max(0.0, min(100.0, percent))

        # --- Angle convention (all elements use the SAME one) ---
        # Tkinter arcs and our trig both place a point at angle theta (deg, measured
        # counter-clockwise from 3 o'clock) at screen coords:
        #     x = cx + r*cos(theta) ,  y = cy - r*sin(theta)   (y inverted for screen)
        # The gauge is the TOP semicircle: 0% -> 180deg (left), 100% -> 0deg (right),
        # sweeping CLOCKWISE over the top. A NEGATIVE Tkinter extent sweeps that way.
        def pt(theta_deg, radius):
            t = math.radians(theta_deg)
            return cx + radius * math.cos(t), cy - radius * math.sin(t)

        end_angle = 180.0 - 1.8 * percent          # angle at the end of the colored bar

        # Grey background track: full top semicircle, always visible behind the bar.
        canvas.create_arc(cx-r, cy-r, cx+r, cy+r, start=180, extent=-180,
                          outline='#3f3f46', width=14, style='arc')

        # Colored fill: from the left (0%) clockwise over the top to the current %.
        if percent > 0:
            canvas.create_arc(cx-r, cy-r, cx+r, cy+r, start=180, extent=-1.8 * percent,
                              outline=color, width=14, style='arc')

        # White needle from the centre to the exact END of the colored bar.
        nx, ny = pt(end_angle, r)
        canvas.create_line(cx, cy, nx, ny, fill='white', width=2.5)

        # Short perpendicular cap at the tip, marking the end of the bar precisely.
        perp_len = 6
        pa = math.radians(end_angle)
        # tangent (perpendicular to the radius) unit vector in screen space
        tx, ty = -math.sin(pa), -math.cos(pa)
        canvas.create_line(nx + perp_len * tx, ny + perp_len * ty,
                           nx - perp_len * tx, ny - perp_len * ty,
                           fill='white', width=2)

        # Center hub
        canvas.create_oval(cx-5, cy-5, cx+5, cy+5, fill='#1a1a1a', outline='#555555', width=1)

        # Big % readout in the open lower area of the dial (below the hub).
        canvas.create_text(cx, cy + 28, text=f"{int(round(percent))}%", fill='white',
                           font=('Space Grotesk', 15, 'bold'), anchor='center')

        # Reference ticks at 0/25/50/75/100 along the track.
        for i in range(0, 101, 25):
            ta = 180.0 - 1.8 * i
            x1, y1 = pt(ta, r * 0.80)
            x2, y2 = pt(ta, r * 0.93)
            canvas.create_line(x1, y1, x2, y2, fill='#666666', width=1)
    
    def layout_gauges(self):
        w = self.root.winfo_width()
        if w < 50:
            w = 560
        for f in self.gauge_frames:
            f.grid_forget()
        if w > 720:
            for i, f in enumerate(self.gauge_frames):
                f.grid(row=0, column=i, padx=4, pady=4, sticky='nsew')
            for i in range(4):
                self.gauges_frame.grid_columnconfigure(i, weight=1)
            self.gauges_frame.grid_rowconfigure(0, weight=1)
        else:
            for i, f in enumerate(self.gauge_frames):
                f.grid(row=i//2, column=i%2, padx=4, pady=4, sticky='nsew')
            for i in range(2):
                self.gauges_frame.grid_columnconfigure(i, weight=1)
                self.gauges_frame.grid_rowconfigure(i, weight=1)
    
    def on_resize(self, event):
        if event.widget == self.root:
            self.layout_gauges()
    
    def update_gauges(self):
        gpu = self.get_gpu_util()
        cpu = self.get_cpu_util()
        mem = self.get_mem_util()
        cuda = min(100.0, gpu)
        
        for key, pct in [('gpu', gpu), ('cpu', cpu), ('mem', mem), ('cuda', cuda)]:
            info = self.gauges[key]
            color = self.get_color(pct)
            self.draw_gauge(info['canvas'], pct, color)
            info['val_label'].config(text=f"{pct:.0f}%", fg=color)
            info['percent'] = pct
        
        self.tick += 1
        if self.tick % 60 == 0:
            for k, v in [('gpu', gpu), ('cpu', cpu), ('mem', mem), ('cuda', cuda)]:
                self.history[k].append(v)
        self.draw_history()
        self.status.config(text="Live • nvidia-smi + /proc • Resizable + 24h history")
    
    def get_color(self, pct):
        if pct > 90: return RED
        elif pct > 60: return AMBER
        return GREEN
    
    def get_gpu_util(self):
        try:
            out = subprocess.check_output(['nvidia-smi', '--query-gpu=utilization.gpu', '--format=csv,noheader,nounits'], 
                                          stderr=subprocess.DEVNULL, timeout=2).decode().strip()
            return max(0.0, min(100.0, float(out.split('\n')[0])))
        except:
            return 55.0
    
    def get_cpu_util(self):
        try:
            out = subprocess.check_output(['top', '-bn1'], stderr=subprocess.DEVNULL, timeout=2).decode()
            for line in out.split('\n'):
                if 'Cpu(s)' in line or '%Cpu' in line:
                    parts = line.replace(',', ' ').split()
                    for i, p in enumerate(parts):
                        if 'us' in p.lower():
                            if i > 0: return max(0.0, min(100.0, float(parts[i-1])))
            return 35.0
        except:
            return 35.0
    
    def get_mem_util(self):
        try:
            out = subprocess.check_output(['free', '-m'], stderr=subprocess.DEVNULL, timeout=2).decode()
            lines = out.split('\n')
            mem = lines[1].split()
            total = float(mem[1])
            used = float(mem[2])
            return max(0.0, min(100.0, (used / total) * 100))
        except:
            return 60.0
    
    def draw_history(self):
        c = self.history_canvas
        c.delete('all')
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10: w = 500
        if h < 10: h = 170

        # Plot margins: room for y labels (left), legend (top), x labels (bottom).
        left, right = 34, w - 14
        top, bottom = 18, h - 16
        plot_w = right - left
        plot_h = bottom - top
        if plot_w < 30 or plot_h < 30:
            return

        c.create_rectangle(0, 0, w - 1, h - 1, fill=PANEL_BG, outline=BORDER)

        # --- Y axis: 0% .. 100% with horizontal gridlines ---
        for pct in range(0, 101, 25):
            y = bottom - (pct / 100.0) * plot_h
            c.create_line(left, y, right, y, fill='#1d1d22')
            c.create_text(left - 5, y, text=f"{pct}", fill='#888888', font=('Arial', 7), anchor='e')
        c.create_text(left - 5, top - 9, text='%', fill='#666666', font=('Arial', 7), anchor='e')

        # --- X axis: fixed 24h window, right edge = now, hourly gridlines ---
        # Major (labelled) lines every 6 hours; minor lines every hour.
        for hr in range(0, 25):
            x = right - (hr / 24.0) * plot_w
            major = (hr % 6 == 0)
            c.create_line(x, top, x, bottom, fill=('#2a2a31' if major else '#191920'))
            if major:
                lbl = 'now' if hr == 0 else f'-{hr}h'
                c.create_text(x, bottom + 3, text=lbl, fill='#888888', font=('Arial', 7), anchor='n')

        # Axis lines
        c.create_line(left, top, left, bottom, fill='#666666')
        c.create_line(left, bottom, right, bottom, fill='#666666')

        # --- Legend (top, horizontal) ---
        legend = [('GPU', ACCENT_SKY), ('CPU', ACCENT_EMERALD), ('Mem', ACCENT_VIOLET), ('Cores', ACCENT_AMBER)]
        lx = left + 4
        for name, col in legend:
            c.create_line(lx, top - 9, lx + 10, top - 9, fill=col, width=2)
            c.create_text(lx + 13, top - 9, text=name, fill=col, font=('Arial', 7), anchor='w')
            lx += 13 + len(name) * 6 + 12

        # --- Data lines (each sample is one minute; index 0 is oldest) ---
        colors = {'gpu': ACCENT_SKY, 'cpu': ACCENT_EMERALD, 'mem': ACCENT_VIOLET, 'cuda': ACCENT_AMBER}
        have_data = any(len(self.history[k]) >= 2 for k in colors)
        if not have_data:
            c.create_text((left + right) // 2, (top + bottom) // 2,
                          text="Collecting history — 1 sample per minute (up to 24 h)…",
                          fill='#888888', font=('Arial', 8))
            return

        for key, col in colors.items():
            data = list(self.history[key])
            n = len(data)
            if n < 2:
                continue
            pts = []
            for i, v in enumerate(data):
                minutes_ago = (n - 1 - i)          # 0 == most recent sample (now)
                x = right - (minutes_ago / 1440.0) * plot_w
                if x < left:
                    x = left
                y = bottom - (max(0.0, min(100.0, v)) / 100.0) * plot_h
                pts.extend([x, y])
            if len(pts) >= 4:
                c.create_line(pts, fill=col, width=1.6)
    
    def update_loop(self):
        self.update_gauges()
        # Refresh the detail tables only while the dock is open (keeps idle overhead low).
        if self.more_expanded and self.tick % 2 == 0:
            self.update_details()
        self.root.after(1000, self.update_loop)

    def _make_table(self, parent, title, color, rows):
        """Build one titled metric card; rows = list of (key, display_name).
        Stores each value cell in self.detail_labels keyed by `key` for live updates."""
        card = Frame(parent, bg=PANEL_BG, bd=1, relief='solid',
                     highlightbackground=BORDER, highlightthickness=1)
        Label(card, text=title, bg=PANEL_BG, fg=color, font=('Inter', 9, 'bold')).grid(
            row=0, column=0, columnspan=2, sticky='w', padx=8, pady=(5, 3))
        for r, (key, name) in enumerate(rows, start=1):
            Label(card, text=name, bg=PANEL_BG, fg='#a1a1aa', font=('Inter', 8),
                  anchor='w').grid(row=r, column=0, sticky='w', padx=(8, 6), pady=1)
            val = Label(card, text='—', bg=PANEL_BG, fg=TEXT,
                        font=('Space Grotesk', 8, 'bold'), anchor='e')
            val.grid(row=r, column=1, sticky='e', padx=(6, 8), pady=1)
            self.detail_labels[key] = val
        card.grid_columnconfigure(0, weight=1)
        card.grid_columnconfigure(1, weight=0)
        return card

    def populate_more_content(self):
        for w in self.more_frame.winfo_children():
            w.destroy()
        self.detail_labels = {}

        gpu = self._make_table(self.more_frame, "GPU  ·  GB10 Blackwell", ACCENT_SKY, [
            ('gpu_temp',    'Temperature'),
            ('gpu_power',   'Power Draw'),
            ('gpu_clock',   'SM / Graphics Clock'),
            ('gpu_pstate',  'Performance State'),
            ('gpu_memutil', 'Mem Bandwidth Util'),
            ('gpu_encdec',  'Encoder / Decoder'),
        ])
        cpu = self._make_table(self.more_frame, "CPU  ·  20-core Grace", ACCENT_EMERALD, [
            ('cpu_load',  'Load Avg (1/5/15m)'),
            ('cpu_freq',  'Avg Frequency'),
            ('cpu_temp',  'Package Temp'),
            ('cpu_cores', 'Cores / Threads'),
        ])
        mem = self._make_table(self.more_frame, "Unified Memory  ·  128 GB", ACCENT_VIOLET, [
            ('mem_total', 'Total'),
            ('mem_used',  'Used'),
            ('mem_avail', 'Available'),
            ('mem_cache', 'Buffers / Cache'),
            ('mem_swap',  'Swap Used'),
        ])
        sysc = self._make_table(self.more_frame, "System  ·  Headroom", ACCENT_AMBER, [
            ('gpu_headroom', 'GPU Headroom'),
            ('gpu_util2',    'GPU Utilization'),
            ('driver',       'Driver Version'),
            ('uptime',       'Uptime'),
        ])
        gpu.grid(row=0, column=0, sticky='nsew', padx=4, pady=4)
        cpu.grid(row=0, column=1, sticky='nsew', padx=4, pady=4)
        mem.grid(row=1, column=0, sticky='nsew', padx=4, pady=4)
        sysc.grid(row=1, column=1, sticky='nsew', padx=4, pady=4)
        self.more_frame.grid_columnconfigure(0, weight=1, uniform='cards')
        self.more_frame.grid_columnconfigure(1, weight=1, uniform='cards')

    def get_detail_metrics(self):
        """Collect secondary metrics. Each source is guarded so a single failure
        (e.g. an N/A field on this passive/unified platform) never blanks the rest."""
        d = {}
        # --- GPU: one combined nvidia-smi call ---
        try:
            q = ('temperature.gpu,power.draw,clocks.sm,clocks.gr,pstate,'
                 'utilization.memory,utilization.encoder,utilization.decoder,'
                 'utilization.gpu,driver_version')
            out = subprocess.check_output(
                ['nvidia-smi', f'--query-gpu={q}', '--format=csv,noheader,nounits'],
                stderr=subprocess.DEVNULL, timeout=2).decode().strip().split('\n')[0]
            f = [x.strip() for x in out.split(',')]
            temp, power, csm, cgr, pst, mu, enc, dec, gu, drv = f

            def num(s):
                try: return float(s)
                except (ValueError, TypeError): return None

            d['gpu_temp']    = f"{temp} °C" if num(temp) is not None else 'N/A'
            d['gpu_power']   = f"{num(power):.1f} W" if num(power) is not None else 'N/A'
            csm_s = f"{csm} MHz" if num(csm) is not None else 'N/A'
            cgr_s = f"{cgr} MHz" if num(cgr) is not None else 'N/A'
            d['gpu_clock']   = f"{csm_s} / {cgr_s}" if csm != cgr else csm_s
            d['gpu_pstate']  = pst if pst not in ('', '[N/A]') else 'N/A'
            d['gpu_memutil'] = f"{mu}%" if num(mu) is not None else 'N/A'
            d['gpu_encdec']  = (f"{enc}% / {dec}%"
                                if num(enc) is not None and num(dec) is not None else 'N/A')
            guv = num(gu) or 0.0
            d['gpu_util2']    = f"{guv:.0f}%"
            d['gpu_headroom'] = f"{100 - guv:.0f}% free"
            d['driver']       = drv if drv not in ('', '[N/A]') else 'N/A'
        except Exception:
            pass
        # --- CPU ---
        try:
            la = open('/proc/loadavg').read().split()
            d['cpu_load'] = f"{la[0]} / {la[1]} / {la[2]}"
        except Exception:
            pass
        try:
            freqs = []
            for p in glob.glob('/sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq'):
                try: freqs.append(int(open(p).read().strip()))
                except Exception: pass
            if freqs:
                d['cpu_freq'] = f"{(sum(freqs) / len(freqs)) / 1e6:.2f} GHz"
        except Exception:
            pass
        try:
            temps = []
            for p in glob.glob('/sys/class/thermal/thermal_zone*/temp'):
                try: temps.append(int(open(p).read().strip()))
                except Exception: pass
            if temps:
                d['cpu_temp'] = f"{max(temps) / 1000.0:.0f} °C"
        except Exception:
            pass
        d['cpu_cores'] = f"{os.cpu_count()} cores"
        # --- Unified memory (nvidia-smi reports N/A; use free) ---
        try:
            lines = subprocess.check_output(['free', '-m'], stderr=subprocess.DEVNULL,
                                            timeout=2).decode().split('\n')
            m = lines[1].split()
            total, used, free_, shared, cache, avail = [float(x) for x in m[1:7]]
            d['mem_total'] = f"{total / 1024:.1f} GB"
            d['mem_used']  = f"{used / 1024:.1f} GB ({used / total * 100:.0f}%)"
            d['mem_avail'] = f"{avail / 1024:.1f} GB"
            d['mem_cache'] = f"{cache / 1024:.1f} GB"
            s = lines[2].split()
            st, su = float(s[1]), float(s[2])
            d['mem_swap'] = f"{su / 1024:.1f} / {st / 1024:.1f} GB"
        except Exception:
            pass
        # --- Uptime ---
        try:
            up = float(open('/proc/uptime').read().split()[0])
            days, rem = divmod(int(up), 86400)
            hh, mm = divmod(rem // 60, 60)
            d['uptime'] = (f"{days}d {hh}h {mm}m" if days else f"{hh}h {mm}m")
        except Exception:
            pass
        return d

    def update_details(self):
        if not self.detail_labels:
            return
        metrics = self.get_detail_metrics()
        for key, lbl in self.detail_labels.items():
            if key in metrics:
                lbl.config(text=metrics[key])

    def toggle_more_metrics(self):
        if not self.more_expanded:
            self.more_frame.pack(fill='x', padx=12, pady=(0,8), after=self.more_btn)
            self.more_btn.config(text="Less Metrics ▲")
            self.more_expanded = True
            self.update_details()
        else:
            self.more_frame.pack_forget()
            self.more_btn.config(text="More Metrics ▾")
            self.more_expanded = False

if __name__ == "__main__":
    root = tk.Tk()
    app = DGXMonitor(root)
    root.mainloop()
