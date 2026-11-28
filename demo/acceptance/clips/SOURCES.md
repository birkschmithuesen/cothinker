# Speech clips for the acceptance run

German speech with natural inner pauses, used by scenario `c` and as the
input for packet-gap injection. All three clips come from one public corpus.

| id | source URL | licence | licence URL | attribution | duration | inner pauses/min |
|---|---|---|---|---|---|---|
| de-parl-01 | https://huggingface.co/datasets/facebook/voxpopuli | CC0 1.0 | https://creativecommons.org/publicdomain/zero/1.0/ | VoxPopuli, Meta AI (facebookresearch/voxpopuli); raw data originates from the European Parliament (https://www.europarl.europa.eu/legal-notice/en/) | 166.5 s | 5.4 |
| de-parl-02 | https://huggingface.co/datasets/facebook/voxpopuli | CC0 1.0 | https://creativecommons.org/publicdomain/zero/1.0/ | VoxPopuli, Meta AI (facebookresearch/voxpopuli); raw data originates from the European Parliament | 141.1 s | 7.2 |
| de-parl-03 | https://huggingface.co/datasets/facebook/voxpopuli | CC0 1.0 | https://creativecommons.org/publicdomain/zero/1.0/ | VoxPopuli, Meta AI (facebookresearch/voxpopuli); raw data originates from the European Parliament | 152.1 s | 7.1 |

## How the clips were made

- Source: the German transcribed validation split of VoxPopuli. The dataset
  card states "The dataset is distributet under CC0 license"; the project
  README lists "VoxPopuli Data: CC0".
- Selection: transcribed, gold-transcript segments that contain at least one
  inner pause of 0.5 s or longer, end in sentence punctuation, and name no
  individual. 36 segments, 459.7 s in total, are concatenated unchanged (no
  added silence) into three clips, then converted with
  `ffmpeg -ac 1 -ar 16000 -c:a libopus -b:a 24k`.
- Inner pauses/min is
  `ffmpeg -i <clip> -af silencedetect=n=-40dB:d=0.5 -f null -`, counting only
  silences that neither start the clip nor end it. The joins between
  segments can add a few pauses, so the figure describes the clips as
  committed.
- Reference text (`<id>.ref.txt`): one sentence per line, taken from the
  corpus transcript with its punctuation. The corpus transcripts drop some
  colons and dashes and every percent sign; those were not restored.
- Speech is parliamentary, read or spoken by speakers of the corpus; no
  recording of a participant of any session is included.
