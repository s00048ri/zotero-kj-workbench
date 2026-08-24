"""Comparing card texts without a language-specific tokeniser.

Character n-grams, not words. A library that mixes English, Japanese and
French has no shared tokeniser, and word segmentation for Japanese would mean
a dictionary and a dependency for a job that character n-grams do well enough.

Everything here sees vocabulary, not argument. Two cards can share every word
and make opposite claims. That limit is stated wherever the numbers are shown.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import normalize

NGRAM_RANGE = (2, 4)
MAX_FEATURES = 40_000


def vectorise(texts: list[str], *, min_df: int = 1) -> np.ndarray:
    """L2-normalised TF-IDF over character n-grams, as a dense matrix.

    Normalising matters downstream: on unit vectors, euclidean distance is
    monotone in cosine distance, so Ward linkage — which is defined in
    euclidean terms — is clustering by the similarity we actually mean.
    """
    vectorizer = TfidfVectorizer(
        analyzer="char_wb",
        ngram_range=NGRAM_RANGE,
        min_df=min_df,
        max_features=MAX_FEATURES,
        sublinear_tf=True,
    )
    matrix = vectorizer.fit_transform(texts)
    return normalize(matrix).toarray()


@dataclass
class Pair:
    first: int
    second: int
    similarity: float


def least_alike(texts: list[str]) -> Pair | None:
    """The two texts in a group that share the least vocabulary.

    A crude proxy for tension. If two cards have almost nothing in common on
    the surface and the researcher grouped them anyway, the reason they did is
    what the group's label needs to say.
    """
    if len(texts) < 3:
        return None
    try:
        X = vectorise(texts)
    except ValueError:  # every text was stop-words or empty
        return None
    similarities = X @ X.T
    np.fill_diagonal(similarities, 2.0)
    flat = int(np.argmin(similarities))
    i, j = divmod(flat, len(texts))
    return Pair(first=i, second=j, similarity=float(similarities[i, j]))
