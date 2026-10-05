"""oneiroNER: our own span NER model with a VAD head (the tagger of the real run). Optional.

The checkpoint and its batch tagger are not public and not in this repo. This adapter runs them as an outside
program: set ONEIRONER_TAG_CMD (or pass --cmd) to a command that reads the slice and writes tags-<kind>.jsonl in the
contract. {slice}, {out}, {kinds} and {limit} in the command are filled in, e.g.

  ONEIRONER_TAG_CMD='python /path/to/oneiroNER/tools/tag_slice.py --slice {slice} --out {out} --kinds {kinds}'

run_tagger.py then checks every file it wrote against the slice (same ids, order and count, span texts, mood
ranges). The real run's numbers and register (chat in 2,048-token windows with A:/B: role prefixes) are in
pipeline/tag/README.md. Without the checkpoint, use the gliner_vad adapter.
"""
import os
import shlex
import subprocess


class OneiroNER:
    name = "oneironer"
    tagger = "oneiroNER"
    model = "oneironer"
    decode = "v1"
    labels = ("PERSON", "PLACE", "ORG", "DATE", "EVENT", "RELATIONSHIP_REF", "EMOTION", "MONEY", "PRODUCT")

    def load(self, args):
        self.cmd = getattr(args, "cmd", None) or os.environ.get("ONEIRONER_TAG_CMD")
        if not self.cmd:
            raise SystemExit("the oneironer adapter needs the private oneiroNER checkpoint and its batch tagger: set "
                             "ONEIRONER_TAG_CMD (or --cmd) to the command that writes tags-<kind>.jsonl, or use "
                             "--adapter gliner_vad")

    def run_all(self, slice_dir, out_dir, kinds, limit):
        cmd = self.cmd.format(slice=shlex.quote(slice_dir), out=shlex.quote(out_dir), kinds=",".join(kinds),
                              limit=limit or 0)
        p = subprocess.run(cmd, shell=True)
        if p.returncode:
            raise SystemExit(f"oneironer: the tag command exited {p.returncode}")
