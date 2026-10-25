# Implementation audit

Scope: the feature and architecture contract in `AGENTS.md`, audited on
2026-10-25. This audit builds on the existing modular implementation rather
than replacing it.

## Findings addressed by the implementation plan

| Area | Existing defect | Required behavior |
| --- | --- | --- |
| Worker/UI boundary | Tray callbacks invoke Tk from the tray thread | Queue commands and consume them on the Tk thread |
| Window settings | A temporary Tk variable makes Always on Top disagree with the real window state | Retain and synchronize the checkbox variable |
| Refresh recovery | A cached exception prevents later good samples from rendering | Publish complete snapshots and recover on a successful fetch |
| Device disappearance | An empty GPU response retains a stale name and records a healthy-looking sample | Clear cards/processes, show no GPU and omit the sample |
| History | Repeated GUI refreshes can record the same cached sample; unsupported values become zero | Record each sample once and preserve missing values |
| Power dialog | Apply is available before limits load; switching GPUs can reuse stale bounds | Bind bounds to a selected GPU and reject stale completions |
| Power diagnostics | Query failures discard the driver's actual diagnostic text | Use a checked getter in the dialog and retain the compatibility getter for older callers |
| Windows elevation | Launching a UAC command is reported as completed success | Wait for completion and verify the result |
| Parsing/input | Non-finite numbers and comma-containing names are not handled reliably | Reject invalid numbers and parse CSV without corrupting identity |
| Configuration | Arbitrary JSON types flow into widget geometry and scheduling | Normalize persisted values and write configuration safely |
| CI | Only the Linux syntax job runs unit tests; release jobs build without testing or optional features | Test core modules on Windows/Linux, gate packaging on tests and bundle pinned extras |
| Documentation | Python 3.6 and optional-dependency requirements are inaccurate | State the actual runtime floor and which extras each feature needs |

## Validation boundaries

Automated tests use mock subprocesses for privileged operations. Live GPU
validation is read-only: no power limit is changed and no user process is
terminated. Real multi-GPU hardware, Linux polkit authentication and Windows
UAC power changes require separate platform/hardware validation. Fixture tests
and mocked platform branches are not evidence of those live behaviors.

Baseline commands before edits:

- `python -m unittest discover -s tests -p "test_*.py" -v`: one aggregate test
  passed (the existing suite wraps its pure-function assertions in one method).
- `.venv/Scripts/python.exe -m pytest tests/ -q`: 17 tests passed.
- `.venv/Scripts/python.exe -m ruff check App.py config.py nvidia_smi.py ui tests`:
  all checks passed.
- A hidden real-Tk session received live metrics without callback exceptions.
- `nvidia-smi --query-gpu=index,name,driver_version --format=csv,noheader`:
  NVIDIA GeForce RTX 3080, driver 621.01. Host OS query reported Windows NT
  10.0.26300.0.

## Integrated verification

Executed by the coordinating agent after implementation, with Python 3.12.14
on Windows NT 10.0.26300.0, NVIDIA GeForce RTX 3080, driver 621.01:

| Check | Command or exercised path | Actual result |
| --- | --- | --- |
| unittest with extras | `.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py" -v` | 69 tests, OK |
| unittest without extras | Bundled stdlib interpreter, same unittest command | 69 tests, OK |
| pytest | `.venv/Scripts/python.exe -m pytest tests/ -q -rs` | 85 passed |
| Syntax | `.venv/Scripts/python.exe -m compileall -q App.py config.py nvidia_smi.py ui tests` | Exit 0 |
| Ruff | `.venv/Scripts/python.exe -m ruff check App.py config.py nvidia_smi.py ui tests` | All checks passed |
| Formatting | `.venv/Scripts/python.exe -m ruff format --check App.py config.py nvidia_smi.py ui tests` | 13 files already formatted |
| Optional dependencies | `.venv/Scripts/python.exe -m pip check` | No broken requirements found |
| Packaging | `.venv/Scripts/python.exe -m PyInstaller --noconfirm --onefile --noconsole --name nvidia-smi-gui-monitor App.py` | Build complete; executable produced |
| Live UI with extras | Hidden Tk window; real worker queries, CSV/JSON export, theme toggle, process sort/filter, graph close/reopen, read-only power dialog | No callback exceptions; query thread check true; exported CSV/JSON row counts matched history |
| Live UI without extras | Separate stdlib interpreter, hidden Tk window and real worker queries | Startup succeeded; live GPU detected; no callback exceptions |
| PowerShell capture without elevation | Actual harmless Python child, stdout/stderr, nonzero exit and timeout; log path includes spaces/apostrophe/dollar sign | Both streams captured; exit 7 preserved; timeout exited 124 and reported unconfirmed |

The installed optional packages were matplotlib 3.10.7, Pillow 12.0.0,
pystray 0.19.5 and plyer 2.1.0. Development tools were pytest 8.4.2,
Ruff 0.14.2 and PyInstaller 6.16.0. The PyInstaller output is in ignored
`dist/`; its build log is in ignored `build/package.log`.

One earlier integrated pytest run skipped a real-Tk test; the subsequent
`-rs` run above passed all tests. Headless environments intentionally skip
tests that require a real display. Desktop notification delivery and actual
tray-menu interaction were not manually tested; callback behavior is covered
by regressions. The packaged executable was built but not interactively run.

Independent review found no remaining blockers after the follow-up fixes.
The reviewer reran eight focused regressions successfully. The review led to
retaining the Always on Top variable, handling an empty GPU list, preserving
query and elevated-command diagnostics, and disabling Apply on an unconfirmed
outcome. PowerShell itself is resolved from the Windows system directory;
read-only monitoring retains the executable-on-PATH convention. The subsequent
[PR review follow-up](review-followup.md) restricts Windows power-changing
commands to the system/NVIDIA installation directories because the PowerShell
UAC prompt obscures the child executable's identity.

GitHub Actions results are linked from the implementation PR. Its first run
passed both Windows test jobs and exposed a mocked Windows-path assumption in
one Linux test; the fixture was corrected to use a host-absolute mock path.
Headless Linux skips the real-display tests. Local workflow execution and
actionlint were not available. Test logs and the live smoke script are retained
locally under ignored `build/`. The external Snyk check reported account test
quota exhaustion; it did not produce a security-analysis result.
