#!/usr/bin/env python3
"""
watermark — Apply watermark and export social media optimized copies.
Analyzes each photo's aspect ratio to route it to instagram/ or facebook/.
Places watermark in quietest corner, resizes for platform specs.

The green elements of the logo (jelly text, camera-d, camera-o ring,
jellybean) are hue-shifted per-photo to complement the dominant color
in the region where the watermark lands. Cream/neutral pixels are untouched.

Platform specs:
  Instagram : portrait/square → 1080x1350 max (4:5), JPEG quality 85
  Facebook  : landscape       → 1200x630  max (1.9:1), JPEG quality 85

Requirements:
    pip install pillow numpy --break-system-packages

Usage:
    watermark                        # prompts for folder
    watermark /path/to/folder
    watermark /path/to/folder --position bottom-right
    watermark /path/to/folder --opacity 0.65
    watermark /path/to/folder --no-adaptive   # keep original green
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from PIL import Image, ImageOps, ImageEnhance
    import numpy as np
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False

try:
    import cloudinary
    import cloudinary.uploader
    import os as _os
    _cld_cfg = cloudinary.config(
        cloud_name  = _os.environ.get("CLOUDINARY_CLOUD_NAME", ""),
        api_key     = _os.environ.get("CLOUDINARY_API_KEY", ""),
        api_secret  = _os.environ.get("CLOUDINARY_API_SECRET", ""),
    )
    HAS_CLOUDINARY = bool(
        _cld_cfg.cloud_name and
        _cld_cfg.api_key and
        _cld_cfg.api_secret
    )
except ImportError:
    HAS_CLOUDINARY = False

# ── Config ────────────────────────────────────────────────────────────────────

SCRIPT_DIR      = Path(__file__).parent
SUPPORTED       = {".jpg", ".jpeg", ".png", ".tiff", ".tif", ".webp"}
WATERMARK_EXTS  = {".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif", ".bmp", ".gif"}
DEFAULT_MARGIN  = 0.025
DEFAULT_SIZE    = 0.18
DEFAULT_OPACITY = 0.65
POSITIONS       = ["auto", "bottom-right", "bottom-left",
                   "top-right", "top-left", "center"]

# Platform output specs
INSTAGRAM = {"max_w": 1080, "max_h": 1350, "quality": 85, "label": "instagram"}
FACEBOOK  = {"max_w": 1200, "max_h": 630,  "quality": 85, "label": "facebook"}

LANDSCAPE_THRESHOLD = 1.1

# ── Green pixel detection ─────────────────────────────────────────────────────
# Hue range for the logo's green in HSV (OpenCV-style 0-360°)
# The logo green is ~105-135°, we use a generous band to catch anti-aliased edges
GREEN_HUE_MIN   = 80    # degrees
GREEN_HUE_MAX   = 160   # degrees
GREEN_SAT_MIN   = 0.35  # minimum saturation to qualify as "colored green"
GREEN_VAL_MIN   = 0.15  # minimum value — ignore near-black pixels

# ── Checks ────────────────────────────────────────────────────────────────────

def check_dependencies():
    if not HAS_DEPS:
        print("Missing: pip install pillow numpy --break-system-packages")
        sys.exit(1)


def find_watermark_file() -> Path:
    candidates = sorted(
        f for f in SCRIPT_DIR.iterdir()
        if f.is_file()
        and f.suffix.lower() in WATERMARK_EXTS
        and not f.name.startswith(".")
        and f.stem.lower() != "watermark"
    )
    if not candidates:
        candidates = sorted(
            f for f in SCRIPT_DIR.iterdir()
            if f.is_file() and f.suffix.lower() in WATERMARK_EXTS
        )
    if not candidates:
        print(f"\nERROR: No image file found in {SCRIPT_DIR}")
        print(f"  Place your logo/watermark image in that folder.")
        print(f"  Files currently there: {[f.name for f in SCRIPT_DIR.iterdir() if f.is_file()]}")
        sys.exit(1)
    if len(candidates) > 1:
        print(f"\nFound {len(candidates)} images in script folder:")
        for i, f in enumerate(candidates, 1):
            print(f"  [{i}] {f.name}")
        while True:
            choice = input("Which one is the watermark? (default: 1): ").strip()
            if choice == "":
                return candidates[0]
            if choice.isdigit() and 1 <= int(choice) <= len(candidates):
                return candidates[int(choice) - 1]
            print(f"  Enter 1–{len(candidates)}")
    return candidates[0]


def prompt_opacity() -> float:
    print("\nWatermark opacity:")
    options = [
        (0.15, "15% — very subtle, barely visible"),
        (0.25, "25% — light, unobtrusive"),
        (0.45, "45% — balanced, professional"),
        (0.65, "65% — strong, clearly visible  (recommended)"),
        (0.85, "85% — bold, very prominent"),
    ]
    for i, (pct, label) in enumerate(options, 1):
        print(f"  [{i}] {label}")
    while True:
        choice = input("\nChoose opacity [1-5] (default: 4): ").strip()
        if choice == "":
            return 0.65
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            val = options[int(choice) - 1][0]
            print(f"  Opacity set to {int(val*100)}%")
            return val
        print("  Enter 1–5")

# ── Folder input ──────────────────────────────────────────────────────────────

def pick_folder() -> Path:
    print("\nDrag and drop your image folder into the terminal and press Enter:")
    folder = input("Folder: ").strip().strip("'\"")
    if not folder:
        print("No folder entered.")
        sys.exit(0)
    return Path(folder).expanduser().resolve()

# ── Platform routing ──────────────────────────────────────────────────────────

def get_platform(img: Image.Image) -> dict:
    w, h  = img.size
    ratio = w / h
    return FACEBOOK if ratio > LANDSCAPE_THRESHOLD else INSTAGRAM


def resize_for_platform(img: Image.Image, platform: dict) -> Image.Image:
    img_w, img_h = img.size
    max_w = platform["max_w"]
    max_h = platform["max_h"]
    if img_w <= max_w and img_h <= max_h:
        return img
    scale = min(max_w / img_w, max_h / img_h)
    new_w = int(img_w * scale)
    new_h = int(img_h * scale)
    return img.resize((new_w, new_h), Image.LANCZOS)

# ── Corner analysis ───────────────────────────────────────────────────────────

def corner_variance(arr: np.ndarray, corner: str,
                    img_w: int, img_h: int,
                    wm_w: int, wm_h: int, margin: int) -> float:
    coords = {
        "bottom-right": (img_w - wm_w - margin, img_h - wm_h - margin),
        "bottom-left":  (margin,                 img_h - wm_h - margin),
        "top-right":    (img_w - wm_w - margin,  margin),
        "top-left":     (margin,                  margin),
    }
    x, y   = coords[corner]
    region = arr[
        max(0, y) : min(img_h, y + wm_h),
        max(0, x) : min(img_w, x + wm_w),
    ]
    return float(region.var())


def find_quietest_corner(img: Image.Image, wm_w: int,
                          wm_h: int, margin: int) -> str:
    img_w, img_h = img.size
    arr     = np.array(img.convert("RGB"), dtype=np.float32)
    penalty = {"bottom-right": 1.0, "bottom-left": 1.0,
               "top-right":    1.1, "top-left":    1.1}
    scores  = {
        c: corner_variance(arr, c, img_w, img_h, wm_w, wm_h, margin) * penalty[c]
        for c in penalty
    }
    return min(scores, key=scores.get)


def get_coords(img_w: int, img_h: int, wm_w: int,
               wm_h: int, position: str, margin: int) -> tuple:
    return {
        "bottom-right": (img_w - wm_w - margin, img_h - wm_h - margin),
        "bottom-left":  (margin,                 img_h - wm_h - margin),
        "top-right":    (img_w - wm_w - margin,  margin),
        "top-left":     (margin,                  margin),
        "center":       ((img_w - wm_w) // 2,     (img_h - wm_h) // 2),
    }.get(position, (img_w - wm_w - margin, img_h - wm_h - margin))

# ── Adaptive color ────────────────────────────────────────────────────────────

def image_to_hsv_arrays(img: Image.Image, downsample: int = 6):
    """
    Convert an RGB image to per-pixel H (0-360°), S (0-1), V (0-1) arrays.
    Downsamples by `downsample` factor for speed.
    Returns (hue, sat, val) numpy arrays flattened to 1-D.
    """
    thumb = img.convert("RGB").resize(
        (max(1, img.width // downsample),
         max(1, img.height // downsample)),
        Image.BOX,
    )
    arr   = np.array(thumb, dtype=np.float32) / 255.0
    R, G, B = arr[..., 0], arr[..., 1], arr[..., 2]
    cmax  = arr.max(axis=2)
    cmin  = arr.min(axis=2)
    delta = cmax - cmin

    V   = cmax
    S   = np.where(cmax > 0, delta / cmax, 0.0)
    hue = np.zeros_like(cmax)
    m   = delta > 0.001

    rm = m & (cmax == R)
    hue[rm] = (60 * ((G[rm] - B[rm]) / delta[rm])) % 360
    gm = m & (cmax == G)
    hue[gm] = 60 * ((B[gm] - R[gm]) / delta[gm]) + 120
    bm = m & (cmax == B)
    hue[bm] = 60 * ((R[bm] - G[bm]) / delta[bm]) + 240

    return hue.ravel(), S.ravel(), V.ravel()


# Warm hue ranges to bias toward.
# Each entry: (center_degrees, half_width_degrees, weight_multiplier)
# Red wraps around 0° so it gets two entries.
WARM_BIAS = [
    (  0,  25, 3.2),   # red / crimson
    (355,  20, 3.2),   # red wrap-around
    ( 20,  18, 2.8),   # orange-red
    ( 38,  18, 2.5),   # orange
    ( 52,  16, 2.0),   # amber
    ( 65,  14, 1.5),   # gold / yellow-orange
]

# Hue band to avoid — the logo's own green, with buffer either side
AVOID_HUE_MIN = 75
AVOID_HUE_MAX = 165


def _warm_multiplier(h: float) -> float:
    """Return a bias multiplier >= 1 if hue h falls in a warm range."""
    mult = 1.0
    for center, hw, w in WARM_BIAS:
        d = abs(h - center) % 360
        if d > 180:
            d = 360 - d
        if d <= hw:
            mult = max(mult, w * (1.0 - d / hw * 0.4))
    return mult


def pick_accent_hue(img: Image.Image, wm_x: int, wm_y: int,
                    wm_w: int, wm_h: int) -> float | None:
    """
    Choose an accent hue for the logo green pixels that:
      1. Contrasts with the photo's actual color content
      2. Never lands in the logo's own green band
      3. Is biased toward warm reds, oranges, and golds

    Samples the whole image for palette richness, but weights the
    watermark placement region 3× since that's where it matters most.

    Returns a hue in degrees [0, 360), or None if image is achromatic.
    """
    img_w, img_h = img.size

    hues_full, sat_full, val_full = image_to_hsv_arrays(img, downsample=8)

    region = img.convert("RGB").crop((
        max(0, wm_x), max(0, wm_y),
        min(img_w, wm_x + wm_w), min(img_h, wm_y + wm_h),
    ))
    hues_wm, sat_wm, val_wm = image_to_hsv_arrays(region, downsample=4)

    # Watermark region counts 3× more than the rest of the image
    hues = np.concatenate([hues_full, hues_wm, hues_wm, hues_wm])
    sats = np.concatenate([sat_full,  sat_wm,  sat_wm,  sat_wm])
    vals = np.concatenate([val_full,  val_wm,  val_wm,  val_wm])

    # Only chromatic, visible pixels vote
    mask    = (sats > 0.12) & (vals > 0.08)
    hues    = hues[mask]
    weights = (sats * vals)[mask]   # saturated + bright = more influence

    if weights.sum() < 0.5:
        return None   # effectively achromatic image

    # Weighted hue histogram, 1° bins
    bins    = np.zeros(360)
    indices = hues.astype(int) % 360
    np.add.at(bins, indices, weights)

    # Smooth with wrap-around so red (near 0°/360°) isn't penalised
    tiled    = np.tile(bins, 3)
    kernel   = np.ones(25) / 25          # simple box blur, no scipy needed
    smoothed = np.convolve(tiled, kernel, mode='same')
    bins     = smoothed[360:720]

    # Score every candidate hue 0-359°
    scores = np.zeros(360)
    for h in range(360):
        # Rare in the photo = more contrast against it
        contrast = 1.0 / (bins[h] + 0.01)

        # Warm bias
        warm = _warm_multiplier(float(h))

        # Zero out if inside the logo's own green band
        if AVOID_HUE_MIN <= h <= AVOID_HUE_MAX:
            scores[h] = 0.0
        else:
            scores[h] = contrast * warm

    best = int(np.argmax(scores))
    return float(best)


def is_green_pixel(h_deg: float, s: float, v: float) -> bool:
    """True if a pixel falls in the logo's green hue band."""
    return (GREEN_HUE_MIN <= h_deg <= GREEN_HUE_MAX
            and s >= GREEN_SAT_MIN
            and v >= GREEN_VAL_MIN)


