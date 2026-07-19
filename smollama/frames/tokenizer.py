"""Vendored CLIP BPE tokenizer (adapted from openai/CLIP, MIT license).

Pure Python so the master node needs no torch. Requires the ``regex`` package
(CLIP's token pattern uses \\p{L}/\\p{N} classes that stdlib ``re`` lacks) and
the BPE vocab file (``bpe_simple_vocab_16e6.txt.gz``) exported alongside the
ONNX models by scripts/jetson/export_clip.py.
"""

import gzip
import html
from functools import lru_cache


@lru_cache()
def bytes_to_unicode() -> dict[int, str]:
    """Map utf-8 bytes to printable unicode chars for reversible BPE."""
    bs = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("¡"), ord("¬") + 1))
        + list(range(ord("®"), ord("ÿ") + 1))
    )
    cs = bs[:]
    n = 0
    for b in range(2**8):
        if b not in bs:
            bs.append(b)
            cs.append(2**8 + n)
            n += 1
    return dict(zip(bs, [chr(c) for c in cs]))


def _get_pairs(word: tuple[str, ...]) -> set[tuple[str, str]]:
    pairs = set()
    prev = word[0]
    for char in word[1:]:
        pairs.add((prev, char))
        prev = char
    return pairs


class SimpleTokenizer:
    """CLIP's BPE tokenizer, producing padded id sequences for the text encoder."""

    def __init__(self, bpe_path: str, context_length: int = 77):
        import regex  # deferred: optional dependency, only needed for frame search

        self._regex = regex
        self.context_length = context_length
        self.byte_encoder = bytes_to_unicode()

        merges = gzip.open(bpe_path).read().decode("utf-8").split("\n")
        merges = merges[1 : 49152 - 256 - 2 + 1]
        merge_pairs = [tuple(m.split()) for m in merges]

        vocab = list(bytes_to_unicode().values())
        vocab = vocab + [v + "</w>" for v in vocab]
        for merge in merge_pairs:
            vocab.append("".join(merge))
        vocab.extend(["<|startoftext|>", "<|endoftext|>"])

        self.encoder = dict(zip(vocab, range(len(vocab))))
        self.bpe_ranks = dict(zip(merge_pairs, range(len(merge_pairs))))
        self.cache: dict[str, str] = {
            "<|startoftext|>": "<|startoftext|>",
            "<|endoftext|>": "<|endoftext|>",
        }
        self.pat = regex.compile(
            r"""<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+""",
            regex.IGNORECASE,
        )

    def _bpe(self, token: str) -> str:
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        pairs = _get_pairs(word)
        if not pairs:
            return token + "</w>"

        while True:
            bigram = min(pairs, key=lambda pair: self.bpe_ranks.get(pair, float("inf")))
            if bigram not in self.bpe_ranks:
                break
            first, second = bigram
            new_word: list[str] = []
            i = 0
            while i < len(word):
                try:
                    j = word.index(first, i)
                except ValueError:
                    new_word.extend(word[i:])
                    break
                new_word.extend(word[i:j])
                i = j
                if word[i] == first and i < len(word) - 1 and word[i + 1] == second:
                    new_word.append(first + second)
                    i += 2
                else:
                    new_word.append(word[i])
                    i += 1
            word = tuple(new_word)
            if len(word) == 1:
                break
            pairs = _get_pairs(word)

        result = " ".join(word)
        self.cache[token] = result
        return result

    def encode(self, text: str) -> list[int]:
        text = html.unescape(html.unescape(text))
        text = self._regex.sub(r"\s+", " ", text).strip().lower()

        bpe_tokens: list[int] = []
        for token in self._regex.findall(self.pat, text):
            token = "".join(self.byte_encoder[b] for b in token.encode("utf-8"))
            bpe_tokens.extend(self.encoder[t] for t in self._bpe(token).split(" "))
        return bpe_tokens

    def __call__(self, text: str) -> list[int]:
        """Tokenize to a fixed-length, zero-padded id sequence with SOT/EOT."""
        sot = self.encoder["<|startoftext|>"]
        eot = self.encoder["<|endoftext|>"]
        tokens = [sot] + self.encode(text) + [eot]
        if len(tokens) > self.context_length:
            tokens = tokens[: self.context_length]
            tokens[-1] = eot
        return tokens + [0] * (self.context_length - len(tokens))
