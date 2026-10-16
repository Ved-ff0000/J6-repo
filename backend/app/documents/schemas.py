from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.documents.models import DocumentStatus

DocumentType = Literal[
    "UNSPECIFIED",
    "LEASE",
    "AMENDMENT",
    "RENEWAL",
    "TERMINATION",
    "EXHIBIT",
    "ADDENDUM",
    "RENT_SCHEDULE",
    "OTHER",
]


class MetadataOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    document_type: str
    property_name: str | None
    external_reference: str | None
    notes: str | None


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    document_id: UUID
    organization_id: UUID
    version_number: int
    filename: str
    mime_type: str
    checksum: str
    file_size: int
    uploaded_by: UUID
    page_count: int | None
    processing_status: str
    parsed_segment_count: int | None
    page_number_kind: str | None
    scan_status: str
    created_at: datetime


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    organization_id: UUID
    title: str
    uploaded_by: UUID
    parent_document_id: UUID | None
    current_version: int
    status: DocumentStatus
    classification: str
    created_at: datetime
    updated_at: datetime
    metadata: MetadataOut
    latest_version: VersionOut


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


class DocumentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    document_type: DocumentType | None = None
    property_name: str | None = Field(default=None, max_length=200)
    external_reference: str | None = Field(default=None, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("title", "document_type")
    @classmethod
    def non_null_when_supplied(cls, value: str | None) -> str:
        if value is None:
            raise ValueError("Field cannot be null when supplied")
        return value


class UploadPolicy(BaseModel):
    max_upload_bytes: int
    allowed_extensions: list[str]
    can_upload: bool
    parsing_enabled: bool = False
    malware_scanning_enabled: bool = False
