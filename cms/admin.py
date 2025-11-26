from django.contrib import admin
from django.utils.html import format_html
from .models import User, Portfolio, PortfolioImages, Blog

# -------------------------------
# User Admin
# -------------------------------
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = [
        "username",
        "email",
        "first_name",
        "last_name",
        "phone",
        "image_tag"
    ]
    
    def image_tag(self, obj):
        if obj.dp:
            return format_html(
                '<img src="{}" style="width:50px; height:50px; border-radius:50%;" />',
                obj.dp.url
            )
        return "—"  # Display a placeholder if no image
    image_tag.short_description = "Profile Picture"


# -------------------------------
# PortfolioImages Admin
# -------------------------------
@admin.register(PortfolioImages)
class PortfolioImagesAdmin(admin.ModelAdmin):
    list_display = ["id", "image_tag"]
    
    def image_tag(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" style="width:50px; height:50px;" />',
                obj.image.url
            )
        return "—"
    image_tag.short_description = "Portfolio Image"


# -------------------------------
# Portfolio Admin
# -------------------------------
@admin.register(Portfolio)
class PortfolioAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "id",
        "client_name",
        "category",
        "thumbnail_tag",
        "status",
        "created_at",
        "updated_at"
    ]
    filter_horizontal = ("images",)  # For ManyToManyField
    
    def thumbnail_tag(self, obj):
        if obj.thumbnail:
            return format_html(
                '<img src="{}" style="width:50px; height:50px;" />',
                obj.thumbnail.url
            )
        return "—"
    thumbnail_tag.short_description = "Thumbnail"


# -------------------------------
# Blog Admin
# -------------------------------
@admin.register(Blog)
class BlogAdmin(admin.ModelAdmin):
    list_display = [
        "title",
        "excerpt",
        "featured_image_tag",
        "status",
        "views_count",
        "created_at",
        "updated_at"
    ]
    
    def featured_image_tag(self, obj):
        if obj.featured_image:
            return format_html(
                '<img src="{}" style="width:50px; height:50px;" />',
                obj.featured_image.url
            )
        return "—"
    featured_image_tag.short_description = "Featured Image"
