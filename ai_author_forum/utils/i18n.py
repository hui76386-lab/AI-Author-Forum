from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.utils import translation

DEFAULT_LANGUAGE = "zh-hans"
ENGLISH_LANGUAGE = "en"
SUPPORTED_LANGUAGE_CODES = (DEFAULT_LANGUAGE, ENGLISH_LANGUAGE)
ENGLISH_PREFIX = "/en"

LANGUAGE_NAMES = {
    DEFAULT_LANGUAGE: {"name": "中文", "native_name": "中文"},
    ENGLISH_LANGUAGE: {"name": "English", "native_name": "English"},
}

UI_TRANSLATIONS = {
    DEFAULT_LANGUAGE: {
        "skip_to_main": "跳到主要内容",
        "home_aria": "AI Author Forum 首页",
        "view_all_journals": "浏览全部期刊",
        "menu": "菜单",
        "search": "搜索",
        "utility_navigation": "辅助导航",
        "primary_navigation": "主导航",
        "no_visible_entries": "暂无可见条目",
        "no_navigation_configured": "当前尚未配置导航栏目。",
        "search_terms": "搜索词",
        "search_placeholder": "搜索 AI Author Forum",
        "journal_scope": "期刊范围",
        "all_journals": "全部期刊",
        "language_switcher": "语言切换",
        "current_language": "当前语言",
        "explore_content": "内容探索",
        "ai_article": "AI 文章",
        "news": "新闻",
        "opinion": "观点",
        "research_analysis": "研究分析",
        "research_highlights": "研究亮点",
        "careers": "职业发展",
        "about_forum": "关于论坛",
        "forum_staff": "论坛团队",
        "forum_information": "论坛信息",
        "forum_metrics": "论坛指标",
        "editorial_policies": "编辑政策",
        "contact": "联系我们",
        "co_authoring_with_ai": "与 AI 共同署名",
        "definition_co_author_ai": "AI 共同作者定义",
        "responsibility_co_author": "共同作者责任",
        "for_readers": "读者指南",
        "how_ai_articles_produced": "AI 署名文章如何产生",
        "readers_responsibility": "读者责任",
        "browse_journals": "浏览期刊",
        "static_workflow": "固定 HTML 静态发布流程",
        "home": "首页",
        "journals": "期刊",
        "journals_az": "期刊 A-Z",
        "breadcrumb": "面包屑",
        "ai_authorship_portal": "AI 作者门户",
        "home_intro_default": "面向 AI 署名文章、AI 共同作者责任与读者指南的学术门户。",
        "explore_ai_articles": "探索 AI 文章",
        "browse_all_journals": "浏览全部期刊",
        "explore_forum": "探索论坛",
        "explore_by_focus": "按重点探索",
        "ai_article_teaser": "由人工智能参与创作的研究内容",
        "coauthor_teaser": "实践、署名与责任",
        "readers_teaser": "评估 AI 署名作品的指南",
        "featured_content": "精选内容",
        "view_all": "查看全部",
        "news_comment": "新闻与评论",
        "latest": "最新",
        "editorial": "编辑推荐",
        "content_prepared": "内容正在准备中",
        "explore_latest_ai": "探索最新 AI 文章",
        "static_editorial_collection": "静态编辑合集",
        "collections": "合集",
        "collections_intro": "精选合集连接文章、期刊和负责任的 AI 作者指南。",
        "latest_ai_article": "最新 AI 文章",
        "search_articles": "搜索文章",
        "comments": "评论",
        "article": "文章",
        "altmetric": "Altmetric",
        "altmetric_placeholder": "文章发布后将显示 Altmetric 趋势。",
        "journal_cover_placeholder": "可替换期刊封面",
        "statistics_chart_placeholder": "统计图表占位图",
        "journals_intro": "120 个期刊条目按 A-Z 顺序渲染为静态页面。后台导入可替换列表，并保留相同输出结构。",
        "journal_letters": "期刊字母索引",
        "no_active_journals": "当前没有可用的启用期刊。",
        "quick_links": "快捷链接",
        "a_journals": "A 字母期刊",
        "b_journals": "B 字母期刊",
        "c_journals": "C 字母期刊",
        "journal_home": "期刊主页",
        "journal_quick_links": "期刊快捷入口",
        "journal_intro_default": "静态子期刊页面共享 AI Author Forum 的全局导航、内容模块和文章模板。",
        "topics": "主题",
        "journal_topics": "期刊主题",
        "subtopics": "子主题",
        "no_journal_articles": "当前该期刊没有已投放文章。",
        "journal_resources": "期刊资源",
        "journal_articles": "期刊文章",
        "author_information": "作者信息",
        "corresponding_author": "通讯作者",
        "cover_image": "封面图",
        "metrics_image": "指标图",
        "recommended_topics": "推荐主题",
        "journal": "期刊",
        "advanced_filters": "高级筛选",
        "subject_keyword": "主题或关键词",
        "author": "作者",
        "ai_coauthor": "AI 共同作者",
        "apply_filters": "应用筛选",
        "clear_search": "清除搜索",
        "searchable_article": "可搜索文章",
        "searchable_articles": "可搜索文章",
        "no_matching_articles": "没有找到匹配文章。",
        "search_results": "搜索结果",
        "recommended_articles": "推荐文章",
        "enable_js_search": "启用 JavaScript 后可搜索静态文章索引；推荐文章链接仍可在上方使用。",
        "author_label": "作者",
        "ai_coauthor_label": "AI 共同作者",
        "title_keyword_author_journal": "标题、关键词、作者或期刊",
    },
    ENGLISH_LANGUAGE: {
        "skip_to_main": "Skip to main content",
        "home_aria": "AI Author Forum home",
        "view_all_journals": "View all journals",
        "menu": "Menu",
        "search": "Search",
        "utility_navigation": "Utility navigation",
        "primary_navigation": "Primary navigation",
        "no_visible_entries": "No visible entries",
        "no_navigation_configured": "No navigation columns are currently configured.",
        "search_terms": "Search terms",
        "search_placeholder": "Search AI Author Forum",
        "journal_scope": "Journal scope",
        "all_journals": "All journals",
        "language_switcher": "Language switcher",
        "current_language": "Current language",
        "explore_content": "Explore content",
        "ai_article": "AI Article",
        "news": "News",
        "opinion": "Opinion",
        "research_analysis": "Research Analysis",
        "research_highlights": "Research Highlights",
        "careers": "Careers",
        "about_forum": "About the forum",
        "forum_staff": "Forum Staff",
        "forum_information": "Forum Information",
        "forum_metrics": "Forum Metrics",
        "editorial_policies": "Editorial policies",
        "contact": "Contact",
        "co_authoring_with_ai": "Co authoring with AI",
        "definition_co_author_ai": "Definition of a co author to the AI",
        "responsibility_co_author": "Responsibility of the Co author",
        "for_readers": "For readers",
        "how_ai_articles_produced": "How AI authored Articles produced",
        "readers_responsibility": "Readers responsibility",
        "browse_journals": "Browse journals",
        "static_workflow": "Static HTML publishing workflow",
        "home": "Home",
        "journals": "Journals",
        "journals_az": "Journals A-Z",
        "breadcrumb": "Breadcrumb",
        "ai_authorship_portal": "AI authorship portal",
        "home_intro_default": "A scholarly portal for AI authored articles, AI co-author responsibility and reader guidance.",
        "explore_ai_articles": "Explore AI articles",
        "browse_all_journals": "Browse all journals",
        "explore_forum": "Explore the forum",
        "explore_by_focus": "Explore by focus",
        "ai_article_teaser": "Research authored with artificial intelligence",
        "coauthor_teaser": "Practice, attribution and responsibility",
        "readers_teaser": "Guidance for evaluating AI-authored work",
        "featured_content": "Featured Content",
        "view_all": "View all",
        "news_comment": "News & Comment",
        "latest": "Latest",
        "editorial": "Editorial",
        "content_prepared": "Content is being prepared",
        "explore_latest_ai": "Explore the latest AI articles",
        "static_editorial_collection": "Static editorial collection",
        "collections": "Collections",
        "collections_intro": "Curated collections connect articles, journals and responsible AI authorship guidance.",
        "latest_ai_article": "Latest AI Article",
        "search_articles": "Search articles",
        "comments": "Comments",
        "article": "Article",
        "altmetric": "Altmetric",
        "altmetric_placeholder": "Altmetric trends will appear as articles are published.",
        "journal_cover_placeholder": "Replaceable journal cover",
        "statistics_chart_placeholder": "Statistics chart placeholder",
        "journals_intro": "120 journal entries are rendered as static pages in A-Z order. Backend import can replace this list while retaining the same output structure.",
        "journal_letters": "Journal letters",
        "no_active_journals": "No active journals are currently available.",
        "quick_links": "Quick links",
        "a_journals": "A journals",
        "b_journals": "B journals",
        "c_journals": "C journals",
        "journal_home": "Journal home",
        "journal_quick_links": "Journal quick links",
        "journal_intro_default": "A static sub-journal page sharing the global AI Author Forum navigation, content modules and article templates.",
        "topics": "Topics",
        "journal_topics": "Journal topics",
        "subtopics": "subtopics",
        "no_journal_articles": "No placed articles are currently available for this journal.",
        "journal_resources": "Journal resources",
        "journal_articles": "Journal articles",
        "author_information": "Author information",
        "corresponding_author": "Corresponding author",
        "cover_image": "cover image",
        "metrics_image": "metrics image",
        "recommended_topics": "Recommended topics",
        "journal": "Journal",
        "advanced_filters": "Advanced filters",
        "subject_keyword": "Subject or keyword",
        "author": "Author",
        "ai_coauthor": "AI co-author",
        "apply_filters": "Apply filters",
        "clear_search": "Clear search",
        "searchable_article": "searchable article",
        "searchable_articles": "searchable articles",
        "no_matching_articles": "No matching articles found.",
        "search_results": "Search results",
        "recommended_articles": "Recommended articles",
        "enable_js_search": "Enable JavaScript to search the static article index. Recommended article links remain available above.",
        "author_label": "Author",
        "ai_coauthor_label": "AI co-author",
        "by": "By",
        "full_text": "Full text",
        "author_search": "Author search",
        "search_ai_coauthor": "Search AI co-author",
        "ai_coauthor_short": "AI co-author",
        "reprints_permissions": "Reprints and permissions",
        "view_author_publications": "View author publications",
        "email": "Email",
        "article_visual_credit": "visual. Credit: AI Author Forum upload area",
        "download_document": "Download document",
        "no_article_body": "No article body has been added yet.",
        "ai_authorship_declaration": "AI authorship declaration",
        "methods_responsibility": "Methods and responsibility",
        "methods_responsibility_text": "This article page records human responsibility, AI contribution, editorial verification and reader-facing context within the shared publishing workflow.",
        "related_articles": "Related Articles",
        "explore_related": "Explore related AI authorship recommendations",
        "subjects": "Subjects",
        "latest_on": "Latest on:",
        "current_issue": "Current issue",
        "browse_issues": "Browse issues",
        "no_issue_articles": "No articles are currently placed in this issue.",
        "no_archived_issues": "No archived issues are available.",
        "column_filters": "Column filters",
        "article_type": "Article type",
        "all_types": "All types",
        "year": "Year",
        "all_years": "All years",
        "featured": "Featured",
        "articles": "Articles",
        "previous": "Previous",
        "next": "Next",
        "pagination": "Pagination",
        "page_x_of_y": "Page {page} of {pages}",
        "recommended": "Recommended",
        "related_links": "Related links",
        "static_recommendations": "Static recommendations",
        "forum_information_link": "Forum information",
        "backend_editable_area": "Backend editable area",
        "section_intro_fallback": "This content area is connected to the editorial placement workflow and can be replaced by managed rich text, policy copy or reader guidance.",
        "section_placeholder_text": "Simulated content is shown until final editorial material is supplied. Only reviewed and placed articles should appear in the live listing below.",
        "what_page_contains": "What this page will contain",
        "placeholder_content_block": "Placeholder content block",
        "top_story": "Top story",
        "section_listing": "listing",
        "no_section_articles": "No articles are currently placed in this section.",
        "curated_static_recommendations": "Curated static recommendations and placeholder cards remain visible for page acceptance.",
        "content_pending_final_copy": "Content pending final editorial copy",
        "static_info_confirmation": "This simulated page confirms the menu target, fixed URL, breadcrumb, content zones, and static HTML publishing path. Administrators can replace this placeholder with approved rich text and media later.",
        "page_content_model": "Page content model",
        "placeholder_block_final_copy": "Placeholder block for final copy, links, images, or policy notes.",
        "editorial_notes": "Editorial notes",
        "static_page": "Static page",
        "generated_fixed_html": "Generated as fixed HTML",
        "fixed_html_manifest": "The public front end should serve this page from the static release manifest.",
        "controlled_content": "Controlled content",
        "safe_placeholder_area": "Safe placeholder area",
        "safe_placeholder_text": "Copy can be replaced by reviewed rich text without adding runtime search or free-form layout dragging.",
        "audit_scope": "Audit scope",
        "future_edits_traceable": "Future edits are traceable",
        "audit_scope_text": "High-risk publication, retry, rollback, and navigation changes should remain auditable.",
        "no_category_articles": "No published articles are currently available in this category.",
        "journal_cover_alt": "Journal cover placeholder",
        "journal_statistics_chart": "Journal statistics chart",
        "statistics_chart": "Statistics chart",
        "search_index_unavailable": "Search index is unavailable",
        "static_search_index_load_failed": "The static search index could not be loaded.",
        "search_results_for": '{count} result(s) for "{query}"',
        "search_results_count": "{count} result(s)",
        "title_keyword_author_journal": "Title, keyword, author, or journal",
    },
}


