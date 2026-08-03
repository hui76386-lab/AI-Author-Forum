from wagtail import blocks
from wagtail.contrib.table_block.blocks import TableBlock
from wagtail.documents.blocks import DocumentChooserBlock
from wagtail.images.blocks import ImageChooserBlock


class ParagraphBlock(blocks.RichTextBlock):
    def __init__(self, **kwargs):
        kwargs.setdefault(
            "features",
            [
                "h2",
                "h3",
                "h4",
                "bold",
                "italic",
                "ol",
                "ul",
                "hr",
                "link",
            ],
        )
        kwargs.setdefault(
            "help_text",
            "直接输入和排版正文，可使用标题、加粗、列表和链接。",
        )
        super().__init__(**kwargs)

    class Meta:
        icon = "pilcrow"
        label = "正文段落（可视化编辑）"


class HeadingBlock(blocks.CharBlock):
    def __init__(self, **kwargs):
        kwargs.setdefault("max_length", 160)
        kwargs.setdefault("help_text", "用于文章正文中的章节标题。")
        super().__init__(**kwargs)

    class Meta:
        icon = "title"
        label = "章节标题"


class ImageBlock(blocks.StructBlock):
    image = ImageChooserBlock(label="图片")
    alt_text = blocks.CharBlock(
        required=False,
        max_length=255,
        label="替代文本",
        help_text="用于无障碍访问；留空时使用图片库标题。",
    )
    caption = blocks.CharBlock(
        required=False,
        max_length=255,
        label="图片说明",
    )

    class Meta:
        icon = "image"
        label = "图片与说明"


class QuoteBlock(blocks.StructBlock):
    quote = blocks.TextBlock(label="引用内容")
    attribution = blocks.CharBlock(
        required=False,
        max_length=255,
        label="出处 / 作者",
    )

    class Meta:
        icon = "openquote"
        label = "引用"


class ListBlock(blocks.StructBlock):
    list_type = blocks.ChoiceBlock(
        choices=[
            ("unordered", "无序列表"),
            ("ordered", "有序列表"),
        ],
        default="unordered",
        label="列表类型",
    )
    items = blocks.ListBlock(
        blocks.RichTextBlock(features=["bold", "italic", "link"]),
        min_num=1,
        label="列表项",
    )

    class Meta:
        icon = "list-ul"
        label = "列表"


class ArticleTableBlock(TableBlock):
    def __init__(self, **kwargs):
        kwargs.setdefault(
            "help_text",
            "在表格中直接添加、删除和编辑行列；建议首行作为表头。",
        )
        super().__init__(**kwargs)

    class Meta:
        icon = "table"
        label = "表格"
        template = "table_block/blocks/table.html"


class DocumentBlock(blocks.StructBlock):
    document = DocumentChooserBlock(label="文档")
    link_text = blocks.CharBlock(
        required=False,
        max_length=160,
        label="链接文字",
        help_text="留空时使用文档标题。",
    )
    description = blocks.TextBlock(
        required=False,
        max_length=500,
        label="说明",
    )

    class Meta:
        icon = "doc-full"
        label = "附件 / 文档"


class AdvancedRawHTMLBlock(blocks.RawHTMLBlock):
    class Meta:
        icon = "code"
        label = "高级：Raw HTML（需权限）"
        help_text = "仅限获授权人员处理历史内容或特殊嵌入；常规文章请使用上方内容块。"


class ArticleBodyBlock(blocks.StreamBlock):
    # Keep the visual paragraph first so it is the primary choice in the block menu.
    # ArticlePageForm also inserts an empty paragraph on an unbound create form.
    paragraph = ParagraphBlock()
    heading = HeadingBlock()
    image = ImageBlock()
    quote = QuoteBlock()
    list = ListBlock()
    table = ArticleTableBlock()
    document = DocumentBlock()
    html = AdvancedRawHTMLBlock()

    class Meta:
        label = "文章正文"