def recolor_green_pixels(wm: Image.Image, target_hue_deg: float) -> Image.Image:
    """
    Replace every green pixel in the watermark with the target hue,
    preserving the original saturation and value (luminance) of each pixel
    so the logo texture and gradients stay intact.
    Cream/neutral pixels (low saturation) are left completely untouched.
    Anti-aliased edge pixels that are partly green get a proportional shift.
    """
    arr = np.array(wm, dtype=np.float32)          # RGBA, 0-255
    out = arr.copy()

    rgb  = arr[..., :3] / 255.0                   # normalise to 0-1
    alpha = arr[..., 3]

    # Convert entire image to HSV vectorised
    R, G, B = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    cmax  = rgb.max(axis=2)
    cmin  = rgb.min(axis=2)
    delta = cmax - cmin

    V = cmax
    S = np.where(cmax > 0, delta / cmax, 0.0)

    hue = np.zeros_like(cmax)
    m   = delta > 0.001

    rm = m & (cmax == R)
    hue[rm] = (60 * ((G[rm] - B[rm]) / delta[rm])) % 360

    gm = m & (cmax == G)
    hue[gm] = 60 * ((B[gm] - R[gm]) / delta[gm]) + 120

    bm = m & (cmax == B)
    hue[bm] = 60 * ((R[bm] - G[bm]) / delta[bm]) + 240

    # Build mask of green pixels (visible and chromatic)
    green_mask = (
        (hue  >= GREEN_HUE_MIN) & (hue  <= GREEN_HUE_MAX) &
        (S    >= GREEN_SAT_MIN) &
        (V    >= GREEN_VAL_MIN) &
        (alpha > 10)                                # skip near-transparent
    )

    if not green_mask.any():
        return wm   # nothing to recolor

    target_h = target_hue_deg / 360.0              # colorsys expects 0-1

    # Recolor matching pixels one-by-one via vectorised colorsys equivalent
    # (colorsys has no numpy path, so we use the HSV→RGB formula directly)
    H  = np.full_like(V, target_h)                 # new hue, uniform
    S_ = S.copy()
    V_ = V.copy()

    # HSV → RGB (vectorised)
    i   = (H * 6).astype(int) % 6
    f   = H * 6 - np.floor(H * 6)
    p   = V_ * (1 - S_)
    q   = V_ * (1 - f * S_)
    t   = V_ * (1 - (1 - f) * S_)

    new_r = np.select(
        [i==0, i==1, i==2, i==3, i==4, i==5],
        [V_,   q,    p,    p,    t,    V_  ], default=V_)
    new_g = np.select(
        [i==0, i==1, i==2, i==3, i==4, i==5],
        [t,    V_,   V_,   q,    p,    p   ], default=V_)
    new_b = np.select(
        [i==0, i==1, i==2, i==3, i==4, i==5],
        [p,    p,    t,    V_,   V_,   q   ], default=V_)

    # Apply only to green pixels
    out[green_mask, 0] = np.clip(new_r[green_mask] * 255, 0, 255)
    out[green_mask, 1] = np.clip(new_g[green_mask] * 255, 0, 255)
    out[green_mask, 2] = np.clip(new_b[green_mask] * 255, 0, 255)
    # Alpha channel unchanged

    return Image.fromarray(out.astype(np.uint8), "RGBA")

