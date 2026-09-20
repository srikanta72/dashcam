# QUBO Dashcam Pipeline

This repository copies QUBO dashcam clips, merges the camera recordings, creates stacked videos, and optionally uploads the results to YouTube.

The normal pipeline produces:

- `cabin_merged.mp4`
- `front_merged.mp4`
- `rear_merged.mp4`
- `front_rear_output.mp4`
- `stacked_output.mp4`

The pipeline is resumable. It stores progress in `pipeline_state.json` inside the target folder and writes diagnostic events to `pipeline.log`.

## Requirements

- Python 3.10 or newer is recommended. Check it with:

  ```text
  py --version       # Windows
  python --version   # other systems
  ```

- FFmpeg, including `ffmpeg` and `ffprobe`, available on `PATH`.
- A source folder containing the QUBO video folders and camera clips.
- A writable destination folder.
- For YouTube uploads: a Google Cloud OAuth desktop client file named `client_secret.json`.

The code uses standard Python modules plus the packages listed in `requirements.txt`. Windows and Android/Termux are supported by the included launchers.

## Installation

### Windows

1. Install Python from [python.org](https://www.python.org/downloads/). During setup, enable the option to add Python to `PATH`.
2. Install FFmpeg and confirm both commands work:

   ```powershell
   ffmpeg -version
   ffprobe -version
   ```

3. Open PowerShell in this `qubo_pipeline` directory.
4. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   py -m pip install --upgrade pip
   py -m pip install -r requirements.txt
   ```

5. If PowerShell blocks activation, run the pipeline with the virtual-environment interpreter directly:

   ```powershell
   .\.venv\Scripts\python.exe run_pipeline.py
   ```

### Android / Termux

```bash
pkg update
pkg install python ffmpeg
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
bash run_termux.sh
```

## Google / YouTube setup

Uploads are optional. Copy, merge, stack, and validation do not require Google credentials.

1. In Google Cloud Console, create or select a project.
2. Enable **YouTube Data API v3**.
3. Configure the OAuth consent screen.
4. Create an OAuth client of type **Desktop app**.
5. Download the client JSON and save it as:

   ```text
   qubo_pipeline/credentials/client_secret.json
   ```

6. Run the pipeline. A browser opens on the first upload for consent. The resulting `token.json` is saved in the same directory.

Never distribute `client_secret.json` or `token.json`. They are credentials. Remove them before sharing or committing the repository, and give each user their own OAuth client/token where appropriate.

## Running the pipeline

### Normal run

From the `qubo_pipeline` directory:

```powershell
py run_pipeline.py
```

or on Termux:

```bash
python3 run_pipeline.py
```

The prompts ask for source, destination, time range, and YouTube title. Press Enter to accept the displayed default. The same command can be run again after an interruption; completed work is skipped when its output is available.

### Windows launcher

```powershell
.\run_windows.bat
```

### Useful commands

```powershell
py run_pipeline.py --target "I:\Archive\Qubo-trio\2026-Sep-06-ALL"
py run_pipeline.py copy --target "I:\Archive\Qubo-trio\2026-Sep-06-ALL" --src "H:\QUBO\B89CB8\06-Sep-2026\Video"
py run_pipeline.py merge --target "I:\Archive\Qubo-trio\2026-Sep-06-ALL"
py run_pipeline.py stack --target "I:\Archive\Qubo-trio\2026-Sep-06-ALL"
py run_pipeline.py upload --target "I:\Archive\Qubo-trio\2026-Sep-06-ALL" --which all
py run_pipeline.py validate --target "I:\Archive\Qubo-trio\2026-Sep-06-ALL"
```

Time boundaries accept `ALL`, `HHMM`, `YYYY-MM-DD HHMM`, or `YYYYMMDDHHMM`. For a cross-day range, include dates on both boundaries.

## What runs in parallel?

The pipeline respects output dependencies:

1. Copy runs first.
2. Cabin merging runs next. After it finishes, the cabin YouTube upload starts in a background thread.
3. While that upload runs, front merging, rear merging, and front/rear stacking run sequentially.
4. After the cabin upload is confirmed, the front/rear upload starts in the background.
5. While that upload runs, the main three-camera stack is created.
6. The main upload starts after the front/rear upload and main stack are ready.

Therefore the current overlap is:

- Cabin upload with front merge, rear merge, and front/rear stack.
- Front/rear upload with main stack.

The three camera merges are not currently parallel with each other. They could be parallelized in a future change, but that would increase disk I/O and CPU contention while FFmpeg is running, so it should be benchmarked on the target hardware first. The final timing summary prints every step's start time, end time, duration, total wall-clock time, and video-seconds-per-wall-second throughput.

## YouTube customization

Edit the settings near the top of `pipeline/youtube_upload.py`:

```python
DEFAULT_PLAYLIST_NAMES = ("Dashcam", "Family Archive")
DEFAULT_PRIVACY = "unlisted"
DEFAULT_PLAYLIST_PRIVACY = "private"
DEFAULT_MADE_FOR_KIDS = False
```

### Playlists

- Use one playlist: `("Dashcam",)`.
- Use multiple playlists: `("Dashcam", "Family Archive", "Trip 2026")`.
- A playlist is created automatically if that exact name does not already exist.
- Every uploaded video is added to every configured playlist.
- `DEFAULT_PLAYLIST_PRIVACY` controls the privacy of playlists created by this pipeline. Existing playlists keep their existing YouTube privacy setting.

Accepted privacy values are `private`, `unlisted`, and `public`.

### Video privacy and made-for-kids setting

- `DEFAULT_PRIVACY` controls uploaded video visibility: `private`, `unlisted`, or `public`.
- `DEFAULT_MADE_FOR_KIDS = False` marks uploads as not made for kids.
- Set it to `True` only when the content is genuinely directed at children and complies with YouTube's applicable requirements. This is a legal/content classification decision, not merely a display preference.

The standalone uploader also accepts these values directly:

```powershell
py pipeline\youtube_upload.py "path\to\video.mp4" "Video title" "Dashcam" "unlisted"
```

The standalone command accepts one playlist name. Multiple playlists are supported by the normal pipeline setting above.

## Output, logs, and troubleshooting

Each target folder can contain:

- `pipeline_state.json`: resumable step state.
- `pipeline.log`: durable event log.
- Intermediate camera folders and generated MP4 files.

Common checks:

```powershell
py run_pipeline.py validate --target "path\to\target"
ffprobe -v error "path\to\video.mp4"
```

If a command is not found, verify Python/FFmpeg are installed and on `PATH`. If upload authentication fails, verify `client_secret.json`, the enabled YouTube Data API, OAuth consent configuration, and browser access. Do not paste tokens or client secrets into an issue.

## Reporting an issue

Please include:

1. Operating system and Python version (`py --version` or `python3 --version`).
2. FFmpeg version (`ffmpeg -version`).
3. The exact command used, with personal paths and credentials removed.
4. The relevant terminal error.
5. The relevant `pipeline.log` entries and the status output from `validate`.
6. Which step failed: copy, merge, stack, upload, or authentication.
7. Whether the failure is repeatable and whether rerunning resumes successfully.

Do not attach `client_secret.json`, `token.json`, OAuth URLs containing sensitive values, or private video files.

## Project layout

```text
qubo_pipeline/
  run_pipeline.py          Main entry point and orchestration
  run_windows.bat          Windows launcher
  run_termux.sh            Termux launcher
  requirements.txt         Python dependencies
  credentials/             Local OAuth files; do not distribute secrets
  pipeline/
    copy_step.py           Source selection and copying
    video_ops.py           Merge, stack, upload orchestration
    youtube_upload.py      YouTube API integration and settings
    state.py               Resume state and event logging
    prompts.py             Interactive input prompts
```

## License and distribution

Add the project's license and any organization-specific usage terms before distributing this repository publicly. Remove local credentials and generated output folders from the distribution package.
