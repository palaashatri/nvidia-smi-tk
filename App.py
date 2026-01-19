import tkinter as tk
from tkinter import ttk, messagebox, Menu
import subprocess
import re
import threading
import json
import os
import platform
import time
from datetime import datetime
from collections import deque
import ctypes

try:
    import matplotlib
    matplotlib.use('TkAgg')
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MATPLOTLIB_AVAILABLE = True
except ImportError:
    MATPLOTLIB_AVAILABLE = False

try:
    from PIL import Image, ImageDraw
    import pystray
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False

try:
    from plyer import notification
    PLYER_AVAILABLE = True
except ImportError:
    PLYER_AVAILABLE = False

VERSION = "2.0.0"
CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".nvidia_smi_tk_config.json")
HISTORY_SIZE = 300

COLOR_UTIL_WARN = 70
COLOR_UTIL_DANGER = 90
COLOR_TEMP_WARN = 65
COLOR_TEMP_DANGER = 80
COLOR_POWER_WARN = 0.8
COLOR_POWER_DANGER = 0.95

LIGHT_THEME = {
    "bg": "#f2f5f0",
    "fg": "#1a1a1a",
    "card_bg": "#ffffff",
    "card_border": "#d6e4cc",
    "hover_bg": "#ecf3e7",
    "select_bg": "#5a8f00",
    "select_fg": "#ffffff",
    "accent": "#76b900",  # NVIDIA green
    "accent_light": "#e7f2d6",
    "tree_bg": "#f7faf4",
    "tree_fg": "#1a1a1a",
    "tree_select_bg": "#76b900",
    "tree_select_fg": "#ffffff",
    "tree_alt_bg": "#f2f7ed",
    "button_bg": "#76b900",
    "button_fg": "#ffffff",
    "button_hover": "#5a8f00",
    "text_bg": "#ffffff",
    "text_fg": "#1a1a1a",
    "header_gradient_start": "#6aa300",
    "header_gradient_end": "#4f7d00",
    "success": "#4c9e00",
    "warning": "#ff8c00",
    "danger": "#d13438",
    "info": "#76b900",
    "muted": "#5d6b55",
    "separator": "#d6e4cc"
}

DARK_THEME = {
    "bg": "#11160f",
    "fg": "#e6f0dd",
    "card_bg": "#1a2415",
    "card_border": "#273220",
    "hover_bg": "#1f2c1b",
    "select_bg": "#6fb300",
    "select_fg": "#ffffff",
    "accent": "#76b900",
    "accent_light": "#274014",
    "tree_bg": "#161f13",
    "tree_fg": "#dce9cf",
    "tree_select_bg": "#6fb300",
    "tree_select_fg": "#ffffff",
    "tree_alt_bg": "#1a2415",
    "button_bg": "#76b900",
    "button_fg": "#ffffff",
    "button_hover": "#5a8f00",
    "text_bg": "#131a11",
    "text_fg": "#dce9cf",
    "header_gradient_start": "#5f9700",
    "header_gradient_end": "#416900",
    "success": "#4c9e00",
    "warning": "#d79b00",
    "danger": "#c5524a",
    "info": "#76b900",
    "muted": "#7a8a72",
    "separator": "#273220"
}

power_win = None
gpu_data_cache = {}
static_cache = {}
update_thread = None
stop_update = False
history_data = {
    'time': deque(maxlen=HISTORY_SIZE),
    'utilization': deque(maxlen=HISTORY_SIZE),
    'temperature': deque(maxlen=HISTORY_SIZE),
    'power': deque(maxlen=HISTORY_SIZE),
    'memory': deque(maxlen=HISTORY_SIZE)
}
last_alert_time = {}
tray_icon = None


def load_config():
    default_config = {
        "refresh_rate": 2000,
        "window_x": 100,
        "window_y": 100,
        "window_width": 800,
        "window_height": 600,
        "dark_mode": False,
        "always_on_top": False,
        "alert_temp": 80,
        "alert_util": 90,
        "alert_enabled": True
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                default_config.update(config)
        except:
            pass
    return default_config

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except:
        pass

def run_with_retry(func, retries=3, delay=1):
    for i in range(retries):
        try:
            return func()
        except Exception as e:
            if i == retries - 1:
                raise
            time.sleep(delay)

def get_gpu_info():
    if 'gpu_name' in static_cache:
        return static_cache['gpu_name']
    try:
        def fetch():
            info = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"],
                encoding="utf-8", stderr=subprocess.STDOUT
            ).strip()
            return info.split("\n")[0].strip()
        name = run_with_retry(fetch)
        static_cache['gpu_name'] = name
        return name
    except Exception:
        return "Unknown"

def get_power_limits():
    if 'power_limits' in static_cache:
        return static_cache['power_limits']
    try:
        def fetch():
            smi = subprocess.check_output(
                ["nvidia-smi", "-q", "-d", "POWER"],
                encoding="utf-8", stderr=subprocess.STDOUT
            )
            cur = min_ = max_ = None
            for line in smi.splitlines():
                line = line.strip()
                m_cur = re.match(r"^Power Limit\s*:\s*([\d\.]+) W", line)
                m_min = re.match(r"^Min Power Limit\s*:\s*([\d\.]+) W", line)
                m_max = re.match(r"^Max Power Limit\s*:\s*([\d\.]+) W", line)
                if m_cur:
                    cur = float(m_cur.group(1))
                elif m_min:
                    min_ = float(m_min.group(1))
                elif m_max:
                    max_ = float(m_max.group(1))
            return cur, min_, max_
        result = run_with_retry(fetch)
        static_cache['power_limits'] = result
        return result
    except Exception:
        return None, None, None

def set_power_limit(new_limit):
    try:
        if platform.system() == "Windows":
            result = subprocess.check_output(
                ["nvidia-smi", "-pl", str(int(new_limit))],
                encoding="utf-8", stderr=subprocess.STDOUT
            )
        else:
            result = subprocess.check_output(
                ["sudo", "nvidia-smi", "-pl", str(int(new_limit))],
                encoding="utf-8", stderr=subprocess.STDOUT
            )
        static_cache.pop('power_limits', None)
        return True, result
    except subprocess.CalledProcessError as e:
        return False, e.output
    except Exception as e:
        return False, str(e)

def get_nvidia_smi_output():
    try:
        def fetch():
            result = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed,clocks.gr,clocks.mem", "--format=csv,noheader,nounits"],
                encoding="utf-8", stderr=subprocess.STDOUT
            )
            proc_result = subprocess.check_output(
                ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
                encoding="utf-8", stderr=subprocess.STDOUT
            )
            return result.strip(), proc_result.strip()
        return run_with_retry(fetch)
    except FileNotFoundError:
        return "Error: nvidia-smi not found. Ensure drivers are installed.", ""
    except Exception as e:
        return f"Error: {e}", ""