# ── Watermark loading ─────────────────────────────────────────────────────────

def load_watermark(wm_path: Path, opacity: float) -> Image.Image:
    """Load watermark and bake opacity into alpha channel."""
    wm      = Image.open(wm_path).convert("RGBA")
    r, g, b, a = wm.split()
    a       = ImageEnhance.Brightness(a).enhance(opacity)
    return Image.merge("RGBA", (r, g, b, a))

# ── Cloudinary upload ─────────────────────────────────────────────────────────

def upload_to_cloudinary(out_path: Path, platform_label: str) -> bool:
    """
    Upload a finished watermarked file to Cloudinary.
    Places it in Framepost/instagram or Framepost/facebook and tags accordingly.
    Returns True on success, False on failure.
    """
    if not HAS_CLOUDINARY:
        return False
    folder = f"Framepost/{platform_label}"
    tags   = ["framepost", platform_label]
    try:
        cloudinary.uploader.upload(
            str(out_path),
            folder          = folder,
            tags            = tags,
            resource_type   = "image",
            use_filename    = True,
            unique_filename = True,
            overwrite       = False,
        )
        return True
    except Exception as e:
        print(f" [Cloudinary ERROR: {e}]", end="")
        return False


# ── Core ──────────────────────────────────────────────────────────────────────

