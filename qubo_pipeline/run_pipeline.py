#!/usr/bin/env python3
"""
run_pipeline.py

The single entry point for the whole QUBO dashcam -> YouTube pipeline.
Works identically on Windows and Android/Termux - only the thin
run_windows.bat / run_termux.sh launchers differ; all real logic lives
in the pipeline/ package below.

NORMAL USE - no arguments needed, nothing to remember:

    python run_pipeline.py

    Prompts interactively for time range / source / destination / upload
    choices - same questions, same order as the old batch script. Once
    the target folder is resolved, if pipeline_state.json already exists
    there, it's picked up automatically: a status report prints showing
    what's already done, and the pipeline continues from the first
    incomplete step. No flag, no separate "resume" command, nothing to
    remember - running the exact same command again after a crash just
    continues where it left off.

    Every "already done" check is verified against the actual file on
    disk (exists, non-empty) before being trusted - see
    pipeline/state.py:verify_step(). A state file claiming a step is
    done does not skip it if the expected output is actually missing.

ADVANCED / SCRIPTED USE - optional flags skip the matching prompt(s):

    python run_pipeline.py --target "I:\\Archive\\Qubo-trio\\2026-Aug-28-ALL"
    python run_pipeline.py --start 2057 --end 2158 --src "H:\\QUBO\\..."

    python run_pipeline.py copy   --src "..." --dst "..." --start 2057 --end 2158
    python run_pipeline.py merge  --target "..."
    python run_pipeline.py stack  --target "..."
    python run_pipeline.py upload --target "..." --which cabin|frontrear|main|all
    python run_pipeline.py validate --target "..."
        Read-only status report for a folder - what's done, what's not.
        Useful for a quick check without running anything.
"""

import argparse
import sys
import threading
from pathlib import Path

from pipeline import state as state_mod
from pipeline import copy_step
from pipeline import prompts
from pipeline import video_ops


def cmd_copy(args):
    st = state_mod.load_or_init(args.target, src=args.src, dst=args.dst,
                                 start=args.start, end=args.end)
    copy_step.run(st)


def cmd_merge(args):
    st = state_mod.load(args.target)
    video_ops.merge_all_cameras(st)


def cmd_stack(args):
    st = state_mod.load(args.target)
    video_ops.create_main_stack(st)
    video_ops.create_front_rear_stack(st)


def cmd_upload(args):
    st = state_mod.load(args.target)
    which = args.which
    if which in ("cabin", "all"):
        video_ops.upload_step(st, "cabin")
    if which in ("frontrear", "all"):
        video_ops.upload_step(st, "frontrear")
    if which in ("main", "all"):
        video_ops.upload_step(st, "main")


def cmd_validate(args):
    st = state_mod.load(args.target)
    state_mod.print_status_report(st)


