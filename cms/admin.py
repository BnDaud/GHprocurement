from django.contrib import admin
from django.utils.html import format_html
from .models import User,  Catalog

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


# -------------------------------
# Portfolio Admin
# -------------------------------



# -------------------------------
# Blog Admin
# -------------------------------
@admin.register(Catalog)
class CatalogAdmin(admin.ModelAdmin):
    list_display = [
        "name",
       "price",
       "description",
        "featured_image_tag",
       
       
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
