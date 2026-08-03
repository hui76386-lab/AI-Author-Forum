from wagtail import blocks

from .validators import validate_public_link


class HeroQuickLinkBlock(blocks.StructBlock):
    label = blocks.CharBlock(max_length=60, label="显示文字")
    url = blocks.CharBlock(
        max_length=500,
        label="链接地址",
        validators=[validate_public_link],
    )
    open_in_new_tab = blocks.BooleanBlock(
        required=False,
        label="在新窗口打开",
    )

    class Meta:
        label = "首页快捷入口"
        icon = "link"