def cmd_run(args):
    """
    The default, no-subcommand behavior. Resolves target (prompting
    interactively for anything not passed as a flag), loads or creates
    state for that folder, prints what's already done vs pending, then
    runs every remaining step in order. This is the ONLY command a user
    needs to remember for normal operation, including resuming after a
    crash - see module docstring above.
    """
    print("\nQUBO pipeline inputs (press Enter to use the default):")
    src = prompts.ask_source(args.src)
    dst = prompts.ask_destination(args.dst)
    start, end = prompts.ask_time_range(args.start, args.end)

    target = args.target or str(Path(dst) / prompts.default_video_title(src))
    st = state_mod.load_or_init(target, src=src, dst=dst,
                                 start=start, end=end)
    title_default = args.title or st.get("youtube_title") or prompts.default_video_title(
        st.get("src"), st.get("target")
    )
    st["youtube_title"] = prompts.ask_title(title_default)
    state_mod._save_state(Path(st["target"]), st)

    print("\nCurrent progress for this folder:")
    state_mod.print_status_report(st)
    print()

    cabin_upload_error = []
    frontrear_upload_error = []

    def upload_cabin_in_background():
        try:
            video_ops.upload_step(st, "cabin")
        except Exception as exc:
            cabin_upload_error.append(exc)

    def upload_frontrear_in_background():
        try:
            video_ops.upload_step(st, "frontrear")
        except Exception as exc:
            frontrear_upload_error.append(exc)

    try:
        copy_step.run(st)
        video_ops.merge_all_cameras(st, selected={"cabin"})
        cabin_upload = threading.Thread(
            target=upload_cabin_in_background,
            name="cabin-youtube-upload",
            daemon=False,
        )
        print("[INFO] Cabin merge is ready; starting YouTube upload in background.")
        cabin_upload.start()

        video_ops.merge_all_cameras(st, selected={"front", "rear"})
        video_ops.create_front_rear_stack(st)
        cabin_upload.join()
        if cabin_upload_error:
            raise cabin_upload_error[0]
        print("[INFO] Front+rear stack is ready; starting YouTube upload in background.")
        frontrear_upload = threading.Thread(
            target=upload_frontrear_in_background,
            name="frontrear-youtube-upload",
            daemon=False,
        )
        frontrear_upload.start()
        video_ops.create_main_stack(st)
        frontrear_upload.join()
        if frontrear_upload_error:
            raise frontrear_upload_error[0]
        video_ops.upload_step(st, "main")
    except Exception as exc:
        state_mod.log_event(st, f"RUN_FAILED error={exc}")
        raise

    print("\n[OK] Pipeline complete. Overview:")
    for step_name in (
        "copy", "merge_cabin", "upload_cabin", "merge_front", "merge_rear",
        "stack_frontrear", "upload_frontrear", "stack_main", "upload_main",
    ):
        status = st.get("steps", {}).get(step_name, {}).get("completed", False)
        print(f"  {'DONE' if status else 'PENDING'}  {step_name}")
    state_mod.print_status_report(st)


def build_parser():
    p = argparse.ArgumentParser(description="QUBO dashcam -> YouTube pipeline")

    # Top-level optional args: available with NO subcommand, for the
    # default smart-run behavior. Anything omitted here is prompted for
    # interactively instead of erroring out.
    p.add_argument("--target", help="Target output folder (omit to resolve from src/start/end, prompting as needed)")
    p.add_argument("--src", help="Source folder (omit to auto-detect QUBO card, or prompt)")
    p.add_argument("--dst", help="Destination root (omit to use config.py default, or prompt)")
    p.add_argument("--start", help="Start time, e.g. 2057, or blank for all files")
    p.add_argument("--end", help="End time, e.g. 2158")
    p.add_argument("--title", help="YouTube title for cabin and final videos")
    p.set_defaults(func=cmd_run)

    sub = p.add_subparsers(dest="command")

    def add_target_arg(sp):
        sp.add_argument("--target", required=True, help="Target output folder for this run")

    sp = sub.add_parser("copy", help="Copy time-filtered clips from the SD card")
    add_target_arg(sp)
    sp.add_argument("--src", help="Source folder (omit to auto-detect QUBO card)")
    sp.add_argument("--dst", help="Destination root (defaults to config.py)")
    sp.add_argument("--start", help="Start time, e.g. 2057, or blank for all files")
    sp.add_argument("--end", help="End time, e.g. 2158")
    sp.set_defaults(func=cmd_copy)

    sp = sub.add_parser("merge", help="Merge each camera's clips into one file")
    add_target_arg(sp)
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser("stack", help="Create the 3-camera and front+rear stacked outputs")
    add_target_arg(sp)
    sp.set_defaults(func=cmd_stack)

    sp = sub.add_parser("upload", help="Upload one or more outputs to YouTube")
    add_target_arg(sp)
    sp.add_argument("--which", choices=["cabin", "frontrear", "main", "all"], default="all")
    sp.set_defaults(func=cmd_upload)

    sp = sub.add_parser("validate", help="Report what's actually done in a target folder (read-only)")
    add_target_arg(sp)
    sp.set_defaults(func=cmd_validate)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\n[STOPPED] Cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
