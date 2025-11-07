from django.contrib import admin
from .models import User , Portfolio , PortfolioImages,Blog
# Register your models here

class UserAdmin(admin.ModelAdmin):
    list_display = ["username" , 
                    "email" , 
                    "first_name",
                    "last_name",
                    "phone",
                    "dp"
                    ]


class PorfolioImagesAdmin(admin.ModelAdmin):
    list_display =["image"]


class PortfolioAdmin(admin.ModelAdmin):
    list_display = ["title",
                    "id",
                    "client_name",
                    "description",
                    "created_at",
                    "updated_at",
                   
                    ]
   


class BlogAdmin(admin.ModelAdmin):
    list_display = ["title" ,
                    "excerpt",
                    "featured_image",
                    "views_count"]


admin.site.register(User , UserAdmin)
admin.site.register(Portfolio , PortfolioAdmin)
admin.site.register(PortfolioImages , PorfolioImagesAdmin)
admin.site.register(Blog , BlogAdmin)
