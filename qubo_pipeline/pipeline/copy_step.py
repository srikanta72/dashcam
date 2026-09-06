"""
pipeline/copy_step.py

Copies time-window-filtered clips from the source (SD card or Termux
storage path) into <target>/cabin, front, rear. This replaces the batch
version's HHMM parsing (and its leading-zero/octal trap) with plain
Python string slicing and int() - no equivalent bug class exists here.

The camera clips are named like ``L1619_<start>_<end>.mp4``. Blank or
``NONE`` time bounds mean ALL, matching the Windows batch pipeline.
"""

from pathlib import Path
from datetime import date, datetime, timedelta


def _time_window(start, end):
    """Return an inclusive HHMM window, or None for ALL files."""
    if start is None or end is None:
        return None

    start_text = str(start).strip()
    end_text = str(end).strip()
    if not start_text or start_text.upper() == "NONE":
        return None
    if not end_text or end_text.upper() == "NONE":
        return None

    try:
        start_num = int(start_text)
        end_num = int(end_text)
    except ValueError:
        return None

    if not (0 <= start_num <= 2359 and 0 <= end_num <= 2359):
        return None
    return start_num, end_num


def _parse_date(value):
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid date '{value}'. Use YYYY-MM-DD.")


def _parse_boundary(value, default_date):
    """Return (date, HHMM), where HHMM is None when the boundary is open."""
    text = str(value or "ALL").strip()
    if not text or text.upper() == "ALL" or text.upper() == "NONE":
        return default_date, None

    normalized = text.replace("T", " ").replace(":", " ")
    parts = normalized.split()
    if len(parts) == 1 and len(parts[0]) in (4, 12) and parts[0].isdigit():
        if len(parts[0]) == 12:
            return _parse_date(parts[0][:8]), int(parts[0][8:])
        clock = int(parts[0])
        if 0 <= clock <= 2359:
            return default_date, clock
    if len(parts) == 2:
        try:
            boundary_date = _parse_date(parts[0])
            clock = int(parts[1])
            if 0 <= clock <= 2359:
                return boundary_date, clock
        except ValueError:
            pass
    raise ValueError(
        f"Invalid date-time '{value}'. Use HHMM or YYYY-MM-DD HHMM."
    )


def _resolve_date_time_range(start, end, source_path):
    source_date = _parse_date(source_path.parent.name)
    start_date, start_time = _parse_boundary(start, source_date)
    end_date, end_time = _parse_boundary(end, source_date)
    end_text = str(end or "ALL").strip().upper()
    if not end_text or end_text in ("ALL", "NONE"):
        end_date = start_date
    elif len(end_text) == 4 and end_text.isdigit():
        end_date = start_date
    if start_time is None and end_time is None:
        start_date = end_date = source_date
    if end_date < start_date:
        raise ValueError("End date cannot be earlier than start date")
    return start_date, end_date, start_time, end_time


def _date_folders(src, date_start, date_end):
    """Return (date, Video folder) pairs for the inclusive requested range."""
    start = _parse_date(date_start)
    end = _parse_date(date_end or date_start)
    if end < start:
        raise ValueError("End date cannot be earlier than start date")

    src_path = Path(src)
    if src_path.name.lower() == "video":
        serial_root = src_path.parent.parent
    else:
        serial_root = src_path
    folders = []
    current = start
    while current <= end:
        date_name = current.strftime("%d-%b-%Y")
        candidate = serial_root / date_name / "Video"
        if candidate.exists():
            folders.append((current, candidate))
        current += timedelta(days=1)
    if not folders:
        raise FileNotFoundError(
            f"No QUBO Video folders found between {start.isoformat()} and {end.isoformat()}"
        )
    return folders


def _selected_for_date(item, folder_date, date_start, date_end, window):
    start_date = _parse_date(date_start)
    end_date = _parse_date(date_end or date_start)
    if folder_date < start_date or folder_date > end_date:
        return False
    if window is None:
        return True
    try:
        time_part = item.stem.split("_", 1)[0]
        clip_time = int(time_part[1:])
    except (IndexError, ValueError):
        return False
    if folder_date == start_date and window[0] is not None and clip_time < window[0]:
        return False
    if folder_date == end_date and window[1] is not None and clip_time > window[1]:
        return False
    return True


def _is_selected_video(item, window):
    """Match the batch pipeline: .mp4 only, optionally filtered by HHMM."""
    if not item.is_file() or item.name.startswith(".") or item.suffix.lower() != ".mp4":
        return False
    if window is None:
        return True

    try:
        time_part = item.stem.split("_", 1)[0]
        clip_time = int(time_part[1:])
    except (IndexError, ValueError):
        return False
    return window[0] <= clip_time <= window[1]


def _remove_hidden_front_files(front_dir):
    """Remove stale dot-prefixed clips from a previous copy run."""
    removed = 0
    if not front_dir.exists() or not front_dir.is_dir():
        return removed
    for item in front_dir.iterdir():
        if item.is_file() and item.name.startswith("."):
            try:
                item.unlink()
                removed += 1
                print(f"[REMOVE] front/{item.name} (dot-prefixed stale file)")
            except OSError as exc:
                print(f"[WARN] Could not remove stale file {item}: {exc}")
    return removed


