from django.contrib import admin

from homefabric.documents.models import Document, DocumentRevision


class RevisionInline(admin.TabularInline):
    model = DocumentRevision
    extra = 0
    readonly_fields = ("revision_number", "original_filename", "mime_type", "size", "sha256",
                       "uploaded_by", "uploaded_at")
    can_delete = False


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("display_name", "category", "household", "updated_at")
    inlines = [RevisionInline]
