from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from werkzeug.utils import secure_filename

from app.core.constants import ALLOWED_IMAGE_EXTENSIONS

BASE_DIR = Path(__file__).resolve().parents[2]
PRODUCT_IMAGES_DIR = Path(os.getenv("PRODUCT_IMAGES_DIR", str(BASE_DIR / "static" / "images" / "products")))


class ImageStore:
    def __init__(self, destination_dir: Path | None = None) -> None:
        self.destination_dir = destination_dir or PRODUCT_IMAGES_DIR
        self.destination_dir.mkdir(parents=True, exist_ok=True)

    def save_product_image(self, file_storage: Any) -> str | None:
        if file_storage is None or not getattr(file_storage, "filename", None):
            return None

        filename = secure_filename(file_storage.filename)
        if not filename:
            return None

        ext = Path(filename).suffix.lower()
        if ext not in ALLOWED_IMAGE_EXTENSIONS:
            return None

        unique_name = f"product_{int(time.time())}_{filename}"
        dest = self.destination_dir / unique_name
        file_storage.save(dest)
        return str(Path("images") / "products" / unique_name)
