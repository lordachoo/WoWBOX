import numpy as np
import pytest
from PIL import Image

from wowbox.bridge.decoder import NUM_BLOCKS, DecodeError, decode, encode

VALUES = {
    "health": 255, "power": 102, "power_type": 0,
    "target_health": 128, "target_flags": 1 | 2, "player_flags": 1,
    "input_flags": 0, "cast_flags": 1, "status_flags": 0,
    "party": [255, 51, 0, 200], "level": 80, "party_bits": 0b1011,
    "name": "Tankadin", "class": "PALADIN",
}


def frame(block=8, frame_no=7, values=VALUES, pad=(20, 60)):
    strip = encode(values, frame_no, block)
    img = np.full((strip.shape[0] + pad[0], strip.shape[1] + pad[1], 3), 37, dtype=np.uint8)
    img[: strip.shape[0], : strip.shape[1]] = strip
    return img


def test_roundtrip():
    st = decode(frame())
    assert st.frame == 7
    assert st.name == "Tankadin" and st.class_token == "PALADIN"
    assert st.health_pct == 100.0
    assert st.power_pct == 40.0 and st.power_name == "mana"
    assert st.target_exists and st.target_hostile and not st.target_dead
    assert st.target_health_pct == pytest.approx(50.2, abs=0.1)
    assert st.combat and not st.resting
    assert st.casting and not st.channeling
    assert not st.typing
    assert st.level == 80
    assert st.party_exists == (True, True, False, True)
    assert st.party_health_pct[1] == 20.0


def test_unicode_name_and_identical_neighbours():
    # zero padding and repeated letters put identical data in adjacent blocks; markers keep them apart
    st = decode(frame(values={**VALUES, "name": "Ääää", "class": ""}))
    assert st.name == "Ääää" and st.class_token == ""


def test_typing_flag():
    st = decode(frame(values={**VALUES, "input_flags": 3}))
    assert st.typing and st.chat_open


@pytest.mark.parametrize("block", [3, 4, 6, 8, 12])
def test_block_sizes(block):
    assert decode(frame(block=block)).frame == 7


@pytest.mark.parametrize("sx,sy", [(0.8, 0.8), (1.33, 0.64), (1.17, 1.17), (2.0, 0.5), (0.7, 1.4), (1.5, 1.5)])
@pytest.mark.parametrize("resample", ["NEAREST", "BILINEAR"])
def test_stretched_strip_decodes(sx, sy, resample):
    """The game stretches its render to the window, unevenly and with blended edges."""
    src = frame(block=8)
    h, w = src.shape[:2]
    img = np.asarray(Image.fromarray(src).resize((int(w * sx), max(2, int(h * sy))), getattr(Image, resample)))
    st = decode(img)
    assert st.name == "Tankadin" and st.level == 80


def test_irregular_block_widths():
    """Nearest-neighbour stretching by a non-integer factor gives blocks of mixed widths (seen live: 8,6,6,6,8...)."""
    src = encode(VALUES, 7, 1)[0]  # one pixel per block
    widths = [8 if i % 4 == 0 else 6 for i in range(NUM_BLOCKS)]
    row = np.concatenate([np.repeat(src[i:i + 1], wd, axis=0) for i, wd in enumerate(widths)])
    img = np.repeat(row[None], 3, axis=0)
    assert decode(img).name == "Tankadin"


def test_no_header():
    with pytest.raises(DecodeError, match="header"):
        decode(np.zeros((12, 300, 3), dtype=np.uint8))


def test_corrupt_block_fails_checksum():
    img = frame()
    img[:8, 8 * 3:8 * 4, 0] ^= 0x10  # flip a bit in block 3's data
    with pytest.raises(DecodeError, match="checksum"):
        decode(img)


def test_strip_cut_off():
    with pytest.raises(DecodeError, match="blocks visible"):
        decode(frame()[:, : 8 * 10])


def test_fast_run_splitter_matches_the_pixel_by_pixel_one():
    from wowbox.bridge.decoder import _runs

    def slow(row, tol):
        runs, start = [], 0
        for x in range(1, row.shape[0] + 1):
            if x == row.shape[0] or np.abs(row[x].astype(int) - row[start].astype(int)).max() > tol:
                runs.append((start, x - start))
                start = x
        return runs

    rng = np.random.default_rng(7)
    for _ in range(200):
        widths = rng.integers(1, 150, size=rng.integers(1, 20))
        row = np.concatenate([np.tile(rng.integers(0, 256, 3), (w, 1)) for w in widths]).astype(np.int16)
        row = np.clip(row + rng.integers(-5, 6, row.shape), 0, 255).astype(np.uint8)  # capture noise around tol
        assert _runs(row, 4) == slow(row, 4)
