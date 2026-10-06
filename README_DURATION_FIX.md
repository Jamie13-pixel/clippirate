# Clip Pirate.ai — Duration Pipeline Fix

Updated pipeline:
- `script_generator.py`: duration-aware word targets for 30/45/60 seconds.
- `tts.py`: selected voice profile plus target-duration trim/pad and caption timeline clipping.
- `video_builder.py`: target duration is authoritative, selected aspect ratio is rendered, captions can be enabled/disabled, and final MP4 duration is verified after encoding.
- `main.py`: passes duration/aspect ratio/voice/captions through the full pipeline.

Duration targets:
- 30s: 65–80 spoken words
- 45s: 95–115 spoken words
- 60s: 125–150 spoken words

The renderer accepts only 30, 45, or 60 seconds and raises an error if the encoded MP4 differs from the target by more than 0.15 seconds.

Syntax check: passed with Python `py_compile`.
Runtime generation test was not run because the current execution environment does not have the project's `groq` dependency installed.
