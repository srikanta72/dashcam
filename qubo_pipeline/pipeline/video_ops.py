"""Video merge, stack, split, upload, and resume operations."""

from pathlib import Path
import subprocess
import threading

from pipeline import state as state_mod


# Change this value when a different maximum output length is required.
MAX_OUTPUT_HOURS = 4
_STATE_LOCK = threading.RLock()


def _save(state):
    with _STATE_LOCK:
        state_mod._save_state(Path(state["target"]), state)


def _log(state, message):
    with _STATE_LOCK:
        state_mod.log_event(state, message)
        print(f"[LOG] {message}")


def _step_done(state, name, output_names):
    if not state.get("steps", {}).get(name, {}).get("completed"):
        return False
    target = Path(state["target"])
    return all((target / output).exists() and (target / output).stat().st_size > 0
               for output in output_names)


def _mark_step(state, name, **fields):
    with _STATE_LOCK:
        state.setdefault("steps", {}).setdefault(name, {}).update(fields)
        state["steps"][name]["completed"] = True
        _save(state)


def _duration_seconds(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True)
    return float(result.stdout.strip())


def _validate_video(path):
    """Decode a video without producing output; fail on broken HEVC packets."""
    try:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-xerror", "-i", str(path),
             "-map", "0:v:0", "-f", "null", "-"],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or "").strip().splitlines()
        detail = details[-1] if details else "invalid or truncated video stream"
        raise ValueError(f"{path.name}: {detail}") from exc