UI_TRANSLATIONS[DEFAULT_LANGUAGE].update(
    {
        "ai_authorship_declaration": "AI \u7f72\u540d\u58f0\u660e",
        "ai_coauthor_short": "AI \u5171\u540c\u4f5c\u8005",
        "all_articles": "\u5168\u90e8\u6587\u7ae0",
        "all_types": "\u5168\u90e8\u7c7b\u578b",
        "all_years": "\u5168\u90e8\u5e74\u4efd",
        "article_actions": "\u6587\u7ae0\u64cd\u4f5c",
        "article_url": "\u6587\u7ae0\u94fe\u63a5",
        "article_type": "\u6587\u7ae0\u7c7b\u578b",
        "article_visual_credit": "\u914d\u56fe\u3002\u6765\u6e90\uff1aAI Author Forum \u4e0a\u4f20\u533a",
        "articles": "\u6587\u7ae0",
        "audit_scope": "\u5ba1\u8ba1\u8303\u56f4",
        "audit_scope_text": "\u540e\u7eed\u7f16\u8f91\u3001\u6295\u653e\u548c\u9759\u6001\u53d1\u5e03\u52a8\u4f5c\u4f1a\u4fdd\u7559\u5ba1\u8ba1\u8bb0\u5f55\u3002",
        "author_declaration": "\u4f5c\u8005\u58f0\u660e",
        "author_search": "\u4f5c\u8005\u641c\u7d22",
        "backend_editable_area": "\u540e\u53f0\u53ef\u7f16\u8f91\u533a\u57df",
        "browse_issues": "\u6d4f\u89c8\u671f\u53f7",
        "by": "\u4f5c\u8005",
        "cancel": "\u53d6\u6d88",
        "cancel_verification": "\u53d6\u6d88\u90ae\u7bb1\u9a8c\u8bc1",
        "column_filters": "\u680f\u76ee\u7b5b\u9009",
        "content_pending_final_copy": "\u6b63\u5f0f\u6587\u6848\u5f85\u8865\u5145",
        "controlled_content": "\u53d7\u63a7\u5185\u5bb9",
        "curated_static_recommendations": "\u9759\u6001\u63a8\u8350\u5185\u5bb9",
        "current_issue": "\u5f53\u524d\u671f\u53f7",
        "copy_link": "\u590d\u5236\u94fe\u63a5",
        "download_document": "\u4e0b\u8f7d\u6587\u6863",
        "download_pdf": "\u4e0b\u8f7d PDF",
        "editorial_notes": "\u7f16\u8f91\u8bf4\u660e",
        "email": "\u90ae\u7bb1",
        "email_verification": "\u90ae\u7bb1\u9a8c\u8bc1",
        "explore_related": "\u6d4f\u89c8\u76f8\u5173 AI \u7f72\u540d\u63a8\u8350",
        "featured": "\u7cbe\u9009",
        "fixed_html_manifest": "\u56fa\u5b9a HTML \u6e05\u5355",
        "forum_information_link": "\u8bba\u575b\u4fe1\u606f",
        "full_text": "\u5168\u6587",
        "future_edits_traceable": "\u540e\u7eed\u7f16\u8f91\u53ef\u8ffd\u6eaf",
        "generated_fixed_html": "\u751f\u6210\u56fa\u5b9a HTML",
        "issue_detail": "\u67e5\u770b\u671f\u53f7\u8be6\u60c5",
        "issue_navigation": "\u671f\u53f7\u5bfc\u822a",
        "journal_cover_alt": "\u671f\u520a\u5c01\u9762",
        "journal_statistics_chart": "\u671f\u520a\u7edf\u8ba1\u56fe\u8868",
        "latest_on": "\u6700\u65b0\u4e3b\u9898\uff1a",
        "methods_responsibility": "\u65b9\u6cd5\u4e0e\u8d23\u4efb",
        "methods_responsibility_text": "\u672c\u6587\u9875\u9762\u5728\u7edf\u4e00\u53d1\u5e03\u6d41\u7a0b\u4e2d\u8bb0\u5f55\u4eba\u5de5\u8d23\u4efb\u3001AI \u8d21\u732e\u3001\u7f16\u8f91\u6838\u9a8c\u548c\u9762\u5411\u8bfb\u8005\u7684\u4e0a\u4e0b\u6587\u3002",
        "more_news": "\u66f4\u591a\u65b0\u95fb",
        "newer_issue": "\u8f83\u65b0\u671f\u53f7",
        "next": "\u4e0b\u4e00\u9875",
        "no_archived_issues": "\u6682\u65e0\u5df2\u5f52\u6863\u671f\u53f7\u3002",
        "no_article_body": "\u5c1a\u672a\u6dfb\u52a0\u6587\u7ae0\u6b63\u6587\u3002",
        "no_category_articles": "\u8be5\u680f\u76ee\u6682\u65e0\u5df2\u6295\u653e\u6587\u7ae0\u3002",
        "no_issue_articles": "\u8be5\u671f\u53f7\u6682\u65e0\u6587\u7ae0\u3002",
        "no_section_articles": "\u8be5\u7248\u5757\u6682\u65e0\u5df2\u6295\u653e\u6587\u7ae0\u3002",
        "older_issue": "\u8f83\u65e9\u671f\u53f7",
        "page_content_model": "\u9875\u9762\u5185\u5bb9\u6a21\u578b",
        "page_x_of_y": "\u7b2c {page} \u9875\uff0c\u5171 {pages} \u9875",
        "pagination": "\u5206\u9875",
        "pairing_code": "\u7535\u8111\u914d\u5bf9\u7801",
        "placeholder_block_final_copy": "\u5360\u4f4d\u5185\u5bb9\u4f1a\u5728\u6700\u7ec8\u7f16\u8f91\u6750\u6599\u63d0\u4f9b\u540e\u66ff\u6362\u3002",
        "placeholder_content_block": "\u5360\u4f4d\u5185\u5bb9\u533a\u5757",
        "previous": "\u4e0a\u4e00\u9875",
        "previous_issue": "\u5f80\u671f\u671f\u53f7",
        "recommended": "\u63a8\u8350",
        "related_articles": "\u76f8\u5173\u6587\u7ae0",
        "related_links": "\u76f8\u5173\u94fe\u63a5",
        "reprints_permissions": "\u8f6c\u8f7d\u4e0e\u6388\u6743",
        "resend_verification": "\u91cd\u65b0\u53d1\u9001",
        "research_articles": "\u7814\u7a76\u6587\u7ae0",
        "safe_placeholder_area": "\u5b89\u5168\u5360\u4f4d\u533a\u57df",
        "safe_placeholder_text": "\u8fd9\u91cc\u5c55\u793a\u6a21\u62df\u5185\u5bb9\uff0c\u76f4\u5230\u63a5\u5165\u6700\u7ec8\u7f16\u8f91\u6750\u6599\u3002\u4e0a\u7ebf\u5217\u8868\u53ea\u5e94\u51fa\u73b0\u5df2\u5ba1\u6838\u5e76\u5b8c\u6210\u6295\u653e\u7684\u6587\u7ae0\u3002",
        "search_ai_coauthor": "\u641c\u7d22 AI \u5171\u540c\u4f5c\u8005",
        "search_index_unavailable": "\u641c\u7d22\u7d22\u5f15\u4e0d\u53ef\u7528",
        "search_results_count": "{count} \u6761\u7ed3\u679c",
        "search_results_for": "\u5305\u542b\u201c{query}\u201d\u7684 {count} \u6761\u7ed3\u679c",
        "send_verification_link": "\u53d1\u9001\u9a8c\u8bc1\u94fe\u63a5",
        "section_intro_fallback": "\u8be5\u5185\u5bb9\u533a\u57df\u5df2\u63a5\u5165\u7f16\u8f91\u6295\u653e\u6d41\u7a0b\uff0c\u53ef\u66ff\u6362\u4e3a\u53d7\u7ba1\u7406\u7684\u5bcc\u6587\u672c\u3001\u653f\u7b56\u8bf4\u660e\u6216\u8bfb\u8005\u6307\u5357\u3002",
        "section_listing": "\u680f\u76ee\u5217\u8868",
        "section_placeholder_text": "\u5728\u6700\u7ec8\u7f16\u8f91\u5185\u5bb9\u63d0\u4f9b\u524d\u663e\u793a\u6a21\u62df\u5185\u5bb9\u3002\u4e0a\u7ebf\u5217\u8868\u53ea\u5e94\u51fa\u73b0\u5df2\u5ba1\u6838\u5e76\u5b8c\u6210\u6295\u653e\u7684\u6587\u7ae0\u3002",
        "share": "\u5206\u4eab",
        "static_info_confirmation": "\u8be5\u9875\u9762\u5c06\u4f5c\u4e3a\u56fa\u5b9a HTML \u8f93\u51fa\uff0c\u5e76\u7eb3\u5165\u9759\u6001\u53d1\u5e03\u6e05\u5355\u3002",
        "static_page": "\u9759\u6001\u9875\u9762",
        "static_recommendations": "\u9759\u6001\u63a8\u8350",
        "static_search_index_load_failed": "\u65e0\u6cd5\u52a0\u8f7d\u9759\u6001\u641c\u7d22\u7d22\u5f15\u3002",
        "statistics_chart": "\u7edf\u8ba1\u56fe\u8868",
        "subjects": "\u4e3b\u9898",
        "table_of_contents": "\u76ee\u5f55",
        "top_story": "\u5934\u6761",
        "view_author_publications": "\u67e5\u770b\u4f5c\u8005\u53d1\u8868\u5185\u5bb9",
        "waiting_for_email_link": "\u6253\u5f00\u9a8c\u8bc1\u90ae\u4ef6\uff0c\u6b64\u7535\u8111\u5c06\u81ea\u52a8\u89e3\u9501\u3002",
        "waiting_for_phone": "\u7b49\u5f85\u6253\u5f00\u9a8c\u8bc1\u90ae\u4ef6\uff0c\u6b64\u7535\u8111\u5c06\u81ea\u52a8\u89e3\u9501\u3002",
        "what_page_contains": "\u672c\u9875\u5c06\u5305\u542b",
        "year": "\u5e74\u4efd",
    }
)

