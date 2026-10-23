# AGENTS.md — nvidia-smi-tk

A Tkinter desktop monitor for NVIDIA GPUs built on the `nvidia-smi` CLI: live utilisation, memory, temperature, power, clocks and fan, a process table with search, history graphs (matplotlib, optional), tray icon (pystray, optional), desktop alerts (plyer, optional), CSV/JSON export, dark mode, multi-GPU support, and power-limit adjustment.

Code is organized with clear boundaries: `nvidia_smi.py` (queries/pure parsing), `config.py` (configuration persistence to `~/.nvidia_smi_tk_config.json`), and `App.py` (Tkinter GUI entrypoint).

## Commands

```bash
python3 App.py                              # stdlib only; needs Tk (python3-tk on Debian/Ubuntu) and nvidia-smi on PATH
pip install -r requirements.txt             # optional extras: matplotlib, pillow, pystray, plyer
python -m unittest discover -s tests -p "test_*.py" -v   # unit test suite (pure stdlib, no GPU needed)
pytest tests/ -v                            # unit test suite via pytest
python3 -m py_compile App.py config.py nvidia_smi.py     # smoke check syntax
pyinstaller --onefile --noconsole --name nvidia-smi-gui-monitor App.py   # what CI builds on Windows
```

CI (`.github/workflows/test-build.yml`, `build-release.yml`) verifies syntax compilation, runs the pure unit tests across platforms, and produces Windows and Linux executables.

## How it works

- Data queries and pure line-by-line parsing live in `nvidia_smi.py` (`get_nvidia_smi_output`, `parse_gpu_metrics`, `parse_processes`, `parse_power_limits`).
- A background worker thread fetches data; the UI refreshes via `root.after(...)` in `GPUMonitorApp.update_gui`.
- Multi-GPU setups display a dedicated card for each GPU (using `--query-gpu=index,name,...`) plus an aggregate combined overview.
- Power limit: validated against min/max limits; executed using `pkexec` (polkit) on Linux or elevated execution on Windows.

## Rules

- Keep parsing pure (string in → dict/list out) and testable without a GPU: keep tests updated with captured `nvidia-smi` CSV samples (single GPU, multi-GPU, `[N/A]` fields, no driver).
- Never call `nvidia-smi` on the Tk main thread, and never touch widgets from the worker thread.
- Pass arguments as lists to `subprocess` (never `shell=True`), validate power limits against the min/max that `nvidia-smi` reports, and show its error output to the user.
- Optional dependencies stay optional: the app must start with only the standard library.
- Keep a clear boundary: `nvidia_smi.py` (queries/parsing), `config.py`, and `App.py`.

## Git workflow

Branch and open a PR unless the owner asks for a direct push to `main`; run the app on a machine with an NVIDIA GPU if you touched queries or parsing, and say in the PR which GPU/driver/OS you tested.
