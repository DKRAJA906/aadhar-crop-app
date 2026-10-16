import base64
import io

import pymupdf
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Aadhaar PDF Crop & Print")

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def home():
    return FileResponse("static/index.html")


@app.post("/api/render")
async def render_pdf(
    file: UploadFile = File(...),
    password: str = Form("")
):
    try:
        data = await file.read()

        doc = pymupdf.open(stream=data, filetype="pdf")

        if doc.needs_pass:
            if not password:
                return JSONResponse(
                    status_code=400,
                    content={"error": "यह PDF password-protected है। Password डालें।"}
                )

            if not doc.authenticate(password):
                return JSONResponse(
                    status_code=400,
                    content={"error": "Password गलत है।"}
                )

        page = doc[0]
        pix = page.get_pixmap(dpi=150)

        png_bytes = pix.tobytes("png")
        b64 = base64.b64encode(png_bytes).decode()

        return {
            "image": "data:image/png;base64," + b64,
            "page_width": page.rect.width,
            "page_height": page.rect.height
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/crop")
async def crop_pdf(
    file: UploadFile = File(...),
    password: str = Form(""),
    x1: float = Form(...),
    y1: float = Form(...),
    x2: float = Form(...),
    y2: float = Form(...),
    card_width_mm: float = Form(85.6),
    card_height_mm: float = Form(53.98)
):
    try:
        data = await file.read()
        doc = pymupdf.open(stream=data, filetype="pdf")

        if doc.needs_pass:
            if not password or not doc.authenticate(password):
                return JSONResponse(
                    status_code=400,
                    content={"error": "PDF password गलत है या password जरूरी है।"}
                )

        page = doc[0]

        crop_rect = pymupdf.Rect(
            min(x1, x2),
            min(y1, y2),
            max(x1, x2),
            max(y1, y2)
        )

        if crop_rect.is_empty or crop_rect.width <= 0 or crop_rect.height <= 0:
            return JSONResponse(
                status_code=400,
                content={"error": "Crop area सही नहीं है।"}
            )

        # Card size in PDF points: 1 inch = 25.4 mm, 1 inch = 72 points
        card_w = card_width_mm * 72 / 25.4
        card_h = card_height_mm * 72 / 25.4

        out = pymupdf.open()
        new_page = out.new_page(width=card_w, height=card_h)

        new_page.show_pdf_page(
            new_page.rect,
            doc,
            0,
            clip=crop_rect
        )

        pdf_bytes = out.tobytes()

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": "attachment; filename=aadhaar-card-print.pdf"
            }
        )

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
