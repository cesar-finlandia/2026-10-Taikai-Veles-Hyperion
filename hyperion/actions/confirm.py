"""Parse a confirmation reply into yes / no / other (DP-ACTIONS §5.3)."""
from __future__ import annotations

import re
from typing import Literal

Decision = Literal["yes", "no", "other"]

YES_WORDS = frozenset({"yes", "y", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed", "proceed", "go ahead", "do it", "please do",
    "yes please", "go for it", "approved", "approve", "affirmative", "correct", "sounds good", "alright", "absolutely", "definitely",
    "of course", "that's fine", "thats fine", "that is fine", "sí", "si", "oui", "ja", "sim"})
NO_WORDS = frozenset({"no", "n", "nope", "nah", "cancel", "stop", "abort", "don't", "dont", "do not", "never mind", "nevermind", "forget it", "leave it",
    "keep it", "not now", "decline", "reject", "negative", "no thanks", "no thank you", "don't do it", "dont do it", "nein", "non", "não", "nao"})
FILLER_WORDS = frozenset({"please", "thanks", "thank", "you", "it", "do", "go", "ahead", "proceed", "confirm", "that", "this", "the", "file", "folder",
    "delete", "overwrite", "change", "edit", "yes", "no", "now", "just", "then", "sure", "ok", "okay", "keep", "leave", "cancel", "stop", "the", "changes", "change"})

_CLEAN_RE = re.compile(r"[^a-z0-9áéíóúñãõâêîôûàèìòùäëïöüç' ]")


def parse_confirmation(text: str) -> Decision:
    """'yes' | 'no' | 'other'. 'no' wins whenever any no-word is present. Never raises."""
    try:
        t = text.lower()
        t = t.replace("’", "'")
        t = _CLEAN_RE.sub(" ", t)
        t = " ".join(t.split())
        if not t:
            return "other"
        if t in NO_WORDS:
            return "no"
        if t in YES_WORDS:
            return "yes"
        w = t.split()
        if len(w) > 6:
            return "other"
        # candidate detection
        candidate: Decision = "other"
        # any single token or bigram in NO_WORDS -> no
        found_no = False
        for i, tok in enumerate(w):
            if tok in NO_WORDS:
                found_no = True
                break
            if i + 1 < len(w):
                bigram = tok + " " + w[i + 1]
                if bigram in NO_WORDS:
                    found_no = True
                    break
        if found_no:
            candidate = "no"
        else:
            # first token or first bigram in YES_WORDS -> yes
            if w[0] in YES_WORDS:
                candidate = "yes"
            elif len(w) >= 2 and (w[0] + " " + w[1]) in YES_WORDS:
                candidate = "yes"
            else:
                return "other"
        # acceptance: every token not part of matched head must be filler
        if candidate == "no":
            # find matched no position: earliest single or bigram match
            match_idx: int | None = None
            match_len = 1
            for i, tok in enumerate(w):
                if tok in NO_WORDS:
                    match_idx = i
                    match_len = 1
                    break
                if i + 1 < len(w) and (tok + " " + w[i + 1]) in NO_WORDS:
                    match_idx = i
                    match_len = 2
                    break
            assert match_idx is not None
            rest = w[:match_idx] + w[match_idx + match_len:]
            # every remaining token that is not part of matched head must be filler;
            # but the matched head tokens themselves are excluded. However other
            # occurrences are part of rest and must be filler.
            for tok in rest:
                if tok not in FILLER_WORDS:
                    return "other"
            return "no"
        else:
            # yes: matched head is first token or first bigram
            if len(w) >= 2 and (w[0] + " " + w[1]) in YES_WORDS:
                head_len = 2
            else:
                head_len = 1
            rest = w[head_len:]
            for tok in rest:
                if tok not in FILLER_WORDS:
                    return "other"
            return "yes"
    except Exception:
        return "other"
