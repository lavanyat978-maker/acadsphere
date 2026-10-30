#!/usr/bin/env bash
# Convert the recorded WebM walkthroughs to MP4 with an embedded subtitle track,
# and join them into one full walkthrough. Needs ffmpeg.
set -euo pipefail
cd "$(dirname "$0")/output"

parts=()
for webm in [0-9]-*.webm; do
  name="${webm%.webm}"
  ffmpeg -loglevel error -y -i "$webm" -i "$name.srt" \
    -map 0:v -map 1:s -c:v libx264 -preset veryfast -crf 23 -pix_fmt yuv420p \
    -c:s mov_text -metadata:s:s:0 language=eng "$name.mp4"
  parts+=("$name")
  echo "built $name.mp4"
done

# One continuous video: concatenate the video and shift each subtitle file.
: > concat.txt
: > praxis-walkthrough.srt
offset=0
index=1
for name in "${parts[@]}"; do
  echo "file '$name.mp4'" >> concat.txt
  python3 - "$name.srt" "$offset" "$index" >> praxis-walkthrough.srt <<'PY'
import re, sys
path, offset, start = sys.argv[1], float(sys.argv[2]), int(sys.argv[3])
def shift(stamp):
    h, m, rest = stamp.split(":"); s, ms = rest.split(",")
    total = int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000 + offset
    return f"{int(total // 3600):02}:{int(total % 3600 // 60):02}:{int(total % 60):02},{int(round(total * 1000)) % 1000:03}"
for n, block in enumerate(filter(None, open(path).read().strip().split("\n\n"))):
    lines = block.split("\n")
    a, b = lines[1].split(" --> ")
    print(f"{start + n}\n{shift(a)} --> {shift(b)}\n" + "\n".join(lines[2:]) + "\n")
PY
  index=$(( index + $(grep -c -- '-->' "$name.srt") ))
  duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$name.mp4")
  offset=$(python3 -c "print($offset + $duration)")
done
ffmpeg -loglevel error -y -f concat -safe 0 -i concat.txt -c:v copy -an joined.mp4
ffmpeg -loglevel error -y -i joined.mp4 -i praxis-walkthrough.srt -map 0:v -map 1:s -c:v copy \
  -c:s mov_text -metadata:s:s:0 language=eng praxis-walkthrough.mp4
rm -f joined.mp4 concat.txt
echo "built praxis-walkthrough.mp4 ($(python3 -c "print(round($offset))") s)"
