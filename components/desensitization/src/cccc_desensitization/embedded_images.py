from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

from docx import Document
from docx.opc.constants import RELATIONSHIP_TYPE
from PIL import Image


def resources(document: Any) -> list[tuple[str, Any, list[str]]]:
    found: dict[str, tuple[str, Any, list[str]]] = {}
    for part in document.part.package.parts:
        for rel in part.rels.values():
            if rel.reltype != RELATIONSHIP_TYPE.IMAGE:
                continue
            key = f"{part.partname}:{rel.rId}" if rel.is_external else str(rel.target_part.partname)
            image_id = hashlib.sha256(key.encode()).hexdigest()[:32]
            entry = found.setdefault(key, (image_id, None if rel.is_external else rel.target_part, []))
            entry[2].append(f"{part.partname}:{rel.rId}")
    return list(found.values())


def warning(image_id: str, locations: list[str], code: str) -> dict[str, str]:
    return {"code": code, "image_id": image_id,
            "message": f"{', '.join(locations)}: {code}; original image retained"}


def analyze_images(data: bytes, folder: Path, analyze: Any) -> tuple[list[dict], list[dict]]:
    images, warnings = [], []
    for image_id, part, locations in resources(Document(io.BytesIO(data))):
        item: dict[str, Any] = {"image_id": image_id, "locations": locations, "status": "failed",
                                "entities": [], "boxes": []}
        code = "DOCX_EXTERNAL_IMAGE" if part is None else "DOCX_IMAGE_FAILED"
        if part is not None:
            try:
                if Path(str(part.partname)).suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}:
                    raise ValueError("unsupported image")
                with Image.open(io.BytesIO(part.blob)) as image:
                    if image.width * image.height > 40_000_000 or getattr(image, "n_frames", 1) > 1:
                        raise ValueError("image limit")
                width, height, boxes, entities, issues = analyze(part.blob, folder)
                item.update(image_width=width, image_height=height, boxes=boxes, entities=entities,
                            status="partial" if issues else "ready")
                if issues:
                    code = "DOCX_IMAGE_OCR_FAILED"
                else:
                    code = ""
            except Exception:
                pass
        if code:
            warnings.append(warning(image_id, locations, code))
        images.append(item)
    return images, warnings


def review_images(server: list[dict], submitted: list[dict] | None, review: Any, validate: Any, sync: Any) -> list[dict]:
    if submitted is None:
        submitted = server
    registered = {item["image_id"]: item for item in server}
    seen = set()
    result = []
    for item in submitted:
        image_id = item.get("image_id")
        if image_id not in registered or image_id in seen:
            raise ValueError("invalid embedded image identity")
        seen.add(image_id)
        original = registered[image_id]
        known_ids = {entity.get("id") for entity in original["entities"]}
        if any(entity.get("id") not in known_ids for entity in item.get("entities", original["entities"])):
            raise ValueError("entity does not belong to embedded image")
        entities = review(original["entities"], item.get("entities", original["entities"]))
        boxes = validate(item.get("boxes", original["boxes"]))
        if any(box.get("page", 1) != 1 for box in boxes):
            raise ValueError("invalid embedded image page")
        originals = {box.get("id"): box for box in original["boxes"]}
        for box in boxes:
            before = originals.get(box.get("id"))
            if before is None:
                continue
            old_coordinates = before.get("bbox", before)
            if any(box[key] != old_coordinates.get(key) for key in ("x", "y", "width", "height")):
                for entity in entities:
                    if box.get("id") in entity.get("box_ids", []):
                        entity["bbox"] = {key: box[key] for key in ("x", "y", "width", "height")}
        entities, boxes = sync(entities, boxes)
        result.append({**original, "entities": entities, "boxes": boxes})
    if seen != set(registered):
        raise ValueError("incomplete embedded image review")
    return result


def mask_images(data: bytes, images: list[dict], policies: dict, mask: Any) -> tuple[bytes, list[dict]]:
    document = Document(io.BytesIO(data))
    registered = {image_id: (part, locations) for image_id, part, locations in resources(document)}
    warnings = []
    for item in images:
        image_id = item["image_id"]
        if image_id not in registered:
            raise ValueError("embedded image missing from document")
        part, locations = registered[image_id]
        if part is None or item["status"] == "failed":
            continue
        if not any(value.get("selected", True) for value in [*item["entities"], *item["boxes"]]):
            continue
        try:
            part._blob = mask(part.blob, str(part.partname), "", item["entities"], policies, item["boxes"])
        except Exception:
            warnings.append(warning(image_id, locations, "DOCX_IMAGE_MASK_FAILED"))
    output = io.BytesIO()
    document.save(output)
    return output.getvalue(), warnings
