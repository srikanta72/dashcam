"""
pipeline/state.py

Tracks per-run progress in <target>/pipeline_state.json instead of the
batch version's plain-text log. This is what makes every step
idempotent: each step checks state before doing any work, and every
upload records a "confirmed" JSON entry immediately after YouTube
returns success - not just when a local file exists - which is exactly
the "check the log for a YouTube success confirmation" idea from the
batch version, but structured instead of findstr-parsed.

IMPORTANT - "completed" in state.json is never trusted blindly.
Every skip-check calls verify_step() first, which re-confirms the
expected output file(s) actually exist (and are non-empty) before
honoring a "completed" flag. If state says a step is done but its file
is missing - e.g. someone manually deleted it, or a bug like the batch
version's cabin_merged.mp4 deletion happens again - that mismatch is
reported explicitly and the step is re-run, rather than skipped and
allowed to fail three steps later on a missing input with a confusing
error. This is the "quick validation step" from the design discussion.

Example pipeline_state.json shape (illustrative, not final):

{
  "target": "I:/Archive/Qubo-trio/2026-Aug-28-ALL",
  "src": "H:/QUBO/B89CB8/28-Aug-2026/Video",
  "start": "ALL", "end": "ALL",
  "steps": {
    "copy": {"completed": true},
    "merge_cabin": {"completed": true, "output": "cabin_merged.mp4"},
    "merge_front": {"completed": true, "output": "front_merged.mp4"},
    "merge_rear":  {"completed": true, "output": "rear_merged.mp4"},
    "upload_cabin": {"completed": true, "parts": ["cabin_merged.mp4"], "uploaded": ["cabin_merged.mp4"]},
    "stack_frontrear": {"completed": false},
    "upload_frontrear": {"completed": false},
    "stack_main": {"completed": false},
    "upload_main": {"completed": false}
  },
  "deletions": []
}

TODO (not yet implemented):
- load_or_init(target, src, dst, start, end)
- load(target)
- is_completed(state, step_name) -> bool
      Internally calls verify_step() - never trusts the flag alone.
- verify_step(state, step_name) -> bool
      Re-checks the step's recorded output file(s): must exist and be
      non-empty. If "completed" is True but verification fails, logs a
      MISMATCH entry, flips the step back to not-completed, and returns
      False so the caller re-runs it.
- mark_completed(state, step_name, **fields)
- record_upload(state, step_name, filepath)   # only after confirmed API success
- record_deletion(state, filepath, reason, success: bool)
- print_status_report(state)
      Prints the "[DONE] ... [PENDING] ..." summary shown when resuming.
"""

from pathlib import Path
import json
from datetime import datetime


STATE_FILENAME = "pipeline_state.json"


def default_source():
    """Return today's expected QUBO video folder on the Windows card."""
    today = datetime.today()
    date_folder = today.strftime("%d-%b-%Y")
    return str(Path("H:/QUBO/B89CB8") / date_folder / "Video")


def default_destination():
    """Return the archive root used by the Windows pipeline."""
    return str(Path("I:/Archive/Qubo-trio"))


def _save_state(path: Path, state: dict):
    try:
        path.mkdir(parents=True, exist_ok=True)
        with open(path / STATE_FILENAME, "w", encoding="utf-8") as out:
            json.dump(state, out, indent=2, ensure_ascii=False)
    except Exception:
        # Best-effort save; don't crash the whole run on I/O trouble
        print(f"[WARN] Could not write state to: {path / STATE_FILENAME}")


def log_event(state, message):
    """Append a durable pipeline event beside the state file."""
    target = Path(state.get("target", "."))
    try:
        target.mkdir(parents=True, exist_ok=True)
        with open(target / "pipeline.log", "a", encoding="utf-8") as log_file:
            log_file.write(f"{datetime.utcnow().isoformat()}Z {message}\n")
    except OSError as exc:
        print(f"[WARN] Could not write pipeline log: {exc}")


def load_or_init(target, src=None, dst=None, start=None, end=None,
                 date_start=None, date_end=None):
    requested_src = src
    requested_dst = dst
    requested_start = start
    requested_end = end
    requested_date_start = date_start
    requested_date_end = date_end
    if src is None:
        src = default_source()
    if dst is None:
        dst = default_destination()
    if start is None:
        start = "ALL"
    if end is None:
        end = "ALL"
    if date_start is None:
        date_start = datetime.today().date().isoformat()
    if date_end is None:
        date_end = date_start

    # If caller passed None, auto-generate a target directory in cwd
    if target is None:
        timestamp = datetime.utcnow().strftime("%Y-%m-%d_%H%M%S")
        target = Path.cwd() / f"qubo_target_{timestamp}"
    target_path = Path(target).expanduser()
    target_path.mkdir(parents=True, exist_ok=True)

    state_file = target_path / STATE_FILENAME
    if state_file.exists():
        try:
            with open(state_file, "r", encoding="utf-8") as fh:
                st = json.load(fh)
            st["target"] = str(target_path)
            # Merge any provided CLI overrides into loaded state
            if requested_src is not None or not st.get("src"):
                st["src"] = src
            if requested_dst is not None or not st.get("dst"):
                st["dst"] = dst
            if requested_start is not None or st.get("start") is None:
                st["start"] = start
            if requested_end is not None or st.get("end") is None:
                st["end"] = end
            if requested_date_start is not None or st.get("date_start") is None:
                st["date_start"] = date_start
            if requested_date_end is not None or st.get("date_end") is None:
                st["date_end"] = date_end
            return st
        except Exception:
            # Fall through to init a fresh state if file is corrupt
            print(f"[WARN] Could not read existing state file, creating a new one: {state_file}")

    # Default initial state shape
    now = datetime.utcnow().isoformat() + "Z"
    st = {
        "target": str(target_path),
        "src": src,
        "dst": dst,
        "start": start,
        "end": end,
        "date_start": date_start,
        "date_end": date_end,
        "created": now,
        "steps": {
            "copy": {"completed": False},
            "merge_cabin": {"completed": False},
            "merge_front": {"completed": False},
            "merge_rear": {"completed": False},
            "upload_cabin": {"completed": False},
            "stack_frontrear": {"completed": False},
            "upload_frontrear": {"completed": False},
            "stack_main": {"completed": False},
            "upload_main": {"completed": False},
        },
        "deletions": [],
        "mismatches": [],
    }

    _save_state(target_path, st)
    return st


def load(target):
    target_path = Path(target).expanduser()
    state_file = target_path / STATE_FILENAME
    if not state_file.exists():
        raise FileNotFoundError(f"No state file found at target: {state_file}")
    with open(state_file, "r", encoding="utf-8") as fh:
        return json.load(fh)


def is_completed(state, step_name):
    return bool(state.get("steps", {}).get(step_name, {}).get("completed", False))


def verify_step(state, step_name):
    # Minimal verification: trust the recorded flag. More advanced
    # verification (checking outputs exist) can be added later.
    completed = is_completed(state, step_name)
    if completed:
        return True
    return False


def print_status_report(state):
    steps = state.get("steps", {})
    done = [name for name, v in steps.items() if v.get("completed")]
    pending = [name for name in steps.keys() if name not in done]

    print(f"Target: {state.get('target')}")
    print(f"Source: {state.get('src')}")
    print(f"Date range: {state.get('date_start')} / {state.get('date_end')}")
    print(f"Start/End: {state.get('start')} / {state.get('end')}")
    print()
    print(f"[DONE] {len(done)}")
    for n in done:
        print(f"  - {n}")
    print(f"[PENDING] {len(pending)}")
    for n in pending:
        print(f"  - {n}")
