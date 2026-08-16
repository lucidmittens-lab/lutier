"""
matte_combine.py -- Qt-free backend for the "matte_combine" stage kind:
combines TWO already-computed upstream mattes (see stack.py's own
docstring, "MATTE CHANNELS") with a boolean-style blend mode, without
ever reading the image's own RGB values -- unlike keyer.py/segmentation.py,
this matte-producing kind's INPUT is other mattes, not pixel color.

Reuses the SAME "reference an upstream producer by its own Stage.id,
gracefully degrade if unresolved" mechanism stage.params["matte_source_id"]
already established for CONSUMERS -- this stage's own "matte_a_source_id"/
"matte_b_source_id" params are just two more instances of that same kind
of reference, resolved by stack._compute_raw_matte against the `mattes`
dict _run_stages has already accumulated by this stage's own position (so
a matte_combine referencing a producer that sits downstream of it, or one
that's been removed, resolves to "no matte" there exactly like any other
consumer's reference would -- stack.py's own dispatch, not this module,
owns that resolution and the "missing input -> treat as an all-zero
matte" fallback, so this module's own function always receives two real
arrays and stays simple).

Being itself a matte-PRODUCING kind (joins stack.MATTE_PRODUCING_KINDS),
matte_combine's own output can be referenced by any downstream consumer
via the SAME per-row matte dropdown any other matte producer already
offers -- and its own two inputs can point at ANY upstream matte
producer, including another matte_combine, letting combines chain.

OPS -- colorist-familiar blend-mode vocabulary rather than raw boolean
terms, since a matte in this app is always a [0,1] float, not a hard
boolean:
- "intersect" (AND): a * b
- "union" (OR): max(a, b)
- "subtract" (A minus B): max(0, a - b)
- "difference" (XOR): abs(a - b)

Same "no PySide6 import" rule as every other backend module in this app.
"""
import numpy as np

DEFAULT_OP = "intersect"
OPS = ("intersect", "union", "subtract", "difference")


def compute_combined_matte(matte_a, matte_b, op=DEFAULT_OP):
    """matte_a/matte_b: (H,W) float arrays in [0,1] -- callers (stack.py's
    own dispatch) are responsible for resolving an unresolved/missing
    reference to an all-zero array of the right shape BEFORE calling
    this, so this function never has to guess a shape from nothing.
    Returns an (H,W) float array in [0,1]."""
    if op == "intersect":
        result = matte_a * matte_b
    elif op == "union":
        result = np.maximum(matte_a, matte_b)
    elif op == "subtract":
        result = np.maximum(0.0, matte_a - matte_b)
    elif op == "difference":
        result = np.abs(matte_a - matte_b)
    else:
        raise ValueError(f"Unknown matte combine op: {op!r} (expected one of {OPS})")
    return np.clip(result, 0.0, 1.0)