UI_TRANSLATIONS[ENGLISH_LANGUAGE].update(
    {
        "all_articles": "All articles",
        "article_actions": "Article actions",
        "article_url": "Article URL",
        "author_declaration": "Author declaration",
        "cancel": "Cancel",
        "cancel_verification": "Cancel verification",
        "copy_link": "Copy link",
        "download_pdf": "Download PDF",
        "email_verification": "Email verification",
        "issue_detail": "View issue details",
        "issue_navigation": "Issue navigation",
        "more_news": "More news",
        "newer_issue": "Newer issue",
        "older_issue": "Older issue",
        "pairing_code": "Computer pairing code",
        "previous_issue": "Previous issue",
        "resend_verification": "Resend",
        "research_articles": "Research articles",
        "send_verification_link": "Send verification link",
        "share": "Share",
        "table_of_contents": "Table of contents",
        "waiting_for_email_link": "Open the verification email to unlock this computer automatically.",
        "waiting_for_phone": "Waiting for the email link. This computer will unlock automatically.",
    }
)


ARTICLE_TYPE_TRANSLATION_KEYS = {
    "AI Article": "ai_article",
    "AI 文章": "ai_article",
    "ai_article": "ai_article",
    "News": "news",
    "新闻": "news",
    "news": "news",
    "Opinion": "opinion",
    "观点": "opinion",
    "opinion": "opinion",
    "Research Analysis": "research_analysis",
    "研究分析": "research_analysis",
    "research_analysis": "research_analysis",
    "Editorial": "editorial",
    "编辑推荐": "editorial",
    "editorial": "editorial",
}


