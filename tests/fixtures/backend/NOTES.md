# Backend calibration notes (DP-IDE-PROTOCOL WU-IDE-05)

Status: calibration not done (no `*.validate.json` fixtures; `test_backend_fixtures.py` skips).
To calibrate: start the organizer containers, create `app.yaml` / `broken.yaml` / `device.yaml` / `demo/app.yaml` in the IDE, then run `uv run python scripts/probe_backend.py --path app.yaml --path broken.yaml --path device.yaml --path demo/app.yaml`.
(a) Can a file be created at `x/y.yaml` when folder `x` does not exist yet? unknown (revisit in DP-RELEASE manual checklist).
(b) Does the Hyperion chat render Markdown (`**bold**`, lists, code fences)? unknown (fill in during DP-RELEASE).
Default `Settings.auto_create_parents` stays True until (a) is answered.
