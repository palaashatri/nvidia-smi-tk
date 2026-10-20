# AGENTS.md — nvidia-smi-tk

A Tkinter desktop monitor for NVIDIA GPUs built on the `nvidia-smi` CLI: live utilisation, memory, temperature, power, clocks and fan, a process table, history graphs (matplotlib, optional), tray icon (pystray, optional), desktop alerts (plyer, optional), CSV/JSON export, dark mode, and power-limit adjustment.

Everything is in one file, `App.py` (~1,430 lines). Settings persist to `~/.nvidia_smi_tk_config.json`.

## Commands

```bash
python3 App.py                              # stdlib only; needs Tk (python3-tk on Debian/Ubuntu) and nvidia-smi on PATH
pip install -r requirements.txt             # optional extras: matplotlib, pillow, pystray, plyer
python3 -m py_compile App.py                # cheapest smoke check; there are no tests yet
pyinstaller --onefile --noconsole --name nvidia-smi-gui-monitor App.py   # what CI builds on Windows
```

CI (`.github/workflows/test-build.yml`, `build-release.yml`) only checks that PyInstaller produces an executable; it does not run any tests.

## How it works

- Data comes from `nvidia-smi --query-gpu=... --format=csv,noheader,nounits` and `--query-compute-apps=...` (functions `get_nvidia_smi_output`, `parse_gpu_metrics`, `parse_processes`).
- A background thread fetches data; the UI refreshes via `root.after(...)` in `GPUMonitorApp.update_gui`.
- Power limit: `nvidia-smi -pl <watts>`, prefixed with `sudo` on Linux.

## Known issues (2026-10-20)

- **Multi-GPU is broken.** `parse_gpu_metrics` splits the whole CSV output on commas, so with two GPUs the lines run together and values are misread. Parse line by line and show one card per GPU (index from `--query-gpu=index,...`).
- Values like `[N/A]` or `[Not Supported]` (common for fan or power on laptops) make `float()` fail and the whole update is dropped; parse each field defensively and show "N/A".
- `sudo nvidia-smi -pl` from a GUI has no terminal for the password prompt, so it fails or hangs. Use `pkexec` (polkit) on Linux, or tell the user to run the app with the required rights; on Windows it needs an elevated process.
- README says "cross-platform … macOS". Current macOS has no NVIDIA driver or `nvidia-smi`; describe the app as Windows and Linux.
- Five unused `except ... as e` variables (ruff F841).

## Rules

- Keep parsing pure (string in → dict/list out) and testable without a GPU: add pytest tests with captured `nvidia-smi` CSV samples (single GPU, multi-GPU, `[N/A]` fields, no driver).
- Never call `nvidia-smi` on the Tk main thread, and never touch widgets from the worker thread.
- Pass arguments as lists to `subprocess` (never `shell=True`), validate power limits against the min/max that `nvidia-smi` reports, and show its error output to the user.
- Optional dependencies stay optional: the app must start with only the standard library.
- When the file is split up, keep a clear boundary: `nvidia_smi.py` (queries/parsing), `config.py`, `ui/`.

## Git workflow

Never push to `main`; branch, run the app on a machine with an NVIDIA GPU if you touched queries or parsing, and say in the PR which GPU/driver/OS you tested.