def _quick_validate_video(path):
    """Check container metadata and confirm that a video stream is present."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            check=True, capture_output=True, text=True,
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or "").strip().splitlines()
        detail = details[-1] if details else "unreadable video metadata"
        raise ValueError(f"{path.name}: {detail}") from exc
    if result.stdout.strip() != "video":
        raise ValueError(f"{path.name}: no video stream found")


def _find_invalid_fragment(files):
    for fragment in files:
        try:
            _validate_video(fragment)
        except ValueError as exc:
            return exc
    return None


def _split_if_needed(state, source, base_name):
    source = Path(source)
    max_seconds = MAX_OUTPUT_HOURS * 3600
    duration = _duration_seconds(source)
    if duration <= max_seconds:
        return [source]

    target = source.parent
    parts = sorted(target.glob(f"{base_name}_part*.mp4"))
    if not parts:
        _log(state, f"SPLIT_START source={source.name} duration={duration:.0f}s max={max_seconds}s")
        subprocess.run([
            "ffmpeg", "-y", "-i", str(source), "-map", "0", "-c", "copy",
            "-f", "segment", "-segment_time", str(max_seconds),
            "-reset_timestamps", "1", str(target / f"{base_name}_part%03d.mp4")
        ], check=True)
        parts = sorted(target.glob(f"{base_name}_part*.mp4"))

    if not parts or any(part.stat().st_size == 0 for part in parts):
        raise RuntimeError(f"Splitting {source.name} did not produce valid parts")
    _log(state, f"SPLIT_DONE source={source.name} parts={len(parts)}")
    return parts


def _stack(state, name, inputs, output, include_audio=False):
    if _step_done(state, name, [output]):
        print(f"[INFO] {name} already completed and output is valid.")
        return Path(state["target"]) / output

    target = Path(state["target"])
    missing = [input_file for input_file in inputs
               if not (target / input_file).is_file()
               or (target / input_file).stat().st_size == 0]
    if missing:
        message = f"Cannot create {output}; missing merged input(s): {', '.join(missing)}"
        _log(state, f"STACK_FAILED name={name} error={message}")
        raise FileNotFoundError(message)
    filters = []
    for index in range(len(inputs)):
        filters.append(
            f"[{index}:v]scale=1080:640:force_original_aspect_ratio=decrease,"
            f"pad=1080:640:(ow-iw)/2:(oh-ih)/2:black[v{index}]"
        )
    labels = "".join(f"[v{index}]" for index in range(len(inputs)))
    filters.append(f"{labels}vstack=inputs={len(inputs)}[v]")
    command = ["ffmpeg", "-y"]
    for input_file in inputs:
        command.extend(["-i", str(target / input_file)])
    command.extend(["-filter_complex", ";".join(filters), "-map", "[v]"])
    if include_audio:
        command.extend(["-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k", "-shortest"])
    else:
        command.append("-an")
    command.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
                    "-pix_fmt", "yuv420p", str(target / output)])
    _log(state, f"STACK_START name={name} output={output}")
    subprocess.run(command, check=True)
    if not (target / output).exists() or (target / output).stat().st_size == 0:
        raise RuntimeError(f"ffmpeg did not create {output}")
    _mark_step(state, name, output=output)
    _log(state, f"STACK_DONE name={name} output={output}")
    return target / output


def _upload_file(state, file_path, title, step_name):
    from pipeline import youtube_upload

    with _STATE_LOCK:
        uploaded = state.setdefault("steps", {}).setdefault(step_name, {}).setdefault("uploaded", {})
        already_uploaded = file_path.name in uploaded
    key = file_path.name
    if already_uploaded:
        print(f"[INFO] Upload already recorded for {key}; skipping.")
        return
    youtube = youtube_upload.get_authenticated_service()
    video_id = youtube_upload.upload_video(
        youtube, str(file_path), title, youtube_upload.DEFAULT_PRIVACY
    )
    for playlist_name in youtube_upload.DEFAULT_PLAYLIST_NAMES:
        playlist_id = youtube_upload.get_or_create_playlist(youtube, playlist_name)
        youtube_upload.add_video_to_playlist(
            youtube, playlist_id, video_id, playlist_name
        )
    with _STATE_LOCK:
        state.setdefault("steps", {}).setdefault(step_name, {}).setdefault("uploaded", {})[key] = video_id
        _save(state)
    _log(state, f"UPLOAD_DONE step={step_name} file={key} video_id={video_id}")


def _upload_parts(state, source, base_name, title, step_name):
    parts = _split_if_needed(state, source, base_name)
    for index, part in enumerate(parts, 1):
        part_title = title if len(parts) == 1 else f"{title} - Part {index} of {len(parts)}"
        print(f"[UPLOAD] {part.name}")
        _upload_file(state, part, part_title, step_name)
    _mark_step(state, step_name, parts=[part.name for part in parts])


def _delete_camera_fragments(state, camera):
    camera_dir = Path(state["target"]) / camera
    if not camera_dir.exists():
        return
    for fragment in sorted(camera_dir.glob("*.mp4")):
        try:
            fragment.unlink()
            _log(state, f"DELETE_{camera.upper()}_FRAGMENT file={fragment.name}")
            print(f"[DELETE] {camera}/{fragment.name}")
        except OSError as exc:
            _log(state, f"DELETE_{camera.upper()}_FRAGMENT_FAILED file={fragment.name} error={exc}")
            raise


def merge_all_cameras(state, selected=None):
    target = Path(state["target"])
    cameras = [("cabin", "merge_cabin"), ("front", "merge_front"), ("rear", "merge_rear")]
    if selected is not None:
        cameras = [camera for camera in cameras if camera[0] in selected]

    for folder_name, step_name in cameras:
        out_file = target / f"{folder_name}_merged.mp4"
        if _step_done(state, step_name, [out_file.name]):
            try:
                _validate_video(out_file)
            except ValueError as exc:
                message = f"Recorded merged output is invalid; fragments must be reviewed: {exc}"
                _log(state, f"MERGE_OUTPUT_INVALID camera={folder_name} error={exc}")
                raise RuntimeError(message) from exc
            print(f"[INFO] {step_name} already completed and output is valid.")
            _delete_camera_fragments(state, folder_name)
            continue

        # The output can exist even when the state write was interrupted.
        # Treat that valid output as completed instead of requiring fragments.
        if out_file.is_file() and out_file.stat().st_size > 0:
            try:
                _validate_video(out_file)
            except ValueError as exc:
                message = f"Existing merged output is invalid; fragments were retained: {exc}"
                _log(state, f"MERGE_OUTPUT_INVALID camera={folder_name} error={exc}")
                raise RuntimeError(message) from exc
            _mark_step(state, step_name, output=out_file.name)
            _log(state, f"MERGE_RECOVERED camera={folder_name} output={out_file.name}")
            print(f"[INFO] Existing merged file recovered: {out_file.name}")
            _delete_camera_fragments(state, folder_name)
            continue

        cam_dir = target / folder_name
        if not cam_dir.exists():
            message = f"Cannot merge {folder_name}; camera folder is missing: {cam_dir}"
            _log(state, f"MERGE_FAILED camera={folder_name} error={message}")
            raise FileNotFoundError(message)
        files = sorted(
            p for p in cam_dir.iterdir()
            if p.is_file() and not p.name.startswith(".") and p.suffix.lower() == ".mp4"
        )
        if not files:
            message = f"Cannot merge {folder_name}; no MP4 fragments found in {cam_dir}"
            _log(state, f"MERGE_FAILED camera={folder_name} error={message}")
            raise FileNotFoundError(message)

        _log(state, f"VALIDATE_START camera={folder_name} files={len(files)} mode=quick")
        print(f"[INFO] Quick validation in progress for {folder_name} files...")
        for fragment in files:
            try:
                _quick_validate_video(fragment)
            except ValueError as exc:
                message = f"Invalid camera fragment; retained for inspection: {exc}"
                _log(state, f"VALIDATE_FAILED camera={folder_name} file={fragment.name} error={exc}")
                raise RuntimeError(message) from exc
        _log(state, f"VALIDATE_DONE camera={folder_name} mode=quick")
        print(f"[OK] Quick validation completed for {folder_name}.")

        list_file = target / f"{folder_name}_concat.txt"
        try:
            with open(list_file, "w", encoding="utf-8") as concat_file:
                for item in files:
                    safe_path = item.as_posix().replace("'", "'\\''")
                    concat_file.write(f"file '{safe_path}'\n")
            _log(state, f"MERGE_START camera={folder_name} files={len(files)}")
            subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c", "copy", str(out_file)
            ], check=True)
            if not out_file.exists() or out_file.stat().st_size == 0:
                raise RuntimeError(f"ffmpeg did not create {out_file.name}")
            try:
                _validate_video(out_file)
            except ValueError as exc:
                fragment_error = _find_invalid_fragment(files)
                if fragment_error is not None:
                    exc = fragment_error
                _log(state, f"MERGE_OUTPUT_INVALID camera={folder_name} error={exc}")
                raise RuntimeError(f"Merged output is invalid; fragments were retained: {exc}") from exc
            _mark_step(state, step_name, output=out_file.name, source_count=len(files))
            _log(state, f"MERGE_DONE camera={folder_name} output={out_file.name}")
            print(f"[OK] Created {out_file}")
            _delete_camera_fragments(state, folder_name)
        except FileNotFoundError as exc:
            raise RuntimeError("ffmpeg/ffprobe not found on PATH") from exc
        except subprocess.CalledProcessError as exc:
            _log(state, f"MERGE_FAILED camera={folder_name} error={exc}")
            raise RuntimeError(f"ffmpeg failed while merging {folder_name}: {exc}") from exc
        finally:
            if list_file.exists():
                list_file.unlink()


def create_front_rear_stack(state):
    return _stack(
        state, "stack_frontrear", ["front_merged.mp4", "rear_merged.mp4"],
        "front_rear_output.mp4"
    )


def create_main_stack(state):
    return _stack(
        state, "stack_main", ["front_merged.mp4", "cabin_merged.mp4", "rear_merged.mp4"],
        "stacked_output.mp4", include_audio=True
    )


def upload_step(state, which):
    target = Path(state["target"])
    if which == "cabin":
        source = target / "cabin_merged.mp4"
        base_name = "cabin_merged"
        title = f"{state.get('youtube_title') or target.name}-cabin"
        step_name = "upload_cabin"
    elif which == "frontrear":
        source = target / "front_rear_output.mp4"
        base_name = "front_rear_output"
        title = f"{state.get('youtube_title') or target.name}-front-rear"
        step_name = "upload_frontrear"
    elif which == "main":
        source = target / "stacked_output.mp4"
        base_name = "stacked_output"
        title = state.get("youtube_title") or target.name
        step_name = "upload_main"
    else:
        raise ValueError(f"Unknown upload type: {which}")

    if not source.exists() or source.stat().st_size == 0:
        message = f"Upload source is missing or empty: {source}"
        _log(state, f"UPLOAD_FAILED step={step_name} error={message}")
        raise FileNotFoundError(message)

    _log(state, f"UPLOAD_START step={step_name} source={source.name}")
    try:
        _upload_parts(state, source, base_name, title, step_name)
    except Exception as exc:
        _log(state, f"UPLOAD_FAILED step={step_name} error={exc}")
        raise