def parse_gpu_metrics(output):
    metrics = {}
    try:
        parts = [x.strip() for x in output.split(",")]
        metrics["utilization"] = float(parts[0]) if parts[0] else 0
        metrics["mem_used"] = float(parts[1]) if parts[1] else 0
        metrics["mem_total"] = float(parts[2]) if parts[2] else 1
        metrics["temperature"] = int(float(parts[3])) if parts[3] else 0
        metrics["power_draw"] = float(parts[4]) if parts[4] else 0
        metrics["power_limit"] = float(parts[5]) if parts[5] else 1
        metrics["fan_speed"] = float(parts[6]) if len(parts) > 6 and parts[6] and parts[6] != "[N/A]" else None
        metrics["clock_gpu"] = float(parts[7]) if len(parts) > 7 and parts[7] else 0
        metrics["clock_mem"] = float(parts[8]) if len(parts) > 8 and parts[8] else 0
    except Exception:
        pass
    return metrics

def parse_processes(proc_output):
    processes = []
    for line in proc_output.splitlines():
        if not line.strip():
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 3:
            pid, name, mem = parts
            processes.append({
                "pid": pid,
                "name": name,
                "mem": mem
            })
    return processes

def format_memory(mem_used, mem_total):
    percent = (mem_used / mem_total) * 100 if mem_total else 0
    if mem_total >= 1024:
        used_str = f"{mem_used/1024:.1f} GB"
        total_str = f"{mem_total/1024:.1f} GB"
    else:
        used_str = f"{mem_used:.0f} MB"
        total_str = f"{mem_total:.0f} MB"
    return f"{used_str} / {total_str} ({percent:.1f}%)", percent

def color_for_percent(val, warn=COLOR_UTIL_WARN, danger=COLOR_UTIL_DANGER):
    if val < warn:
        return "green"
    elif val < danger:
        return "orange"
    else:
        return "red"

def color_for_temp(temp):
    if temp < COLOR_TEMP_WARN:
        return "green"
    elif temp < COLOR_TEMP_DANGER:
        return "orange"
    else:
        return "red"

def color_for_power(draw, limit):
    if draw < limit * COLOR_POWER_WARN:
        return "green"
    elif draw < limit * COLOR_POWER_DANGER:
        return "orange"
    else:
        return "red"

def export_to_csv(filename):
    try:
        import csv
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['Timestamp', 'Utilization (%)', 'Temperature (C)', 'Power (W)', 'Memory (%)'])
            for i in range(len(history_data['time'])):
                writer.writerow([
                    history_data['time'][i],
                    history_data['utilization'][i],
                    history_data['temperature'][i],
                    history_data['power'][i],
                    history_data['memory'][i]
                ])
        return True
    except Exception as e:
        return False

def export_to_json(filename):
    try:
        data = {
            'export_time': datetime.now().isoformat(),
            'gpu_name': static_cache.get('gpu_name', 'Unknown'),
            'data': []
        }
        for i in range(len(history_data['time'])):
            data['data'].append({
                'timestamp': history_data['time'][i],
                'utilization': history_data['utilization'][i],
                'temperature': history_data['temperature'][i],
                'power': history_data['power'][i],
                'memory': history_data['memory'][i]
            })
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception as e:
        return False

def show_notification(title, message):
    if not PLYER_AVAILABLE:
        return
    try:
        notification.notify(
            title=title,
            message=message,
            app_name="NVIDIA-SMI Monitor",
            timeout=5
        )
    except:
        pass

def check_alerts(metrics, config):
    global last_alert_time
    current_time = time.time()
    
    if not config.get('alert_enabled', True):
        return
    
    temp = metrics.get('temperature', 0)
    util = metrics.get('utilization', 0)
    alert_temp = config.get('alert_temp', 80)
    alert_util = config.get('alert_util', 90)
    
    if temp >= alert_temp:
        if current_time - last_alert_time.get('temp', 0) > 300:
            show_notification("Temperature Alert", f"GPU temperature is {temp}°C (threshold: {alert_temp}°C)")
            last_alert_time['temp'] = current_time
    
    if util >= alert_util:
        if current_time - last_alert_time.get('util', 0) > 300:
            show_notification("Utilization Alert", f"GPU utilization is {util}% (threshold: {alert_util}%)")
            last_alert_time['util'] = current_time

def get_gpu_info():
    if 'gpu_name' in static_cache:
        return static_cache['gpu_name']
    try:
        def fetch():
            info = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader,nounits"],
                encoding="utf-8", stderr=subprocess.STDOUT
            ).strip()
            return info.split("\n")[0].strip()
        name = run_with_retry(fetch)
        static_cache['gpu_name'] = name
        return name
    except Exception:
        return "Unknown"

def get_power_limits():
    if 'power_limits' in static_cache:
        return static_cache['power_limits']
    try:
        def fetch():
            smi = subprocess.check_output(
                ["nvidia-smi", "-q", "-d", "POWER"],
                encoding="utf-8", stderr=subprocess.STDOUT
            )
            cur = min_ = max_ = None
            for line in smi.splitlines():
                line = line.strip()
                m_cur = re.match(r"^Power Limit\s*:\s*([\d\.]+) W", line)
                m_min = re.match(r"^Min Power Limit\s*:\s*([\d\.]+) W", line)
                m_max = re.match(r"^Max Power Limit\s*:\s*([\d\.]+) W", line)
                if m_cur:
                    cur = float(m_cur.group(1))
                elif m_min:
                    min_ = float(m_min.group(1))
                elif m_max:
                    max_ = float(m_max.group(1))
            return cur, min_, max_
        result = run_with_retry(fetch)
        static_cache['power_limits'] = result
        return result
    except Exception:
        return None, None, None

def set_power_limit(new_limit):
    try:
        if platform.system() == "Windows":
            result = subprocess.check_output(
                ["nvidia-smi", "-pl", str(int(new_limit))],
                encoding="utf-8", stderr=subprocess.STDOUT
            )
        else:
            result = subprocess.check_output(
                ["sudo", "nvidia-smi", "-pl", str(int(new_limit))],
                encoding="utf-8", stderr=subprocess.STDOUT
            )
        static_cache.pop('power_limits', None)
        return True, result
    except subprocess.CalledProcessError as e:
        return False, e.output
    except Exception as e:
        return False, str(e)

def get_nvidia_smi_output():
    try:
        def fetch():
            result = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw,power.limit,fan.speed,clocks.gr,clocks.mem", "--format=csv,noheader,nounits"],
                encoding="utf-8", stderr=subprocess.STDOUT
            )
            proc_result = subprocess.check_output(
                ["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"],
                encoding="utf-8", stderr=subprocess.STDOUT
            )
            return result.strip(), proc_result.strip()
        return run_with_retry(fetch)
    except FileNotFoundError:
        return "Error: nvidia-smi not found. Ensure drivers are installed.", ""
    except Exception as e:
        return f"Error: {e}", ""

def parse_gpu_metrics(output):
    metrics = {}
    try:
        parts = [x.strip() for x in output.split(",")]
        metrics["utilization"] = float(parts[0]) if parts[0] else 0
        metrics["mem_used"] = float(parts[1]) if parts[1] else 0
        metrics["mem_total"] = float(parts[2]) if parts[2] else 1
        metrics["temperature"] = int(float(parts[3])) if parts[3] else 0
        metrics["power_draw"] = float(parts[4]) if parts[4] else 0
        metrics["power_limit"] = float(parts[5]) if parts[5] else 1
        metrics["fan_speed"] = float(parts[6]) if len(parts) > 6 and parts[6] and parts[6] != "[N/A]" else None
        metrics["clock_gpu"] = float(parts[7]) if len(parts) > 7 and parts[7] else 0
        metrics["clock_mem"] = float(parts[8]) if len(parts) > 8 and parts[8] else 0
    except Exception:
        pass
    return metrics