def normalize_language(language_code: str | None = None) -> str:
    code = (
        (language_code or translation.get_language() or settings.LANGUAGE_CODE or "")
        .lower()
        .replace("_", "-")
    )
    if code.startswith("zh"):
        return DEFAULT_LANGUAGE
    if code.startswith("en"):
        return ENGLISH_LANGUAGE
    return DEFAULT_LANGUAGE


def is_public_language_prefix(prefix: str) -> bool:
    return prefix.lower() == ENGLISH_LANGUAGE


def strip_public_language_prefix(path: str) -> str:
    raw_path = path or "/"
    if not raw_path.startswith("/"):
        raw_path = f"/{raw_path}"
    parts = raw_path.split("/", 2)
    if len(parts) >= 2 and is_public_language_prefix(parts[1]):
        suffix = parts[2] if len(parts) == 3 else ""
        return f"/{suffix}" if suffix else "/"
    return raw_path


def localize_path(path: str, language_code: str | None = None) -> str:
    if not path:
        path = "/"
    split = urlsplit(str(path))
    # Keep absolute external URLs unchanged.
    if split.scheme or split.netloc:
        return str(path)
    normalized_language = normalize_language(language_code)
    clean_path = strip_public_language_prefix(split.path or "/")
    if normalized_language == ENGLISH_LANGUAGE:
        localized = f"{ENGLISH_PREFIX}{clean_path}"
        if clean_path == "/":
            localized = f"{ENGLISH_PREFIX}/"
    else:
        localized = clean_path
    query = split.query
    return urlunsplit(("", "", localized, query, split.fragment))


