from django import template

register = template.Library()


@register.filter
def get_item(dictionary, key):
    """Get an item from a dictionary by key in Django templates."""
    if dictionary is None:
        return ""
    return dictionary.get(key, "")
