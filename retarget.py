"""
retarget.py -- change the input/output color space a LUT (or any object with
an .apply(RGB) method: colour.LUT1D, LUT3D, LUTSequence, or our own CLF-node
wrappers) operates on, without invoking a DRT/RRT+ODT.

Example: an LMT authored to expect and return ACEScct (AP1) code values,
retargeted so it can instead be fed ACES2065-1 (AP0 linear) values directly.
No resampling of the original LUT happens here -- this wraps it with a
pre-conversion and a post-conversion step. When the *internal* representation
is CLF-native (a ProcessList/node chain), the equivalent operation is
prepending/appending Matrix + transfer-function nodes to the chain instead of
this Python-level wrapper -- same idea, expressed as CLF nodes so it survives
export losslessly. This wrapper is useful on its own for applying/previewing
values, and as the reference behavior the node-chain version must match.
"""
import numpy as np

from colorspace import convert


def retarget_io(lut_like, from_space: str, to_space: str,
                 chromatic_adaptation_transform: str = "CAT02"):
    """Return a callable(RGB) that behaves like `lut_like.apply(RGB)`, except
    it expects and returns values encoded in `to_space` instead of
    `from_space`.

    lut_like: anything with an .apply(RGB) -> RGB method (colour.LUT1D,
    LUT3D, LUTSequence, or a plain object exposing the same interface).
    from_space: the color space `lut_like` was actually authored for.
    to_space: the color space you want to feed it / read results in instead.
    """

    def wrapped(RGB):
        RGB = np.asarray(RGB, dtype=float)
        native_space_input = convert(
            RGB, from_space=to_space, to_space=from_space,
            chromatic_adaptation_transform=chromatic_adaptation_transform)
        native_space_output = lut_like.apply(native_space_input)
        return convert(
            native_space_output, from_space=from_space, to_space=to_space,
            chromatic_adaptation_transform=chromatic_adaptation_transform)

    wrapped.from_space = from_space
    wrapped.to_space = to_space
    wrapped.wraps = lut_like
    return wrapped