def apply_watermark_and_export(img_path: Path,
                                ig_dir: Path, fb_dir: Path,
                                wm_orig: Image.Image,
                                position: str,
                                adaptive: bool) -> tuple:
    """
    Process one image:
    1. Determine platform from aspect ratio
    2. Resize for that platform
    3. Choose placement corner
    4. Sample photo region → derive complementary hue
    5. Recolor green logo pixels to that hue
    6. Paste watermark and save
    7. Upload to Cloudinary with platform tags (if configured)
    Returns (platform_label, chosen_corner, accent_hue_deg or None, cloudinary_uploaded)
    """
    img      = ImageOps.exif_transpose(Image.open(img_path)).convert("RGBA")
    platform = get_platform(img)
    img      = resize_for_platform(img, platform)

    img_w, img_h = img.size
    margin   = max(8, int(img_w * DEFAULT_MARGIN))

    # Scale watermark
    wm_w    = max(80, int(img_w * DEFAULT_SIZE))
    aspect  = wm_orig.height / wm_orig.width
    wm      = wm_orig.resize((wm_w, int(wm_w * aspect)), Image.LANCZOS)

    # Choose corner
    chosen = position
    if position == "auto":
        chosen = find_quietest_corner(img, wm.width, wm.height, margin)

    x, y = get_coords(img_w, img_h, wm.width, wm.height, chosen, margin)

    # Adaptive recoloring
    accent_hue = None
    wm_colored = wm
    if adaptive:
        accent_hue = pick_accent_hue(img, x, y, wm.width, wm.height)
        if accent_hue is not None:
            wm_colored = recolor_green_pixels(wm, accent_hue)
        # If image is achromatic, keep original green (wm_colored = wm)

    # Composite
    result = img.copy()
    result.paste(wm_colored, (x, y), wm_colored)

    # Save
    out_dir  = ig_dir if platform["label"] == "instagram" else fb_dir
    out_path = out_dir / (img_path.stem + ".jpg")
    result.convert("RGB").save(
        out_path, format="JPEG",
        quality=platform["quality"],
        optimize=True
    )

    # Upload to Cloudinary if credentials are available
    uploaded = upload_to_cloudinary(out_path, platform["label"])

    return platform["label"], chosen, accent_hue, uploaded


