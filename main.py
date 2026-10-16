from pathlib import Path
import math

import pymupdf
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, Response


BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Aadhaar Front Back A4 Print")

MAX_FILE_BYTES = 15 * 1024 * 1024

POINTS_PER_INCH = 72
MM_TO_POINTS = 72 / 25.4

PAGE_WIDTH_INCHES = 8.267
PAGE_HEIGHT_INCHES = 11.693

CARD_WIDTH_MM = 85.6
CARD_HEIGHT_MM = 53.98

CARD_GAP_MM = 8
TOP_MARGIN_MM = 15
CONTENT_PADDING_MM = 2

RENDER_DPI = 300

# Half of the previous 3-point border.
BORDER_WIDTH_POINTS = 1.5


@app.get("/")
def home():
    return FileResponse(
        BASE_DIR / "static" / "index.html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


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

        crop_values = [
            crop_top,
            crop_bottom,
            front_left,
            front_right,
            back_left,
            back_right,
        ]

        valid_values = all(
            math.isfinite(value) and 0 <= value <= 100
            for value in crop_values
        )

        if not valid_values:
            raise HTTPException(
                status_code=400,
                detail="Crop values must be between 0 and 100.",
            )

        valid_boundaries = (
            crop_top < crop_bottom
            and front_left < front_right
            and back_left < back_right
        )

        if not valid_boundaries:
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
                    detail=(
                        "This template requires an upright PDF "
                        "without page rotation."
                    ),
                )

            page_bounds = source_page.rect

            source_width = page_bounds.width
            source_height = page_bounds.height

            if (
                source_width <= 0
                or source_height <= 0
                or max(source_width, source_height) > 2000
            ):
                raise HTTPException(
                    status_code=400,
                    detail="Unsupported PDF page size.",
                )

            def make_crop_rectangle(left_percent, right_percent):
                return pymupdf.Rect(
                    page_bounds.x0
                    + source_width * left_percent / 100,
                    page_bounds.y0
                    + source_height * crop_top / 100,
                    page_bounds.x0
                    + source_width * right_percent / 100,
                    page_bounds.y0
                    + source_height * crop_bottom / 100,
                )

            front_crop = make_crop_rectangle(
                front_left,
                front_right,
            )

            back_crop = make_crop_rectangle(
                back_left,
                back_right,
            )

            crop_rectangles = [
                front_crop,
                back_crop,
            ]

            with pymupdf.open() as output_pdf:
                output_page = output_pdf.new_page(
                    width=PAGE_WIDTH_INCHES * POINTS_PER_INCH,
                    height=PAGE_HEIGHT_INCHES * POINTS_PER_INCH,
                )

                card_width = CARD_WIDTH_MM * MM_TO_POINTS
                card_height = CARD_HEIGHT_MM * MM_TO_POINTS
                gap = CARD_GAP_MM * MM_TO_POINTS

                top_margin = TOP_MARGIN_MM * MM_TO_POINTS
                padding = CONTENT_PADDING_MM * MM_TO_POINTS

                stroke = BORDER_WIDTH_POINTS

                total_cards_width = 2 * card_width + gap

                left_margin = (
                    output_page.rect.width - total_cards_width
                ) / 2

                for index, crop_rectangle in enumerate(
                    crop_rectangles
                ):
                    pixmap = source_page.get_pixmap(
                        dpi=RENDER_DPI,
                        clip=crop_rectangle,
                        colorspace=pymupdf.csRGB,
                        alpha=False,
                        annots=True,
                    )

                    card_left = (
                        left_margin
                        + index * (card_width + gap)
                    )

                    outer_rectangle = pymupdf.Rect(
                        card_left,
                        top_margin,
                        card_left + card_width,
                        top_margin + card_height,
                    )

                    content_rectangle = pymupdf.Rect(
                        outer_rectangle.x0 + padding,
                        outer_rectangle.y0 + padding,
                        outer_rectangle.x1 - padding,
                        outer_rectangle.y1 - padding,
                    )

                    output_page.insert_image(
                        content_rectangle,
                        stream=pixmap.tobytes("png"),
                        keep_proportion=True,
                    )

                    border_rectangle = pymupdf.Rect(
                        outer_rectangle.x0 + stroke / 2,
                        outer_rectangle.y0 + stroke / 2,
                        outer_rectangle.x1 - stroke / 2,
                        outer_rectangle.y1 - stroke / 2,
                    )

                    output_page.draw_rect(
                        border_rectangle,
                        color=(0, 0, 0),
                        width=stroke,
                        overlay=True,
                    )

                result_pdf = output_pdf.tobytes(
                    garbage=4,
                    deflate=True,
                )

        disposition = (
            "attachment"
            if mode == "download"
            else "inline"
        )

        return Response(
            content=result_pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    disposition
                    + '; filename="aadhaar-a4-print.pdf"'
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
                "Check the PDF format and password."
            ),
        )

    finally:
        file.file.close()
