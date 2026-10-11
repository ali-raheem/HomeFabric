from django.contrib import admin

from homefabric.assets.models import Entity, EntityRelationship, EntityType, Tag


@admin.register(Entity)
class EntityAdmin(admin.ModelAdmin):
    list_display = ("name", "entity_type", "household", "status", "updated_at")
    list_filter = ("status", "entity_type__base_kind", "household")
    search_fields = ("name", "model_number", "serial_number")


@admin.register(EntityRelationship)
class RelationshipAdmin(admin.ModelAdmin):
    list_display = ("source", "kind", "target", "household")
    list_filter = ("kind",)


admin.site.register(EntityType)
admin.site.register(Tag)
