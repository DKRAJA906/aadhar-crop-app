from pathlib import Path
import io
import math

import pymupdf
from PIL import Image, ImageDraw
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, Response


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Aadhaar Rounded Front Back A4")

MAX_FILE_BYTES = 15 * 1024 * 1024

MM_TO_POINTS = 72 / 25.4

PAGE_WIDTH_INCHES = 8.267
PAGE_HEIGHT_INCHES = 11.693

CARD_WIDTH_MM = 85.6
CARD_HEIGHT_MM = 53.98

CARD_GAP_MM = 8
TOP_MARGIN_MM = 15

RENDER_DPI = 300
BORDER_WIDTH_POINTS = 1.5
CORNER_RADIUS_MM = 2.5


@app.get("/")
def home():
    return FileResponse(
        BASE_DIR / "static" / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


def make_rounded_png(pixmap, radius_points, width_points):
    image = Image.frombytes(
        "RGB",
        (pixmap.width, pixmap.height),
        pixmap.samples,
    ).convert("RGBA")

    radius_pixels = radius_points * image.width / width_points
    radius_pixels = min(
        radius_pixels,
        min(image.size) / 2,
    )

    scale = 3

    mask = Image.new(
        "L",
        (image.width * scale, image.height * scale),
        0,
    )

    drawing = ImageDraw.Draw(mask)

    drawing.rounded_rectangle(
        (0, 0, mask.width - 1, mask.height - 1),
        radius=radius_pixels * scale,
        fill=255,
    )

    mask = mask.resize(
        image.size,
        Image.Resampling.LANCZOS,
    )

    image.putalpha(mask)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def place_card(output_page, source_page, crop_rect, card_box):
    pixmap = source_page.get_pixmap(
        dpi=RENDER_DPI,
        clip=crop_rect,
        colorspace=pymupdf.csRGB,
        alpha=False,
        annots=True,
    )

    stroke = BORDER_WIDTH_POINTS

    available_width = card_box.width - 2 * stroke
    available_height = card_box.height - 2 * stroke

    image_ratio = pixmap.width / pixmap.height

    fitted_width = available_width
    fitted_height = fitted_width / image_ratio

    if fitted_height > available_height:
        fitted_height = available_height
        fitted_width = fitted_height * image_ratio

    image_left = (
        card_box.x0 + (card_box.width - fitted_width) / 2
    )

    image_top = (
        card_box.y0 + (card_box.height - fitted_height) / 2
    )

    image_rect = pymupdf.Rect(
        image_left,
        image_top,
        image_left + fitted_width,
        image_top + fitted_height,
    )

    corner_radius = min(
        CORNER_RADIUS_MM * MM_TO_POINTS,
        min(fitted_width, fitted_height) / 4,
    )

    png_data = make_rounded_png(
        pixmap,
        corner_radius,
        fitted_width,
    )

    output_page.insert_image(
        image_rect,
        stream=png_data,
        keep_proportion=True,
    )

    border_rect = pymupdf.Rect(
        image_rect.x0 - stroke / 2,
        image_rect.y0 - stroke / 2,
        image_rect.x1 + stroke / 2,
        image_rect.y1 + stroke / 2,
    )

    border_radius = corner_radius + stroke / 2

    radius_fraction = border_radius / min(
        border_rect.width,
        border_rect.height,
    )

    shape = output_page.new_shape()

    shape.draw_rect(
        border_rect,
        radius=radius_fraction,
    )

    shape.finish(
        color=(0, 0, 0),
        width=stroke,
        closePath=True,
    )

    shape.commit(overlay=True)


@app.post("/api/auto-a4")
def create_a4_pdf(
    file: UploadFile = File(...),
    password: str = Form(""),
    mode: str = Form("preview"),
    crop_top: float = Form(72.4),
    crop_bottom: float = Form(92.7),
    front_left: float = Form(7.8),
    front_right: float = Form(49.2),
    back_left: float = Form(50.7),
    back_right: float = Form(92.2),
):
    try:
        pdf_data = file.file.read(MAX_FILE_BYTES + 1)

        if not pdf_data:
            raise HTTPException(
                status_code=400,
                detail="Please select a PDF file.",
            )

        if len(pdf_data) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Maximum PDF file size is 15 MB.",
            )

        values = [
            crop_top,
            crop_bottom,
            front_left,
            front_right,
            back_left,
            back_right,
        ]

        if not all(
            math.isfinite(value) and 0 <= value <= 100
            for value in values
        ):
            raise HTTPException(
                status_code=400,
                detail="Crop values must be between 0 and 100.",
            )

        if not (
            crop_top < crop_bottom
            and front_left < front_right
            and back_left < back_right
        ):
            raise HTTPException(
                status_code=400,
                detail="Invalid crop boundaries.",
            )

        with pymupdf.open(
            stream=pdf_data,
            filetype="pdf",
        ) as source_pdf:
            if source_pdf.needs_pass:
                if not source_pdf.authenticate(password):
                    raise HTTPException(
                        status_code=400,
                        detail="PDF password is missing or incorrect.",
                    )

            if source_pdf.page_count < 1:
                raise HTTPException(
                    status_code=400,
                    detail="The PDF contains no pages.",
                )

            source_page = source_pdf[0]

            if source_page.rotation != 0:
                raise HTTPException(
                    status_code=400,
                    detail="Use an upright PDF without page rotation.",
                )

            bounds = source_page.rect

            if (
                bounds.width <= 0
                or bounds.height <= 0
                or max(bounds.width, bounds.height) > 2000
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Unsupported PDF page size.",
                )

            def make_crop(left_percent, right_percent):
                return pymupdf.Rect(
                    bounds.x0 + bounds.width * left_percent / 100,
                    bounds.y0 + bounds.height * crop_top / 100,
                    bounds.x0 + bounds.width * right_percent / 100,
                    bounds.y0 + bounds.height * crop_bottom / 100,
                )

            crops = [
                make_crop(front_left, front_right),
                make_crop(back_left, back_right),
            ]

            for crop in crops:
                if crop.width < 10 or crop.height < 10:
                    raise HTTPException(
                        status_code=400,
                        detail="Selected crop area is too small.",
                    )

            with pymupdf.open() as output_pdf:
                output_page = output_pdf.new_page(
                    width=PAGE_WIDTH_INCHES * 72,
                    height=PAGE_HEIGHT_INCHES * 72,
                )

                card_width = CARD_WIDTH_MM * MM_TO_POINTS
                card_height = CARD_HEIGHT_MM * MM_TO_POINTS
                gap = CARD_GAP_MM * MM_TO_POINTS
                top = TOP_MARGIN_MM * MM_TO_POINTS

                left = (
                    output_page.rect.width
                    - (2 * card_width + gap)
                ) / 2

                for index, crop in enumerate(crops):
                    x = left + index * (card_width + gap)

                    card_box = pymupdf.Rect(
                        x,
                        top,
                        x + card_width,
                        top + card_height,
                    )

                    place_card(
                        output_page,
                        source_page,
                        crop,
                        card_box,
                    )

                result = output_pdf.tobytes(
                    garbage=4,
                    deflate=True,
                )

        disposition = (
            "attachment"
            if mode == "download"
            else "inline"
        )

        return Response(
            content=result,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    disposition
                    + '; filename="aadhaar-rounded-a4.pdf"'
                ),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    except HTTPException:
        raise

    except Exception:
        raise HTTPException(
            status_code=400,
            detail=(
                "PDF processing failed. "
                "Check the PDF format, password and crop settings."
            ),
        )

    finally:
        file.file.close()
