"""Durable overlay store for /upload/* evidence.

Uploads are folded into the next /analyse call, and are also written to a
small JSON file so a backend restart does not erase analyst-supplied GST,
bank, or parsed AOC-4 evidence. This keeps the simple no-Redis deployment
model while avoiding process-local amnesia.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.app.config import get_settings
from backend.app.ingest.gst import RawGSTEntity
from backend.app.ingest.schemas import CompanyBundle, RawFinancialStatement
from backend.app.parse.pdf_parser import PDFForensics

logger = logging.getLogger(__name__)


@dataclass
class CompanyUploadOverlay:
    """Whatever the user has uploaded for a single CIN. All optional."""

    extra_financials: list[RawFinancialStatement] = field(default_factory=list)
    forensics: list[PDFForensics] = field(default_factory=list)
    gst_entity: RawGSTEntity | None = None
    bank_credits_total: float | None = None
    has_gst_upload: bool = False
    has_bank_upload: bool = False

    def merge_into(self, bundle: CompanyBundle) -> CompanyBundle:
        """Return a new CompanyBundle with overlay rows folded in."""
        existing_keys = {(f.cin, f.year) for f in self.extra_financials}
        merged_fs = [f for f in bundle.financials if (f.cin, f.year) not in existing_keys]
        merged_fs.extend(self.extra_financials)
        return bundle.model_copy(update={
            "financials": sorted(merged_fs, key=lambda f: f.year),
            "has_gst_upload": bundle.has_gst_upload or self.has_gst_upload,
            "has_bank_upload": bundle.has_bank_upload or self.has_bank_upload,
        })


def _dt_from_json(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _forensics_to_json(item: PDFForensics) -> dict[str, Any]:
    raw = asdict(item)
    for key in ("created_at", "modified_at"):
        if raw[key] is not None:
            raw[key] = raw[key].isoformat()
    return raw


def _forensics_from_json(raw: dict[str, Any]) -> PDFForensics:
    return PDFForensics(
        pdf_creation_software=raw.get("pdf_creation_software"),
        created_at=_dt_from_json(raw.get("created_at")),
        modified_at=_dt_from_json(raw.get("modified_at")),
        creation_mod_gap_days=raw.get("creation_mod_gap_days"),
        page_count=int(raw.get("page_count") or 0),
        distinct_fonts=int(raw.get("distinct_fonts") or 0),
        dpi_inconsistency=bool(raw.get("dpi_inconsistency", False)),
    )


def _overlay_to_json(overlay: CompanyUploadOverlay) -> dict[str, Any]:
    return {
        "extra_financials": [f.model_dump(mode="json") for f in overlay.extra_financials],
        "forensics": [_forensics_to_json(f) for f in overlay.forensics],
        "gst_entity": overlay.gst_entity.model_dump(mode="json") if overlay.gst_entity else None,
        "bank_credits_total": overlay.bank_credits_total,
        "has_gst_upload": overlay.has_gst_upload,
        "has_bank_upload": overlay.has_bank_upload,
    }


def _overlay_from_json(raw: dict[str, Any]) -> CompanyUploadOverlay:
    return CompanyUploadOverlay(
        extra_financials=[RawFinancialStatement.model_validate(x) for x in raw.get("extra_financials", [])],
        forensics=[_forensics_from_json(x) for x in raw.get("forensics", [])],
        gst_entity=(RawGSTEntity.model_validate(raw["gst_entity"]) if raw.get("gst_entity") else None),
        bank_credits_total=raw.get("bank_credits_total"),
        has_gst_upload=bool(raw.get("has_gst_upload", False)),
        has_bank_upload=bool(raw.get("has_bank_upload", False)),
    )


class UploadStore:
    """CIN -> upload overlay map with JSON persistence."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or Path(get_settings().upload_overlay_path)
        self._overlays: dict[str, CompanyUploadOverlay] = {}
        self._loaded = False

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._overlays = {
                cin: _overlay_from_json(payload)
                for cin, payload in raw.get("overlays", {}).items()
            }
        except Exception as exc:  # noqa: BLE001 - corrupt cache should not break analysis
            logger.warning("upload overlay load failed from %s: %s", self._path, exc)
            self._overlays = {}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "overlays": {cin: _overlay_to_json(overlay) for cin, overlay in self._overlays.items()},
        }
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self._path)

    def get(self, cin: str) -> CompanyUploadOverlay:
        self._ensure_loaded()
        return self._overlays.get(cin) or CompanyUploadOverlay()

    def upsert_financials(self, cin: str, statement: RawFinancialStatement, forensics: PDFForensics) -> CompanyUploadOverlay:
        self._ensure_loaded()
        overlay = self._overlays.setdefault(cin, CompanyUploadOverlay())
        overlay.extra_financials = [f for f in overlay.extra_financials if f.year != statement.year] + [statement]
        overlay.forensics.append(forensics)
        self._save()
        return overlay

    def upsert_gst(self, cin: str, entity: RawGSTEntity) -> CompanyUploadOverlay:
        self._ensure_loaded()
        overlay = self._overlays.setdefault(cin, CompanyUploadOverlay())
        overlay.gst_entity = entity
        overlay.has_gst_upload = True
        self._save()
        return overlay

    def upsert_bank(self, cin: str, total_credits: float) -> CompanyUploadOverlay:
        self._ensure_loaded()
        overlay = self._overlays.setdefault(cin, CompanyUploadOverlay())
        overlay.bank_credits_total = total_credits
        overlay.has_bank_upload = True
        self._save()
        return overlay

    def reset(self) -> None:
        self._overlays.clear()
        self._loaded = True
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete upload overlay file %s", self._path)


_UPLOAD_STORE = UploadStore()


def get_upload_store() -> UploadStore:
    return _UPLOAD_STORE