def parse_processes(proc_output):
    processes = []
    for line in proc_output.splitlines():
        if not line.strip():
            continue
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 3:
            pid, name, mem = parts
            processes.append({
                "pid": pid,
                "name": name,
                "mem": mem
            })
    return processes

def format_memory(mem_used, mem_total):
    percent = (mem_used / mem_total) * 100 if mem_total else 0
    if mem_total >= 1024:
        used_str = f"{mem_used/1024:.1f} GB"
        total_str = f"{mem_total/1024:.1f} GB"
    else:
        used_str = f"{mem_used:.0f} MB"
        total_str = f"{mem_total:.0f} MB"
    return f"{used_str} / {total_str} ({percent:.1f}%)", percent

def color_for_percent(val, warn=COLOR_UTIL_WARN, danger=COLOR_UTIL_DANGER):
    if val < warn:
        return "green"
    elif val < danger:
        return "orange"
    else:
        return "red"

def color_for_temp(temp):
    if temp < COLOR_TEMP_WARN:
        return "green"
    elif temp < COLOR_TEMP_DANGER:
        return "orange"
    else:
        return "red"

def color_for_power(draw, limit):
    if draw < limit * COLOR_POWER_WARN:
        return "green"
    elif draw < limit * COLOR_POWER_DANGER:
        return "orange"
    else:
        return "red"

def create_tooltip(widget, text):
    tooltip = None
    
    def on_enter(event):
        nonlocal tooltip
        x, y, _, _ = widget.bbox("insert") if hasattr(widget, 'bbox') else (0, 0, 0, 0)
        x += widget.winfo_rootx() + 25
        y += widget.winfo_rooty() + 25
        
        tooltip = tk.Toplevel(widget)
        tooltip.wm_overrideredirect(True)
        tooltip.wm_geometry(f"+{x}+{y}")
        tooltip.attributes('-alpha', 0.95)
        
        label = tk.Label(
            tooltip,
            text=text,
            background="#1a1a1a",
            foreground="#e8e8e8",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 9),
            padx=12,
            pady=6,
        )
        label.pack()
    
    def on_leave(event):
        nonlocal tooltip
        if tooltip:
            tooltip.destroy()
            tooltip = None
    
    widget.bind("<Enter>", on_enter)
    widget.bind("<Leave>", on_leave)


def create_tray_icon_image(theme):
    """Generate a simple NVIDIA-green themed tray icon."""
    if not PYSTRAY_AVAILABLE or not Image:
        return None
    size = 64
    img = Image.new('RGBA', (size, size), theme['accent'])
    draw = ImageDraw.Draw(img)
    margin = 10
    draw.ellipse([margin, margin, size - margin, size - margin], fill='white')
    inner_margin = 22
    draw.ellipse([inner_margin, inner_margin, size - inner_margin, size - inner_margin], fill=theme['accent'])
    return img


def open_power_limit_window(parent_app):
    global power_win
    if power_win is not None and tk.Toplevel.winfo_exists(power_win):
        power_win.lift()
        return
    cur, min_, max_ = get_power_limits()
    power_win = tk.Toplevel(parent_app.root)
    power_win.title("Power Limit Configuration")
    power_win.resizable(False, False)
    power_win.grab_set()
    
    theme = DARK_THEME if parent_app.config['dark_mode'] else LIGHT_THEME
    power_win.configure(bg=theme['bg'])
    
    main_frame = ttk.Frame(power_win)
    main_frame.pack(padx=25, pady=20, fill="both", expand=True)
    
    title = ttk.Label(main_frame, text="GPU POWER LIMIT", style='Title.TLabel')
    title.pack(pady=(0, 15))
    
    info_frame = ttk.Frame(main_frame, style='Card.TFrame')
    info_frame.pack(fill='x', pady=(0, 15), ipady=10, ipadx=10)
    
    ttk.Label(info_frame, text=f"Current: {cur if cur is not None else 'Unknown'} W", 
              font=("Segoe UI Semibold", 10)).pack(pady=3)
    ttk.Label(info_frame, text=f"Range: {min_ if min_ is not None else 'Unknown'} - {max_ if max_ is not None else 'Unknown'} W", 
              font=("Segoe UI", 9), foreground=theme['muted']).pack(pady=3)
    
    ttk.Label(main_frame, text="New Power Limit (Watts)", font=("Segoe UI", 9)).pack(pady=(0, 5), anchor='w')
    entry = ttk.Entry(main_frame, width=20, font=("Segoe UI", 11))
    entry.pack(pady=(0, 10), ipady=4, fill='x')
    entry.focus()
    
    msg = tk.StringVar()
    msg_label = ttk.Label(main_frame, textvariable=msg, font=("Segoe UI", 9))
    msg_label.pack(pady=8)
    
    def apply_limit():
        try:
            val = float(entry.get())
            if min_ is not None and max_ is not None and (val < min_ or val > max_):
                msg.set(f"Value must be between {min_} and {max_} W")
                msg_label.config(foreground=theme['danger'])
                return
            ok, out = set_power_limit(val)
            if ok:
                msg.set("Power limit applied successfully")
                msg_label.config(foreground=theme['success'])
            else:
                msg.set(f"Failed: {out[:40]}")
                msg_label.config(foreground=theme['danger'])
        except Exception as e:
            msg.set("Invalid input - please enter a number")
            msg_label.config(foreground=theme['danger'])
    
    button_frame = ttk.Frame(main_frame)
    button_frame.pack(pady=15)
    ttk.Button(button_frame, text="Apply Changes", style='Accent.TButton', command=apply_limit).pack(side="left", padx=5)
    ttk.Button(button_frame, text="Cancel", command=lambda: power_win.destroy()).pack(side="left", padx=5)
    
    entry.bind('<Return>', lambda e: apply_limit())
    entry.bind('<Escape>', lambda e: power_win.destroy())
    
    note_text = "Requires administrator privileges to modify"
    if platform.system() == "Windows":
        note_text = "Run application as Administrator to change power limits"
    
    ttk.Label(main_frame, text=note_text, font=("Segoe UI", 8), foreground=theme['muted']).pack(pady=(5,0))
    
    def on_close():
        global power_win
        power_win.destroy()
        power_win = None
    power_win.protocol("WM_DELETE_WINDOW", on_close)