def localized_output_path(output_path: str, language_code: str | None = None) -> str:
    path = output_path or "index.html"
    path_url = f"/{path}" if not path.startswith("/") else path
    if path_url.endswith("/index.html"):
        path_url = path_url[: -len("index.html")]
    return localize_path(path_url, language_code)


def language_switch_options(
    path: str, query_string: str = ""
) -> list[dict[str, object]]:
    current_language = normalize_language()
    query = (
        query_string.decode()
        if isinstance(query_string, bytes)
        else (query_string or "")
    )
    query_pairs = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key != "language"
    ]
    suffix = f"?{urlencode(query_pairs)}" if query_pairs else ""
    options = []
    for code in SUPPORTED_LANGUAGE_CODES:
        labels = LANGUAGE_NAMES[code]
        options.append(
            {
                "code": code,
                "name": labels["name"],
                "native_name": labels["native_name"],
                "url": f"{localize_path(path, code)}{suffix}",
                "active": code == current_language,
            }
        )
    return options


def ui_label(
    key: str, language_code: str | None = None, default: str | None = None
) -> str:
    code = normalize_language(language_code)
    return UI_TRANSLATIONS.get(code, {}).get(key) or default or key


def article_type_label(
    article_type: str | None,
    language_code: str | None = None,
    default: str | None = None,
) -> str:
    """Return the public article-type label for the active language."""
    value = str(article_type or "").strip()
    key = ARTICLE_TYPE_TRANSLATION_KEYS.get(value)
    if not key:
        return default or value
    return ui_label(key, language_code, default or value)


def localized_journal_name(journal, language_code: str | None = None) -> str:
    """Return the journal name appropriate for the active public language."""
    if not journal:
        return ""
    if normalize_language(language_code) == ENGLISH_LANGUAGE:
        return str(
            getattr(journal, "name", "")
            or getattr(journal, "name_cn", "")
            or getattr(journal, "slug", "")
        )
    return str(
        getattr(journal, "name_cn", "")
        or getattr(journal, "name", "")
        or getattr(journal, "slug", "")
    )
