"""No tagger: an empty tag record per row (no spans, no mood). The bundle then has no entities, no tagged mentions
and no mood, and the privacy rules run without PERSON spans. Useful to see the pipeline run before a tagger is set up;
never the choice for a public bundle (a PERSON tag is one of the ways a name gets cut)."""


class NoTags:
    name = "none"
    tagger = "none"
    model = "none"
    decode = "none"
    labels = ()

    def load(self, args):
        pass

    def tag(self, rows):
        return [{"spans": [], "mood": None} for _ in rows]