class GPUMonitorApp:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.labels = {}
        self.sort_column = None
        self.sort_reverse = False
        self.graph_window = None
        
        self.root.title(f"NVIDIA-SMI GPU Monitor v{VERSION}")
        self.root.geometry(f"{self.config['window_width']}x{self.config['window_height']}+{self.config['window_x']}+{self.config['window_y']}")
        
        self.setup_menu()
        self.apply_theme()
        self.init_gui()
        self.setup_keyboard_shortcuts()
        
        if self.config.get('always_on_top', False):
            self.root.attributes('-topmost', True)
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        self.start_background_update()
        
    def setup_menu(self):
        menubar = Menu(self.root)
        self.root.config(menu=menubar)
        
        file_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Export to CSV", command=self.export_csv)
        file_menu.add_command(label="Export to JSON", command=self.export_json)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.on_closing, accelerator="Ctrl+Q")
        
        view_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="View", menu=view_menu)
        view_menu.add_command(label="Toggle Dark Mode", command=self.toggle_theme, accelerator="Ctrl+D")
        view_menu.add_checkbutton(label="Always on Top", command=self.toggle_always_on_top, 
                                   variable=tk.BooleanVar(value=self.config.get('always_on_top', False)))
        view_menu.add_command(label="Refresh", command=self.manual_refresh, accelerator="F5")
        if MATPLOTLIB_AVAILABLE:
            view_menu.add_command(label="Show Graphs", command=self.show_graphs, accelerator="Ctrl+G")
        
        settings_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Settings", menu=settings_menu)
        settings_menu.add_command(label="Adjust Refresh Rate", command=self.adjust_refresh_rate)
        settings_menu.add_command(label="Alert Settings", command=self.alert_settings)
        settings_menu.add_command(label="Power Limit", command=lambda: open_power_limit_window(self))
        
        help_menu = Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self.show_about)
    
    def setup_keyboard_shortcuts(self):
        self.root.bind('<F5>', lambda e: self.manual_refresh())
        self.root.bind('<Control-q>', lambda e: self.on_closing())
        self.root.bind('<Control-d>', lambda e: self.toggle_theme())
        if MATPLOTLIB_AVAILABLE:
            self.root.bind('<Control-g>', lambda e: self.show_graphs())
    
    def apply_theme(self):
        theme = DARK_THEME if self.config['dark_mode'] else LIGHT_THEME
        self.root.configure(bg=theme['bg'])
        
        style = ttk.Style()
        style.theme_use('clam')
        
        style.configure('.', background=theme['bg'], foreground=theme['fg'], 
                      borderwidth=0, relief='flat')
        style.configure('TFrame', background=theme['bg'])
        style.configure('TLabel', background=theme['bg'], foreground=theme['fg'])
        
        style.configure('Title.TLabel', 
                      background=theme['bg'], 
                      foreground=theme['accent'],
                      font=('Segoe UI', 13, 'bold'))
        
        style.configure('Subtitle.TLabel',
                      background=theme['bg'],
                      foreground=theme['muted'],
                      font=('Segoe UI', 9))
        
        style.configure('Header.TLabel',
                      background=theme['card_bg'],
                      foreground=theme['fg'],
                      font=('Segoe UI', 11, 'bold'))
        
        style.configure('Metric.TLabel',
                      background=theme['card_bg'],
                      foreground=theme['muted'],
                      font=('Segoe UI', 9))
        
        style.configure('Value.TLabel',
                      background=theme['card_bg'],
                      foreground=theme['fg'],
                      font=('Segoe UI Semibold', 11))
        
        style.configure('Accent.TButton', 
                      background=theme['button_bg'], 
                      foreground=theme['button_fg'],
                      borderwidth=0,
                      relief='flat',
                      padding=(20, 8),
                      font=('Segoe UI Semibold', 9))
        style.map('Accent.TButton', 
                 background=[('active', theme['button_hover']), ('pressed', theme['button_hover'])],
                 relief=[('pressed', 'flat')])
        
        style.configure('TButton',
                      background=theme['card_bg'],
                      foreground=theme['fg'],
                      borderwidth=1,
                      relief='flat',
                      padding=(12, 6))
        style.map('TButton',
                 background=[('active', theme['hover_bg']), ('pressed', theme['select_bg'])],
                 foreground=[('active', theme['fg'])])
        
        style.configure('Treeview',
                      background=theme['tree_bg'],
                      foreground=theme['tree_fg'],
                      fieldbackground=theme['tree_bg'],
                      borderwidth=0,
                      relief='flat',
                      rowheight=28)
        style.configure('Treeview.Heading',
                      background=theme['card_bg'],
                      foreground=theme['accent'],
                      borderwidth=0,
                      relief='flat',
                      font=('Segoe UI Semibold', 9))
        style.map('Treeview.Heading',
                 background=[('active', theme['hover_bg'])],
                 relief=[('active', 'flat')])
        style.map('Treeview',
                 background=[('selected', theme['tree_select_bg'])],
                 foreground=[('selected', theme['tree_select_fg'])])
        
        style.configure('Card.TFrame', 
                      background=theme['card_bg'], 
                      relief='flat',
                      borderwidth=1,
                      bordercolor=theme['card_border'])
        
        style.configure('Header.TFrame',
                      background=theme['card_bg'],
                      relief='flat')
        
        style.configure('Separator.TFrame',
                      background=theme['separator'],
                      relief='flat')

        style.configure('Metric.Horizontal.TProgressbar',
                  background=theme['accent'],
                  troughcolor=theme['card_bg'],
                  bordercolor=theme['card_border'],
                  lightcolor=theme['accent'],
                  darkcolor=theme['accent'],
                  thickness=6)
    
    def update_text_widget_colors(self):
        theme = DARK_THEME if self.config['dark_mode'] else LIGHT_THEME
        if 'full_output_text' in self.labels:
            self.labels['full_output_text'].config(bg=theme['text_bg'], fg=theme['text_fg'])
    
    def toggle_theme(self):
        self.config['dark_mode'] = not self.config['dark_mode']
        save_config(self.config)
        self.apply_theme()
        self.update_text_widget_colors()
        messagebox.showinfo("Theme Changed", "Theme has been updated!")
    
    def toggle_always_on_top(self):
        self.config['always_on_top'] = not self.config.get('always_on_top', False)
        self.root.attributes('-topmost', self.config['always_on_top'])
        save_config(self.config)
    
    def adjust_refresh_rate(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Adjust Refresh Rate")
        dialog.resizable(False, False)
        dialog.grab_set()
        
        ttk.Label(dialog, text=f"Current refresh rate: {self.config['refresh_rate']/1000:.1f} seconds", font=("Arial", 11)).pack(pady=10)
        ttk.Label(dialog, text="Enter new refresh rate (seconds):", font=("Arial", 11)).pack()
        
        entry = ttk.Entry(dialog, width=10)
        entry.insert(0, str(self.config['refresh_rate']/1000))
        entry.pack(pady=5)
        
        def apply():
            try:
                val = float(entry.get())
                if val < 0.5 or val > 60:
                    messagebox.showerror("Invalid Input", "Refresh rate must be between 0.5 and 60 seconds")
                    return
                self.config['refresh_rate'] = int(val * 1000)
                save_config(self.config)
                messagebox.showinfo("Success", "Refresh rate updated. Will take effect on next update.")
                dialog.destroy()
            except:
                messagebox.showerror("Invalid Input", "Please enter a valid number")
        
        ttk.Button(dialog, text="Apply", command=apply).pack(pady=10)
    
    def alert_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Alert Settings")
        dialog.resizable(False, False)
        dialog.grab_set()
        
        ttk.Label(dialog, text="Temperature Alert Threshold (°C):", font=("Arial", 11)).pack(pady=5)
        temp_entry = ttk.Entry(dialog, width=10)
        temp_entry.insert(0, str(self.config.get('alert_temp', 80)))
        temp_entry.pack()
        
        ttk.Label(dialog, text="Utilization Alert Threshold (%):", font=("Arial", 11)).pack(pady=5)
        util_entry = ttk.Entry(dialog, width=10)
        util_entry.insert(0, str(self.config.get('alert_util', 90)))
        util_entry.pack()
        
        enabled_var = tk.BooleanVar(value=self.config.get('alert_enabled', True))
        ttk.Checkbutton(dialog, text="Enable Alerts", variable=enabled_var).pack(pady=10)
        
        if not PLYER_AVAILABLE:
            ttk.Label(dialog, text="Note: Install 'plyer' package for desktop notifications", 
                     font=("Arial", 9, "italic"), foreground="orange").pack()
        
        def apply():
            try:
                self.config['alert_temp'] = int(temp_entry.get())
                self.config['alert_util'] = int(util_entry.get())
                self.config['alert_enabled'] = enabled_var.get()
                save_config(self.config)
                messagebox.showinfo("Success", "Alert settings updated")
                dialog.destroy()
            except:
                messagebox.showerror("Invalid Input", "Please enter valid numbers")
        
        ttk.Button(dialog, text="Apply", command=apply).pack(pady=10)
    
    def export_csv(self):
        from tkinter import filedialog
        filename = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if filename:
            if export_to_csv(filename):
                messagebox.showinfo("Success", f"Data exported to {filename}")
            else:
                messagebox.showerror("Error", "Failed to export data")
    
    def export_json(self):
        from tkinter import filedialog
        filename = filedialog.asksaveasfilename(defaultextension=".json", filetypes=[("JSON files", "*.json")])
        if filename:
            if export_to_json(filename):
                messagebox.showinfo("Success", f"Data exported to {filename}")
            else:
                messagebox.showerror("Error", "Failed to export data")
    
    def show_about(self):
        about_text = f"""NVIDIA-SMI GPU Monitor
Version {VERSION}

A real-time GPU monitoring tool with enhanced features:
• Real-time metrics with color coding
• Historical graphs and data export
• Desktop notifications and alerts
• Customizable refresh rate and themes
• System tray support

GPU: {static_cache.get('gpu_name', 'Unknown')}
Platform: {platform.system()} {platform.release()}

© 2026 - MIT License"""
        messagebox.showinfo("About", about_text)
    
    def manual_refresh(self):
        global stop_update
        stop_update = True
        if update_thread and update_thread.is_alive():
            update_thread.join(timeout=1)
        stop_update = False
        self.start_background_update()
    
    def show_graphs(self):
        if not MATPLOTLIB_AVAILABLE:
            messagebox.showwarning("Matplotlib Not Available", "Install matplotlib to view graphs:\npip install matplotlib")
            return
        
        if self.graph_window and tk.Toplevel.winfo_exists(self.graph_window):
            self.graph_window.lift()
            return
        
        self.graph_window = tk.Toplevel(self.root)
        self.graph_window.title("GPU History Graphs")
        self.graph_window.geometry("1000x700")

        theme = DARK_THEME if self.config['dark_mode'] else LIGHT_THEME
        
        fig = Figure(figsize=(10, 7), dpi=100)
        fig.patch.set_facecolor(theme['bg'])
        
        ax1 = fig.add_subplot(2, 2, 1)
        ax1.set_title('GPU Utilization')
        ax1.set_ylabel('Utilization (%)')
        ax1.set_facecolor(theme['card_bg'])
        ax1.grid(True, alpha=0.25, color=theme['muted'])
        ax1.tick_params(colors=theme['fg'])
        ax1.title.set_color(theme['fg'])
        ax1.yaxis.label.set_color(theme['fg'])
        for spine in ax1.spines.values():
            spine.set_edgecolor(theme['card_border'])
        
        ax2 = fig.add_subplot(2, 2, 2)
        ax2.set_title('Temperature')
        ax2.set_ylabel('Temperature (°C)')
        ax2.set_facecolor(theme['card_bg'])
        ax2.grid(True, alpha=0.25, color=theme['muted'])
        ax2.tick_params(colors=theme['fg'])
        ax2.title.set_color(theme['fg'])
        ax2.yaxis.label.set_color(theme['fg'])
        for spine in ax2.spines.values():
            spine.set_edgecolor(theme['card_border'])
        
        ax3 = fig.add_subplot(2, 2, 3)
        ax3.set_title('Power Draw')
        ax3.set_ylabel('Power (W)')
        ax3.set_facecolor(theme['card_bg'])
        ax3.grid(True, alpha=0.25, color=theme['muted'])
        ax3.tick_params(colors=theme['fg'])
        ax3.title.set_color(theme['fg'])
        ax3.yaxis.label.set_color(theme['fg'])
        for spine in ax3.spines.values():
            spine.set_edgecolor(theme['card_border'])
        
        ax4 = fig.add_subplot(2, 2, 4)
        ax4.set_title('Memory Usage')
        ax4.set_ylabel('Memory (%)')
        ax4.set_facecolor(theme['card_bg'])
        ax4.grid(True, alpha=0.25, color=theme['muted'])
        ax4.tick_params(colors=theme['fg'])
        ax4.title.set_color(theme['fg'])
        ax4.yaxis.label.set_color(theme['fg'])
        for spine in ax4.spines.values():
            spine.set_edgecolor(theme['card_border'])
        
        canvas = FigureCanvasTkAgg(fig, master=self.graph_window)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        
        def update_graphs():
            if not self.graph_window or not tk.Toplevel.winfo_exists(self.graph_window):
                return
            
            if len(history_data['time']) > 0:
                time_range = list(range(len(history_data['time'])))
                
                ax1.clear()
                ax1.set_facecolor(theme['card_bg'])
                ax1.grid(True, alpha=0.25, color=theme['muted'])
                ax1.plot(time_range, list(history_data['utilization']), color=theme['accent'], linewidth=2)
                ax1.set_title('GPU Utilization')
                ax1.set_ylabel('Utilization (%)')
                ax1.tick_params(colors=theme['fg'])
                ax1.title.set_color(theme['fg'])
                ax1.yaxis.label.set_color(theme['fg'])
                for spine in ax1.spines.values():
                    spine.set_edgecolor(theme['card_border'])
                
                ax2.clear()
                ax2.set_facecolor(theme['card_bg'])
                ax2.grid(True, alpha=0.25, color=theme['muted'])
                ax2.plot(time_range, list(history_data['temperature']), color='#d79b00', linewidth=2)
                ax2.set_title('Temperature')
                ax2.set_ylabel('Temperature (°C)')
                ax2.tick_params(colors=theme['fg'])
                ax2.title.set_color(theme['fg'])
                ax2.yaxis.label.set_color(theme['fg'])
                for spine in ax2.spines.values():
                    spine.set_edgecolor(theme['card_border'])
                
                ax3.clear()
                ax3.set_facecolor(theme['card_bg'])
                ax3.grid(True, alpha=0.25, color=theme['muted'])
                ax3.plot(time_range, list(history_data['power']), color=theme['accent'], linewidth=2)
                ax3.set_title('Power Draw')
                ax3.set_ylabel('Power (W)')
                ax3.tick_params(colors=theme['fg'])
                ax3.title.set_color(theme['fg'])
                ax3.yaxis.label.set_color(theme['fg'])
                for spine in ax3.spines.values():
                    spine.set_edgecolor(theme['card_border'])
                
                ax4.clear()
                ax4.set_facecolor(theme['card_bg'])
                ax4.grid(True, alpha=0.25, color=theme['muted'])
                ax4.plot(time_range, list(history_data['memory']), color='#5a8f00', linewidth=2)
                ax4.set_title('Memory Usage')
                ax4.set_ylabel('Memory (%)')
                ax4.tick_params(colors=theme['fg'])
                ax4.title.set_color(theme['fg'])
                ax4.yaxis.label.set_color(theme['fg'])
                for spine in ax4.spines.values():
                    spine.set_edgecolor(theme['card_border'])
                
                fig.tight_layout()
                canvas.draw()
            
            self.root.after(2000, update_graphs)
        
        update_graphs()
        
        def on_graph_close():
            self.graph_window.destroy()
            self.graph_window = None
        
        self.graph_window.protocol("WM_DELETE_WINDOW", on_graph_close)
    
    def sort_treeview(self, col):
        if self.sort_column == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column = col
            self.sort_reverse = False
        
        items = [(self.labels['proc_table'].set(k, col), k) for k in self.labels['proc_table'].get_children('')]
        
        try:
            items.sort(key=lambda t: float(t[0].split()[0]) if t[0] else 0, reverse=self.sort_reverse)
        except:
            items.sort(reverse=self.sort_reverse)
        
        for index, (val, k) in enumerate(items):
            self.labels['proc_table'].move(k, '', index)

    def copy_selected_value(self, column_index):
        selection = self.labels['proc_table'].selection()
        if not selection:
            return
        values = self.labels['proc_table'].item(selection[0], 'values')
        if column_index >= len(values):
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(str(values[column_index]))
        self.labels['status'].config(text=f"Copied: {values[column_index]}")

    def end_selected_task(self):
        selection = self.labels['proc_table'].selection()
        if not selection:
            return
        values = self.labels['proc_table'].item(selection[0], 'values')
        if len(values) < 2:
            return
        pid, name = values[0], values[1]
        if not messagebox.askyesno("End Task", f"End process {name} (PID {pid})?"):
            return
        try:
            if platform.system() == "Windows":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=True, capture_output=True)
            else:
                os.kill(int(pid), 9)
            self.labels['status'].config(text=f"Ended process {pid}")
        except Exception as e:
            messagebox.showerror("Error", f"Could not end task: {e}")

    def init_gui(self):
        name = get_gpu_info()
        theme = DARK_THEME if self.config['dark_mode'] else LIGHT_THEME
        
        header_outer = tk.Frame(self.root, bg=theme['accent'], height=85)
        header_outer.pack(fill="x", padx=0, pady=0)
        header_outer.pack_propagate(False)
        
        header_content = tk.Frame(header_outer, bg=theme['accent'])
        header_content.pack(side='left', padx=20, pady=15)
        
        gpu_title = tk.Label(header_content, text="NVIDIA GPU MONITOR", 
                             font=("Segoe UI", 8, "bold"), 
                             bg=theme['accent'], fg="#c8d1dc")
        gpu_title.pack(anchor='w')
        
        self.labels['gpu'] = tk.Label(header_content, text=name, 
                                      font=("Segoe UI", 17, "bold"), 
                                      bg=theme['accent'], fg="#ffffff")
        self.labels['gpu'].pack(anchor='w', pady=(3, 0))
        
        btn_container = tk.Frame(header_outer, bg=theme['accent'])
        btn_container.pack(side='right', padx=20, pady=15)

        power_btn = tk.Button(btn_container, text="Power Settings", 
                              bg="#ffffff", fg=theme['accent'],
                              font=("Segoe UI Semibold", 9),
                              relief='flat', borderwidth=1, highlightthickness=0,
                              padx=18, pady=8, cursor='hand2',
                              command=lambda: open_power_limit_window(self))
        power_btn.pack()
        
        def on_enter(e):
            power_btn.config(bg="#f0f7e8", highlightbackground=theme['accent'], highlightcolor=theme['accent'])
        def on_leave(e):
            power_btn.config(bg="#ffffff", highlightbackground=theme['card_border'], highlightcolor=theme['card_border'])
        power_btn.bind("<Enter>", on_enter)
        power_btn.bind("<Leave>", on_leave)

        divider = tk.Frame(self.root, bg=theme['header_gradient_end'], height=3)
        divider.pack(fill='x', padx=0, pady=0)

        toolbar = tk.Frame(self.root, bg=theme['bg'], height=40)
        toolbar.pack(fill='x', padx=0, pady=0)
        toolbar.pack_propagate(False)

        toolbar_inner = tk.Frame(toolbar, bg=theme['bg'])
        toolbar_inner.pack(side='left', padx=15, pady=8)

        def make_toolbar_btn(text, cmd, tooltip):
            btn = tk.Button(
                toolbar_inner,
                text=text,
                bg=theme['card_bg'],
                fg=theme['accent'],
                font=("Segoe UI", 8),
                relief='flat',
                borderwidth=1,
                highlightthickness=0,
                padx=12,
                pady=4,
                cursor='hand2',
                command=cmd
            )
            btn.pack(side='left', padx=4)
            create_tooltip(btn, tooltip)
            return btn

        make_toolbar_btn("Refresh", self.manual_refresh, "Refresh now (F5)")
        if MATPLOTLIB_AVAILABLE:
            make_toolbar_btn("Graphs", self.show_graphs, "Open graphs (Ctrl+G)")
        make_toolbar_btn("Theme", self.toggle_theme, "Toggle light/dark (Ctrl+D)")

        metrics_container = ttk.Frame(self.root)
        metrics_container.pack(padx=20, pady=(15, 10), fill="x")
        
        row_container = ttk.Frame(metrics_container)
        row_container.pack(fill="x")
        
        metrics = [
            ('util', 'GPU Utilization', 'Percentage of GPU compute being used'),
            ('mem', 'Memory Usage', 'VRAM used out of total available'),
            ('temp', 'Temperature', 'GPU core temperature in Celsius'),
            ('power', 'Power Draw', 'Current power consumption vs limit')
        ]
        metric_max = {'util': 100, 'mem': 100, 'temp': 100, 'power': 120}
        
        for i, (key, title, tooltip) in enumerate(metrics):
            card = ttk.Frame(row_container, style='Card.TFrame')
            card.pack(side='left', fill='both', expand=True, padx=(0 if i == 0 else 8, 0))
            
            inner = ttk.Frame(card, style='Card.TFrame')
            inner.pack(padx=15, pady=12, fill='both', expand=True)
            
            label = ttk.Label(inner, text=title.upper(), style='Metric.TLabel')
            label.pack(anchor='w')
            create_tooltip(label, tooltip)
            
            self.labels[key] = ttk.Label(inner, text="--", style='Value.TLabel')
            self.labels[key].pack(anchor='w', pady=(4, 0))

            bar = ttk.Progressbar(inner, style='Metric.Horizontal.TProgressbar', mode='determinate', maximum=metric_max.get(key, 100))
            bar.pack(fill='x', pady=(6, 0))
            self.labels[f"{key}_bar"] = bar
        
        row_container2 = ttk.Frame(metrics_container)
        row_container2.pack(fill="x", pady=(10, 0))
        
        metrics2 = [
            ('fan', 'Fan Speed', 'Cooling fan RPM percentage'),
            ('clock_gpu', 'GPU Clock', 'Core processing frequency'),
            ('clock_mem', 'Memory Clock', 'VRAM operating frequency')
        ]
        
        for i, (key, title, tooltip) in enumerate(metrics2):
            card = ttk.Frame(row_container2, style='Card.TFrame')
            card.pack(side='left', fill='both', expand=True, padx=(0 if i == 0 else 8, 0))
            
            inner = ttk.Frame(card, style='Card.TFrame')
            inner.pack(padx=15, pady=12, fill='both', expand=True)
            
            label = ttk.Label(inner, text=title.upper(), style='Metric.TLabel')
            label.pack(anchor='w')
            create_tooltip(label, tooltip)
            
            self.labels[key] = ttk.Label(inner, text="--", style='Value.TLabel')
            self.labels[key].pack(anchor='w', pady=(4, 0))
        
        separator = ttk.Frame(self.root, height=1, style='Separator.TFrame')
        separator.pack(fill='x', padx=20, pady=(15, 0))
        
        self.labels['proc_label'] = ttk.Label(self.root, text="RUNNING PROCESSES", style='Title.TLabel')
        self.labels['proc_label'].pack(pady=(20, 10), padx=20, anchor='w')
        
        proc_card = ttk.Frame(self.root, style='Card.TFrame')
        proc_card.pack(padx=20, pady=(0, 15), fill="both", expand=True)
        
        proc_frame = ttk.Frame(proc_card, style='Card.TFrame')
        proc_frame.pack(padx=15, pady=15, fill="both", expand=True)
        
        self.labels['proc_table'] = ttk.Treeview(proc_frame, columns=("PID", "Name", "Memory"), 
                                                 show="headings", height=8)
        self.labels['proc_table'].heading("PID", text="PROCESS ID", command=lambda: self.sort_treeview("PID"))
        self.labels['proc_table'].heading("Name", text="APPLICATION NAME", command=lambda: self.sort_treeview("Name"))
        self.labels['proc_table'].heading("Memory", text="VRAM USAGE", command=lambda: self.sort_treeview("Memory"))
        self.labels['proc_table'].column("PID", width=100, anchor="center")
        self.labels['proc_table'].column("Name", width=350, anchor="w")
        self.labels['proc_table'].column("Memory", width=130, anchor="center")
        # Tag styles for striping/hover
        theme = DARK_THEME if self.config['dark_mode'] else LIGHT_THEME
        self.labels['proc_table'].tag_configure('even', background=theme['tree_bg'])
        self.labels['proc_table'].tag_configure('odd', background=theme['tree_alt_bg'])
        self.labels['proc_table'].tag_configure('hover', background=theme['hover_bg'])
        self.hover_row = None
        self.proc_context = Menu(proc_frame, tearoff=0)
        self.proc_context.add_command(label="Copy PID", command=lambda: self.copy_selected_value(0))
        self.proc_context.add_command(label="Copy Name", command=lambda: self.copy_selected_value(1))
        self.proc_context.add_separator()
        self.proc_context.add_command(label="End Task", command=self.end_selected_task)
        
        scrollbar = ttk.Scrollbar(proc_frame, orient="vertical", command=self.labels['proc_table'].yview)
        self.labels['proc_table'].configure(yscrollcommand=scrollbar.set)
        self.labels['proc_table'].pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def on_motion(event):
            row = self.labels['proc_table'].identify_row(event.y)
            if row == self.hover_row:
                return
            if self.hover_row:
                self.labels['proc_table'].item(self.hover_row, tags=[t for t in self.labels['proc_table'].item(self.hover_row, 'tags') if t != 'hover'])
            self.hover_row = row
            if row:
                tags = list(self.labels['proc_table'].item(row, 'tags'))
                if 'hover' not in tags:
                    tags.append('hover')
                self.labels['proc_table'].item(row, tags=tags)

        self.labels['proc_table'].bind('<Motion>', on_motion)

        def show_context(event):
            row = self.labels['proc_table'].identify_row(event.y)
            if row:
                self.labels['proc_table'].selection_set(row)
            try:
                self.proc_context.tk_popup(event.x_root, event.y_root)
            finally:
                self.proc_context.grab_release()

        self.labels['proc_table'].bind('<Button-3>', show_context)

        def toggle_full_output():
            if self.labels['full_output_frame'].winfo_ismapped():
                self.labels['full_output_frame'].pack_forget()
                self.labels['show_btn'].config(text="Show Detailed Output")
            else:
                self.labels['full_output_frame'].pack(padx=20, pady=(0, 15), fill='both', expand=True)
                self.labels['show_btn'].config(text="Hide Detailed Output")

        self.labels['show_btn'] = ttk.Button(self.root, text="Show Detailed Output", command=toggle_full_output)
        self.labels['show_btn'].pack(pady=(5, 10))
        self.labels['full_output_frame'] = ttk.Frame(self.root, style='Card.TFrame')
        
        text_inner = ttk.Frame(self.labels['full_output_frame'], style='Card.TFrame')
        text_inner.pack(padx=15, pady=15, fill="both", expand=True)
        
        output_label = ttk.Label(text_inner, text="NVIDIA-SMI OUTPUT", style='Metric.TLabel')
        output_label.pack(anchor='w', pady=(0, 8))
        
        theme = DARK_THEME if self.config['dark_mode'] else LIGHT_THEME
        self.labels['full_output_text'] = tk.Text(text_inner, height=14, width=90, 
                                                  font=("Consolas", 9), wrap="none",
                                                  bg=theme['text_bg'], fg=theme['text_fg'],
                                                  relief='flat', borderwidth=0,
                                                  padx=10, pady=8)
        self.labels['full_output_text'].pack(fill="both", expand=True)
        self.labels['full_output_text'].config(state="disabled")
        
        status_frame = tk.Frame(self.root, bg=theme['card_bg'], height=35)
        status_frame.pack(side="bottom", fill="x")
        status_frame.pack_propagate(False)
        
        self.labels['status'] = tk.Label(status_frame, text="Initializing system...", 
                                        font=("Segoe UI", 8), anchor="w",
                                        bg=theme['accent_light'], fg=theme['fg'])
        self.labels['status'].pack(side="left", fill="x", expand=True, padx=20)

    def start_background_update(self):
        global update_thread, stop_update
        stop_update = False
        
        def background_worker():
            while not stop_update:
                try:
                    gpu_output, proc_output = get_nvidia_smi_output()
                    gpu_data_cache['gpu_output'] = gpu_output
                    gpu_data_cache['proc_output'] = proc_output
                    gpu_data_cache['timestamp'] = datetime.now()
                except Exception as e:
                    gpu_data_cache['error'] = str(e)
                
                for _ in range(self.config['refresh_rate'] // 100):
                    if stop_update:
                        break
                    time.sleep(0.1)
        
        update_thread = threading.Thread(target=background_worker, daemon=True)
        update_thread.start()
        self.update_gui()

    def update_gui(self):
        if stop_update:
            return
        
        if 'error' in gpu_data_cache:
            self.labels['gpu'].config(text=f"Error: {gpu_data_cache['error']}")
            self.root.after(3000, self.update_gui)
            return
        
        if 'gpu_output' not in gpu_data_cache:
            self.root.after(500, self.update_gui)
            return
        
        gpu_output = gpu_data_cache.get('gpu_output', '')
        proc_output = gpu_data_cache.get('proc_output', '')
        timestamp = gpu_data_cache.get('timestamp', datetime.now())
        
        if gpu_output.startswith("Error"):
            self.labels['gpu'].config(text=gpu_output)
            self.labels['status'].config(text=f"Error at {timestamp.strftime('%H:%M:%S')}")
            self.root.after(3000, self.update_gui)
            return

        metrics = parse_gpu_metrics(gpu_output)
        processes = parse_processes(proc_output)
        
        history_data['time'].append(timestamp.strftime('%H:%M:%S'))
        history_data['utilization'].append(metrics.get("utilization", 0))
        history_data['temperature'].append(metrics.get("temperature", 0))
        history_data['power'].append(metrics.get("power_draw", 0))
        mem_used = metrics.get("mem_used", 0)
        mem_total = metrics.get("mem_total", 1)
        mem_percent = (mem_used / mem_total) * 100 if mem_total else 0
        history_data['memory'].append(mem_percent)
        
        check_alerts(metrics, self.config)

        util_val = metrics.get("utilization", 0)
        util_color = color_for_percent(util_val)
        self.labels['util'].config(text=f"{util_val:.1f}%", foreground=util_color)
        if 'util_bar' in self.labels:
            self.labels['util_bar']['value'] = max(0, min(100, util_val))

        mem_str, mem_percent = format_memory(mem_used, mem_total)
        mem_color = color_for_percent(mem_percent)
        self.labels['mem'].config(text=mem_str, foreground=mem_color)
        if 'mem_bar' in self.labels:
            self.labels['mem_bar']['value'] = max(0, min(100, mem_percent))

        temp = metrics.get("temperature", 0)
        temp_color = color_for_temp(temp)
        self.labels['temp'].config(text=f"{temp}°C", foreground=temp_color)
        if 'temp_bar' in self.labels:
            self.labels['temp_bar']['value'] = max(0, min(100, temp))

        power_draw = metrics.get("power_draw", 0)
        power_limit = metrics.get("power_limit", 1)
        power_color = color_for_power(power_draw, power_limit)
        power_str = f"{power_draw:.1f} W / {power_limit:.1f} W"
        self.labels['power'].config(text=power_str, foreground=power_color)
        if 'power_bar' in self.labels:
            pct = (power_draw / power_limit * 100) if power_limit else 0
            self.labels['power_bar']['value'] = max(0, min(120, pct))
        
        fan_speed = metrics.get("fan_speed")
        if fan_speed is not None:
            self.labels['fan'].config(text=f"{fan_speed:.0f}%", foreground="blue")
        else:
            self.labels['fan'].config(text="N/A", foreground="gray")
        
        clock_gpu = metrics.get("clock_gpu", 0)
        self.labels['clock_gpu'].config(text=f"{clock_gpu:.0f} MHz", foreground="purple")
        
        clock_mem = metrics.get("clock_mem", 0)
        self.labels['clock_mem'].config(text=f"{clock_mem:.0f} MHz", foreground="purple")

        self.labels['proc_table'].delete(*self.labels['proc_table'].get_children())
        theme = DARK_THEME if self.config['dark_mode'] else LIGHT_THEME
        self.labels['proc_table'].tag_configure('even', background=theme['tree_bg'], foreground=theme['tree_fg'])
        self.labels['proc_table'].tag_configure('odd', background=theme['tree_alt_bg'], foreground=theme['tree_fg'])
        self.labels['proc_table'].tag_configure('hover', background=theme['hover_bg'], foreground=theme['fg'])
        for idx, proc in enumerate(processes):
            tag = 'even' if idx % 2 == 0 else 'odd'
            self.labels['proc_table'].insert("", "end", values=(proc["pid"], proc["name"], proc["mem"]), tags=(tag,))

        self.labels['full_output_text'].config(state="normal")
        self.labels['full_output_text'].delete("1.0", tk.END)
        self.labels['full_output_text'].insert("1.0", subprocess.getoutput("nvidia-smi"))
        self.labels['full_output_text'].config(state="disabled")
        
        self.labels['status'].config(text=f"Last updated {timestamp.strftime('%H:%M:%S')} · Refresh rate {self.config['refresh_rate']/1000:.1f}s")
        
        if PYSTRAY_AVAILABLE and tray_icon:
            tray_icon.title = f"GPU: {util_val:.0f}% | Temp: {temp}°C"

        self.root.after(self.config['refresh_rate'], self.update_gui)
    
    def on_closing(self):
        global stop_update, tray_icon
        stop_update = True
        
        geometry = self.root.geometry()
        match = re.match(r'(\d+)x(\d+)\+(\d+)\+(\d+)', geometry)
        if match:
            self.config['window_width'] = int(match.group(1))
            self.config['window_height'] = int(match.group(2))
            self.config['window_x'] = int(match.group(3))
            self.config['window_y'] = int(match.group(4))
        
        save_config(self.config)
        
        if tray_icon:
            tray_icon.stop()
        
        self.root.quit()
        self.root.destroy()

def setup_tray_icon(app):
    if not PYSTRAY_AVAILABLE:
        return None
    
    try:
        theme = DARK_THEME if app.config.get('dark_mode') else LIGHT_THEME
        image = create_tray_icon_image(theme)
        if image is None:
            return None
        
        def show_window(icon, item):
            app.root.deiconify()
            app.root.lift()
        
        def quit_app(icon, item):
            icon.stop()
            app.on_closing()
        
        menu = pystray.Menu(
            pystray.MenuItem("Show", show_window, default=True),
            pystray.MenuItem("Quit", quit_app)
        )
        
        icon = pystray.Icon("nvidia_monitor", image, "NVIDIA GPU Monitor", menu)
        
        def run_tray():
            icon.run()
        
        tray_thread = threading.Thread(target=run_tray, daemon=True)
        tray_thread.start()
        
        return icon
    except:
        return None

root = tk.Tk()
app = GPUMonitorApp(root)

if PYSTRAY_AVAILABLE:
    tray_icon = setup_tray_icon(app)

root.mainloop()