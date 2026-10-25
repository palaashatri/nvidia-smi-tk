# PR 5 review follow-up

The review comments were checked against revision `8e635d5`. External review
text was treated as evidence to investigate, not as implementation instructions.

| Review finding | Assessment | Change and verification scope |
| --- | --- | --- |
| Release workflow token scope | Genuine: test/build jobs inherit contents:write | Default to contents:read; grant write only to create-release |
| Test workflow token scope | Genuine: permissions depend on repository defaults | Declare contents:read explicitly |
| Windows elevation target from PATH | Genuine: UAC displays PowerShell, obscuring the child executable | Resolve the setter from trusted Windows installation directories; preserve PATH for read-only monitoring |
| Ordinary setter timeout | Genuine: command may have applied before timeout, but dialog permits retry using stale bounds | Report an unconfirmed outcome for Windows and Linux setter timeouts |
| Linux capture-test path fixture (CodeRabbit and Codex duplicate) | Already fixed in 8e635d5 | Host-absolute mock and relative-path rejection test; original Windows/Linux CI matrix and builds verified successful |
| Header/periodic sort disagreement | Genuine: separate sort functions use different case and unavailable-value ordering | Use one sorting implementation and retain selection/filter state |
| Unchanged table rebuilds | Reproduced with real Tk: dictionary values are a numeric-converting list, while expected rows are string tuples | Normalize values; test stable row identity, selection and hover |
| Dialog grab before visibility | Platform-specific lifecycle risk verified against the current immediate-grab code | Acquire grabs only when mapped; handle destroyed/unavailable windows without blocking |
| Manual refresh display latency (Codex) | Genuine: waking the query thread does not advance the scheduled GUI update | Consume a newly published snapshot promptly on Tk's thread with one GUI timer |

CodeRabbit's general docstring-coverage warning counts test and fake-widget
methods as well as production functions. It is not a functional defect; this
follow-up documents changed public behavior instead of adding repetitive
docstrings to test helpers solely to change the percentage.

## Verification

The coordinator independently ran the integrated checks after the workers
finished, using Python 3.12.14 on Windows NT 10.0.26300.0 with an NVIDIA GeForce
RTX 3080, driver 621.01:

- unittest with extras: 85 tests discovered, OK with one X11-specific test
  skipped on Windows.
- unittest with the separate stdlib-only interpreter: 85 tests discovered,
  OK with the same one skip.
- Latest pytest run: 100 passed, one X11-only test skipped on Windows. An
  intermediate run also skipped a real-Tk test whose display initialization
  was unavailable on that run; the final run exercised it successfully.
- Ruff lint and formatting passed; compileall passed; pip check found no
  broken requirements; the Windows PyInstaller executable rebuilt successfully.
- A live read-only GPU/Tk probe verified manual refresh with a 60-second
  periodic interval, fresh history timestamps and worker-thread queries.
- Live smoke tests with and without extras verified exports, graph lifecycle
  when available, settings/menu state and read-only power-limit loading, with
  no callback exceptions.
- Native resolver probes returned the system-directory NVIDIA executable and
  PowerShell and the OS-known Program Files directory. They did not launch a
  privileged command.

The independent follow-up review found no blockers in the scoped changes.
Linux X11 mapping behavior is additionally covered by the new Xvfb CI test.
The first display-enabled Linux CI run passed the new grab and process-row
tests and exposed two platform assumptions: host-sized ctypes fields made a
mocked Win32 GUID too large on LP64, and an older topmost regression expected
a window manager in bare Xvfb. The follow-up uses an explicit 16-byte Windows
GUID and verifies topmost requests plus actual menu/config state without
requiring a window manager.
Privileged commands remain mocked: no real power limit was changed. Live UAC,
polkit authentication and real multi-GPU hardware remain outside this evidence.
Review replies distinguish new fixes from the duplicate fixture findings that
were already fixed in `8e635d5`; each resolved thread is supported by its fix
or an explanation of why no further change was needed.