def _merged_outputs_ready(target):
    target = Path(target)
    return all(
        (target / f"{camera}_merged.mp4").is_file()
        and (target / f"{camera}_merged.mp4").stat().st_size > 0
        for camera in ("cabin", "front", "rear")
    )


def run(state):
    from pathlib import Path
    from pipeline import state as state_mod
    import datetime
    import shutil

    # Remove dot-prefixed files even when resuming a completed copy step.
    if state.get("steps", {}).get("copy", {}).get("completed"):
        target = state.get("target")
        if target:
            _remove_hidden_front_files(Path(target) / "front")
        print("[INFO] copy step already completed according to state.")
        return

    # Merged outputs are the durable proof that copying already happened.
    # Fragment folders may have been deleted after each successful merge.
    target = state.get("target")
    if target and _merged_outputs_ready(target):
        state.setdefault("steps", {}).setdefault("copy", {})["completed"] = True
        state_mod._save_state(Path(target), state)
        print("[INFO] All three merged videos exist; skipping copy step.")
        return

    # Determine source path: prefer state['src'], otherwise try to auto-detect
    src = state.get("src")
    src_path = Path(src) if src else None
    if not src_path or not src_path.exists() or not src_path.is_dir():
        raise FileNotFoundError(
            f"Source folder is not available: {src or '(not specified)'}\n"
            "The memory-card drive may be unplugged or already ejected. "
            "Reconnect it and run the same command again."
        )

    # Determine destination root: prefer state['dst'], otherwise use two levels up from current target
    dst_root = Path(state.get("dst")) if state.get("dst") else Path(state.get("target")).parents[1]
    if not dst_root:
        dst_root = Path.cwd()

    # Build destination folder name from source date folder (e.g. 06-Sep-2026 -> 2026-Sep-06-ALL)
    try:
        date_folder = src_path.parent.name  # expecting '06-Sep-2026'
        dd, mon, yyyy = date_folder.split("-")
        dest_name = f"{yyyy}-{mon}-{dd}-ALL"
    except Exception:
        # Fallback to timestamped name
        dest_name = datetime.datetime.utcnow().strftime("%Y-%b-%d-ALL")

    dest_path = Path(dst_root) / dest_name
    dest_path.mkdir(parents=True, exist_ok=True)
    _remove_hidden_front_files(dest_path / "front")

    date_start, date_end, start_time, end_time = _resolve_date_time_range(
        state.get("start"), state.get("end"), src_path
    )
    date_start_text = date_start.isoformat()
    date_end_text = date_end.isoformat()
    state["date_start"] = date_start_text
    state["date_end"] = date_end_text
    source_folders = _date_folders(src_path, date_start_text, date_end_text)
    cameras = ("cabin", "front", "rear")
    window = None if start_time is None and end_time is None else (start_time, end_time)
    copied_any = False

    for cam in cameras:
        dest_cam = dest_path / cam
        dest_cam.mkdir(parents=True, exist_ok=True)

        for folder_date, source_folder in source_folders:
            src_cam = source_folder / cam
            if src_cam.exists() and src_cam.is_dir():
                source_items = sorted(src_cam.iterdir())
            else:
                source_items = sorted(source_folder.iterdir()) if source_folder.exists() else []
            for item in source_items:
                if not _is_selected_video(item, None) or not _selected_for_date(
                    item, folder_date, date_start_text, date_end_text, window
                ):
                    continue
                dst_file = dest_cam / item.name
                try:
                    print(f"[COPY] {cam}/{item.name}")
                    # Skip copy if identical size already exists
                    if dst_file.exists() and dst_file.stat().st_size == item.stat().st_size:
                        print(f"[SKIP] {cam}/{item.name} already exists")
                        continue
                    shutil.copy2(item, dst_file)
                    copied_any = True
                    print(f"[OK]   {cam}/{item.name}")
                except Exception as e:
                    print(f"[WARN] Could not copy {item} -> {dst_file}: {e}")
            if not src_cam.exists() or not src_cam.is_dir():
                for item in source_items:
                    if not _is_selected_video(item, None) or cam not in item.name.lower():
                        continue
                    dst_file = dest_cam / item.name
                    try:
                        print(f"[COPY] {cam}/{item.name}")
                        if dst_file.exists() and dst_file.stat().st_size == item.stat().st_size:
                            print(f"[SKIP] {cam}/{item.name} already exists")
                            continue
                        shutil.copy2(item, dst_file)
                        copied_any = True
                        print(f"[OK]   {cam}/{item.name}")
                    except Exception as e:
                        print(f"[WARN] Could not copy {item} -> {dst_file}: {e}")

    if not copied_any:
        print(f"[WARN] No files copied from {src_path} into {dest_path}")

    # Update state to point to the real run target (the date-named folder)
    state["target"] = str(dest_path)
    state.setdefault("steps", {})
    state["steps"].setdefault("copy", {})
    state["steps"]["copy"]["completed"] = True
    state_mod._save_state(dest_path, state)

    print(f"[OK] Copy step complete. Source: {src_path} -> Target: {dest_path}")
