"""
pipeline/cleanup.py

Permanent, no-confirmation deletion (matching your requirement: never a
Yes/No prompt), with every attempt recorded in state before and after,
so state.json is always the source of truth for "was this deleted on
purpose, or is it just missing." This directly carries over the
DELETE_FILE / DELETE_FOLDER design from the batch version.

Critically: this module has NO special-case knowledge of which files
are "safe" to delete (e.g. cabin_merged.mp4 needing to survive for the
main stack) - that decision belongs to the CALLER in video_ops.py,
explicitly, at the point where it knows what still depends on the file.
Keeping that logic out of a generic delete function is what the batch
bug actually violated.

TODO (not yet implemented):
- delete_file(state, path, reason) -> bool
- delete_folder(state, path, reason) -> bool
"""


def delete_file(state, path, reason):
    raise NotImplementedError("cleanup.delete_file: to be implemented")


def delete_folder(state, path, reason):
    raise NotImplementedError("cleanup.delete_folder: to be implemented")
