from PIL import Image, ImageDraw, ImageFont

from ocr_ptr_pdf_converter.orient import best_rotation, orientation_score


def _text_image(text: str, rotate: int = 0) -> Image.Image:
    img = Image.new("RGB", (800, 200), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
    except OSError:
        font = ImageFont.load_default()
    draw.text((20, 60), text, fill="black", font=font)
    return img.rotate(-rotate, expand=True)


def test_score_higher_for_correct_orientation():
    img = _text_image("PURCHASE SALE EXCHANGE AMOUNT DATE")
    upright = orientation_score(img)
    flipped = orientation_score(img.rotate(180, expand=True))
    assert upright > flipped


def test_best_rotation_picks_zero_for_upright():
    img = _text_image("PURCHASE SALE EXCHANGE")
    rot, _ = best_rotation(img)
    assert rot == 0


def test_best_rotation_recovers_180():
    img = _text_image("PURCHASE SALE EXCHANGE", rotate=180)
    rot, _ = best_rotation(img)
    assert rot == 180