def process_folder(folder: Path, wm_path: Path,
                   position: str, opacity: float, adaptive: bool,
                   output_name: str = ""):
    files = sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED
    )
    if not files:
        print(f"No supported images found in {folder}")
        sys.exit(0)

    if output_name:
        folder_name = output_name
    else:
        print(f"\nOutput folder name (inside {folder.parent}/) [default: social]: ", end="")
        folder_name = input().strip() or "social"
    social_dir  = folder.parent / folder_name
    ig_dir      = social_dir / "instagram"
    fb_dir      = social_dir / "facebook"
    ig_dir.mkdir(parents=True, exist_ok=True)
    fb_dir.mkdir(parents=True, exist_ok=True)

    wm = load_watermark(wm_path, opacity)

    print(f"\nWatermark : {wm_path.name}")
    print(f"Found     : {len(files)} image(s)")
    print(f"Position  : {position}")
    print(f"Opacity   : {int(opacity*100)}%")
    print(f"Adaptive  : {'on — green accent shifts per photo' if adaptive else 'off — original green kept'}")
    print(f"Cloudinary: {'on — uploading to Framepost/instagram + Framepost/facebook' if HAS_CLOUDINARY else 'off — install cloudinary + set env vars to enable'}")
    print(f"Instagram : max {INSTAGRAM['max_w']}x{INSTAGRAM['max_h']}  "
          f"(portrait/square, ratio ≤ {LANDSCAPE_THRESHOLD})")
    print(f"Facebook  : max {FACEBOOK['max_w']}x{FACEBOOK['max_h']}  "
          f"(landscape, ratio > {LANDSCAPE_THRESHOLD})")
    print(f"Output    : {social_dir}\n")

    ig_count = fb_count = errors = 0

    for i, fpath in enumerate(files, 1):
        print(f"  [{i}/{len(files)}] {fpath.name} ...", end="", flush=True)
        try:
            platform, chosen, hue, uploaded = apply_watermark_and_export(
                fpath, ig_dir, fb_dir, wm, position, adaptive
            )
            hue_str    = f"  accent {hue:.0f}°" if hue is not None else "  (neutral — kept green)"
            cloud_str  = "  ☁ uploaded" if uploaded else ""
            print(f" {platform}  [{chosen}]{hue_str}{cloud_str}")
            if platform == "instagram":
                ig_count += 1
            else:
                fb_count += 1
        except Exception as e:
            print(f" ERROR: {e}")
            errors += 1

    print(f"\nDone.")
    print(f"  Instagram : {ig_count} photo(s)  →  {ig_dir}")
    print(f"  Facebook  : {fb_count} photo(s)  →  {fb_dir}")
    if HAS_CLOUDINARY:
        print(f"  Cloudinary: uploaded to Framepost/instagram + Framepost/facebook")
    if errors:
        print(f"  Errors    : {errors}")

# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Watermark and export photos optimized for Instagram or Facebook"
    )
    parser.add_argument("folder",        nargs="?", default="",
                        help="Image folder (drag and drop or type path)")
    parser.add_argument("--position",    default="auto", choices=POSITIONS,
                        help="Corner — auto picks quietest (default: auto)")
    parser.add_argument("--opacity",     type=float, default=DEFAULT_OPACITY,
                        help=f"Watermark opacity 0.0-1.0 (default: {DEFAULT_OPACITY})")
    parser.add_argument("--watermark",   default="",
                        help="Custom watermark image path")
    parser.add_argument("--output",      default="",
                        help="Output folder name inside the source folder's parent (default: prompts)")
    parser.add_argument("--no-adaptive", action="store_true",
                        help="Disable adaptive hue — keep original logo green")
    args = parser.parse_args()

    check_dependencies()

    wm_path = Path(args.watermark).expanduser().resolve() if args.watermark \
              else find_watermark_file()

    print(f"Watermark file: {wm_path.name}")

    folder = Path(args.folder).expanduser().resolve() if args.folder \
             else pick_folder()

    if not folder.is_dir():
        print(f"Not a directory: {folder}")
        sys.exit(1)

    opacity = args.opacity if args.opacity != DEFAULT_OPACITY \
              else prompt_opacity()

    process_folder(folder, wm_path, args.position, opacity,
                   adaptive=not args.no_adaptive,
                   output_name=args.output or "")


if __name__ == "__main__":
    main()
