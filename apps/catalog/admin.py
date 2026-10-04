from django.contrib import admin
from simple_history.admin import SimpleHistoryAdmin

from .models import Category, Product, ProductGroup, Unit


@admin.register(ProductGroup)
class ProductGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "sort_order", "is_active")
    list_filter = ("organization", "is_active")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "group", "organization", "sort_order", "is_active")
    list_filter = ("organization", "group", "is_active")


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("name", "organization")
    list_filter = ("organization",)


@admin.register(Product)
class ProductAdmin(SimpleHistoryAdmin):
    list_display = ("name", "category", "unit", "sort_order", "is_active")
    list_filter = ("organization", "category__group", "category", "is_active")
    search_fields = ("name",)
