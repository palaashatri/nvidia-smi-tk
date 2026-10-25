# Complete AGENTS.md Implementation Plan

> Agent workers: use subagent-driven development, with Gemini first when available and Luna Max as fallback. Independently review and verify integrated changes.

**Goal:** Make the existing monitor satisfy AGENTS.md reliably, retaining its modular Tkinter architecture and optional extras.

**Architecture:** Keep pure parsing and CLI operations in nvidia_smi.py, persistence in config.py, and presentation in ui/. Transfer worker results as complete snapshots; consume events and update widgets exclusively on the Tk thread.

**Tech stack:** Python 3.10+, Tkinter, unittest/pytest; optional matplotlib, Pillow, pystray and plyer.

**Spec:** AGENTS.md.

## Constraints and risks

- No nvidia-smi calls on Tk's main thread; no Tk calls from workers or tray callbacks.
- Use subprocess argument lists, finite numeric input and confirmed device limits. Never report an elevation request as completed success.
- Preserve stdlib-only startup, unavailable readings, driver diagnostics, multi-GPU identity, history, exports and settings.
- Test Windows natively in an isolated venv because this is a Windows desktop/GPU application. Docker daemon is unavailable. Do not change real power limits or terminate real user processes during QA.
- Existing unittest suite currently aggregates all assertions into one test. Add independently discoverable regressions.
- GitHub CLI is unauthenticated; attempt connected GitHub tooling for PR creation after local verification.

## Tasks

- [x] Backend: audit and fix finite numeric parsing, CSV names/UUID association, malformed/error input, selected-device power parsing, validated power commands and Windows elevation completion/error handling. Add mocked command and parser regressions in tests/test_backend_regressions.py.
- [x] Config: validate persisted field types/ranges, tolerate invalid files, preserve signed window positions and write safely; add config regressions.
- [x] UI: repair snapshot publication/error recovery/sample deduplication and unavailable history values; queue tray events; stabilize process sorting/selection; fix graph callback lifecycle and optional import failures. Add focused tests using fakes and a real Tk smoke test where appropriate.
- [x] Power dialog: bind requests to the selected GPU and dialog instance; disable Apply until valid limits arrive; reject stale completions, non-finite values and duplicate submissions; keep worker errors visible. Add regression coverage.
- [x] CI/docs: run syntax and unit tests on Windows and Linux before packaging/releasing; correct runtime/optional-feature claims and document limits and actual evidence.
- [x] Independently review changes, run unittest, pytest, syntax and lint checks, exercise stdlib-only Tk startup and read-only live GPU queries, verify optional extras where available, then commit/push/open PR if authentication permits.

## Review focus

Invalid config types; stale or failed GPU queries; changed/non-contiguous GPU indices; closing/reopening dialogs while workers finish; elevated commands that fail after UAC.

## Initial evidence

- Baseline unittest command: one aggregate test passed. pytest unavailable in bundled interpreter before venv setup.
- Read-only nvidia-smi identity query: NVIDIA GeForce RTX 3080, driver 621.01 on Windows.
- Audited defects include tray-thread Tk calls, stale power limits and unconfirmed elevation success. Audit findings and fixes must be reverified after integration.

