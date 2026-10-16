from pathlib import Path
import math

import pymupdf
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, Response

BASE = Path(__file__).resolve().parent
app = FastAPI(title="Aadhaar A4 Print")

MAX_BYTES = 15 * 1024 * 1024
MM = 72 / 25.4


@app.get("/")
def home():
    return FileResponse(BASE / "static" / "index.html")


@app.post("/api/auto-a4")
def auto_a4(
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
        data = file.file.read(MAX_BYTES + 1)

        if not data:
            raise HTTPException(400, "Please select a PDF.")

        if len(data) > MAX_BYTES:
            raise HTTPException(413, "Maximum PDF size is 15 MB.")

        values = [
            crop_top, crop_bottom,
            front_left, front_right,
            back_left, back_right,
        ]

        if not all(math.isfinite(v) and 0 <= v <= 100 for v in values):
            raise HTTPException(400, "Crop values must be between 0 and 100.")

        if not (
            crop_top < crop_bottom
            and front_left < front_right
            and back_left < back_right
        ):
            raise HTTPException(400, "Invalid crop boundaries.")

        with pymupdf.open(stream=data, filetype="pdf") as source:
            if source.needs_pass and not source.authenticate(password):
                raise HTTPException(400, "PDF password is missing or incorrect.")

            if source.page_count < 1:
                raise HTTPException(400, "PDF contains no pages.")

            page = source[0]

            if page.rotation != 0:
                raise HTTPException(
                    400,
                    "Use an upright, unrotated PDF for this template."
                )

            bounds = page.rect
            width = bounds.width
            height = bounds.height

            if width <= 0 or height <= 0 or max(width, height) > 2000:
                raise HTTPException(400, "Unsupported PDF page size.")

            def clip_rect(left, right):
                return pymupdf.Rect(
                    bounds.x0 + width * left / 100,
                    bounds.y0 + height * crop_top / 100,
                    bounds.x0 + width * right / 100,
                    bounds.y0 + height * crop_bottom / 100,
                )

            clips = [
                clip_rect(front_left, front_right),
                clip_rect(back_left, back_right),
            ]

            with pymupdf.open() as output:
                                sheet = output.new_page(
                    width=8.267 * 72,
                    height=11.693 * 72,
                )

                card_w = 85.6 * MM
                card_h = 53.98 * MM
                gap = 8 * MM
                left_margin = (sheet.rect.width - 2 * card_w - gap) / 2
                top_margin = 15 * MM

                stroke = 1.5
                padding = 2 * MM

                for index, clip in enumerate(clips):
                    pix = page.get_pixmap(
                        dpi=300,
                        clip=clip,
                        colorspace=pymupdf.csRGB,
                        alpha=False,
                        annots=True,
                    )

                    x = left_margin + index * (card_w + gap)

                    outer = pymupdf.Rect(
                        x,
                        top_margin,
                        x + card_w,
                        top_margin + card_h,
                    )

                    content = pymupdf.Rect(
                        outer.x0 + padding,
                        outer.y0 + padding,
                        outer.x1 - padding,
                        outer.y1 - padding,
                    )

                    sheet.insert_image(
                        content,
                        stream=pix.tobytes("png"),
                        keep_proportion=True,
                    )

                    border = pymupdf.Rect(
                        outer.x0 + stroke / 2,
                        outer.y0 + stroke / 2,
                        outer.x1 - stroke / 2,
                        outer.y1 - stroke / 2,
                    )

                    sheet.draw_rect(
                        border,
                        color=(0, 0, 0),
                        width=stroke,
                        overlay=True,
                    )

                result = output.tobytes(garbage=4, deflate=True)

        disposition = "attachment" if mode == "download" else "inline"

        return Response(
            content=result,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    disposition + '; filename="aadhaar-a4-print.pdf"'
                ),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            400,
            "PDF processing failed. Check the PDF format and password."
        )
    finally:
        file.file.close()
