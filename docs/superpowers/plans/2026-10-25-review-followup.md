# PR 5 Review Follow-up Plan

**Goal:** Verify all inline review findings, implement genuine remaining defects, and reply to/resolve the corresponding PR threads after verification.

**Baseline:** 8e635d5. Gemini/Antigravity CLI remains unavailable on PATH; scoped Luna Max workers implement the fixes. Keep the existing branch/PR and standard-library core.

## Findings and ownership

- Backend (`nvidia_smi.py`, backend regressions): verify Windows executable elevation risk, resolve power-changing targets from Windows system/NVIDIA installation directories without trusting PATH or environment overrides, fail closed if unavailable, and mark ordinary setter timeouts unconfirmed on both platforms. Preserve read-only query compatibility and captured diagnostics. Mock privileged operations; no real power writes.
- App (`ui/app.py`, UI regressions): reproduce differing header/periodic sort order and Tk list/numeric value conversions; normalize rows and share sorting. Fix the additional Codex manual-refresh latency finding with prompt main-thread snapshot consumption and one scheduled GUI loop.
- Dialogs/CI (`ui/dialogs.py`, dialog regressions, workflows): verify early-grab behavior and defer grabs until mapped without an unbounded visibility wait; handle close/failure. Limit workflow tokens to contents:read and release-write only for create-release. Exercise Tk tests under Xvfb on Linux CI if required to verify the X11 lifecycle.
- Previously fixed (two duplicate threads): the Windows-style mocked path was replaced by a host-absolute fixture in 8e635d5, and both Linux CI jobs subsequently passed. Do not apply the proposed production-code workaround blindly.

## Verification and delivery

Each worker first reproduces its findings with focused regression tests, then implements and verifies its owned files. Main reviews the integrated diff and independently runs unittest with/without extras, pytest, Ruff, compileall, live read-only GPU/Tk smoke and packaging. Refresh docs that describe Windows setter resolution. Push the fixes to PR 5; reply with verified reasons/evidence and resolve addressed threads (including the duplicate already-fixed fixture findings). Confirm CI on the new revision. Keep Snyk account-quota failure distinct from application checks.

## Risks

Trusted Windows executable locations may reject custom copied CLI installations for power changes; explain that limitation. Scheduled UI work must remain on Tk's thread and cancel on shutdown. Mapping callbacks must not hang hidden-window tests or touch destroyed widgets. CI permissions must still allow release publication.
