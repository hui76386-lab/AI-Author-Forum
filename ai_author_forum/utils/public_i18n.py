"""Localized presentation values for the public static front end.

Imported test content is authored in English, while the public site exposes a
Chinese default locale.  These helpers keep the source records canonical and
apply reviewed Chinese presentation text only when the Chinese site is being
rendered.  Unrecognised editorial content deliberately falls back to its
source value instead of pretending that it has been translated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import escape

from django.utils.safestring import mark_safe
from django.utils.translation import get_language

from .i18n import ENGLISH_LANGUAGE, normalize_language


ARTICLE_THEME_TRANSLATIONS = {
    "Research Foundations and Open Questions": "研究基础与开放问题",
    "Benchmark Design for Reliable Progress": "面向可靠进展的基准设计",
    "Robust Evaluation Under Distribution Shift": "分布偏移下的稳健评估",
    "Dataset and Evidence Design": "数据集与证据设计",
    "Scalable System Architecture": "可扩展系统架构",
    "Safety, Reliability, and Failure Analysis": "安全性、可靠性与故障分析",
    "Human-AI Collaboration in Practice": "人机协作实践",
    "Reproducibility and Responsible Governance": "可复现性与负责任治理",
    "Real-World Deployment Lessons": "真实场景部署经验",
    "Future Directions and Research Roadmap": "未来方向与研究路线图",
}

ARTICLE_KEYWORD_TRANSLATIONS = {
    "foundations": "研究基础",
    "research agenda": "研究议程",
    "open questions": "开放问题",
    "benchmarks": "基准测试",
    "measurement": "测量",
    "comparative evaluation": "比较评估",
    "robustness": "稳健性",
    "evaluation": "评估",
    "distribution shift": "分布偏移",
    "datasets": "数据集",
    "evidence quality": "证据质量",
    "data governance": "数据治理",
    "systems": "系统",
    "scalability": "可扩展性",
    "architecture": "架构",
    "safety": "安全性",
    "reliability": "可靠性",
    "failure analysis": "故障分析",
    "human-ai collaboration": "人机协作",
    "workflows": "工作流",
    "usability": "可用性",
    "reproducibility": "可复现性",
    "governance": "治理",
    "transparency": "透明度",
    "future directions": "未来方向",
    "roadmap": "路线图",
    "emerging methods": "新兴方法",
    "import test": "导入测试",
}

NAVIGATION_GROUP_TRANSLATIONS = {
    "explore-content": "内容探索",
    "journals": "期刊",
    "about-the-forum": "关于论坛",
    "co-authoring-with-ai": "与 AI 共同署名",
    "for-readers": "读者指南",
    "about-this-journal": "关于本期刊",
    "publish-with-us": "与我们合作发表",
}

NAVIGATION_ITEM_TRANSLATIONS = {
    "ai-article": "AI 文章",
    "news": "新闻",
    "opinion": "观点",
    "research-analysis": "研究分析",
    "books-and-culture": "图书与文化",
    "careers": "职业发展",
    "podcasts": "播客",
    "videos": "视频",
    "current-issue": "当前期号",
    "browse-issues": "浏览期号",
    "a-z-journals": "期刊 A-Z",
    "forum-staff": "论坛团队",
    "about-the-editors": "关于编辑",
    "research-cross-forum-editorial-team": "跨论坛研究编辑团队",
    "forum-information": "论坛信息",
    "forum-metrics": "论坛指标",
    "our-publishing-models": "我们的出版模式",
    "editorial-values-statement": "编辑价值声明",
    "editorial-policies": "编辑政策",
    "journalistic-principles": "新闻原则",
    "development-of-the-forum": "论坛发展",
    "awards": "奖项",
    "contact": "联系我们",
    "definition-of-a-co-author-to-the-ai": "AI 共同作者定义",
    "responsibility-of-the-co-author": "共同作者责任",
    "research-articles": "研究文章",
    "news-and-comment": "新闻与评论",
    "journal-information": "期刊信息",
    "author-guidelines": "作者指南",
}

STATIC_GROUP_TRANSLATIONS = {
    "about-the-forum": "关于论坛",
    "co-authoring-with-ai": "与 AI 共同署名",
    "for-readers": "读者指南",
}

STATIC_PAGE_TRANSLATIONS = {
    "forum-staff": ("论坛团队", "负责论坛运营、编辑支持、期刊接入与出版支持的团队。"),
    "about-the-editors": ("关于编辑", "介绍编辑职责、评审协调、决策边界与利益冲突处理。"),
    "research-cross-forum-editorial-team": ("跨论坛研究编辑团队", "负责协调参与期刊之间的研究标准与编辑一致性。"),
    "forum-information": ("论坛信息", "AI Author Forum 由一个主站和多个期刊主页组成，使用统一模板并输出固定静态 HTML。"),
    "forum-metrics": ("论坛指标", "展示经编辑审核的文章、期刊、出版与读者指标快照。"),
    "our-publishing-models": ("我们的出版模式", "说明 AI 署名和 AI 辅助内容如何从草稿经过审核、批准、投放、静态构建后发布。"),
    "editorial-values-statement": ("编辑价值声明", "论坛坚持透明、负责、诚信，并优先保证读者能够清楚理解证据与结论。"),
    "editorial-policies": ("编辑政策", "涵盖投稿、评审、AI 使用披露、纠错、撤稿、图像、数据与引用完整性。"),
    "journalistic-principles": ("新闻原则", "强调事实准确、来源透明、观点与分析清晰标注，以及及时纠正错误。"),
    "development-of-the-forum": ("论坛发展", "记录论坛的建设目标、期刊接入、内容管理和静态发布流程。"),
    "awards": ("奖项", "用于展示经核验的奖项、认证和社区认可信息。"),
    "contact": ("联系我们", "提供编辑、出版、期刊接入和静态内容修正的联系渠道。"),
    "definition-of-a-co-author-to-the-ai": ("AI 共同作者定义", "说明 AI 共同作者的作用边界、标注方式、人工责任与披露要求。"),
    "responsibility-of-the-co-author": ("共同作者责任", "说明 AI 参与学术工作时，人类共同作者仍对准确性、伦理、原创性、引用质量和读者指引负责。"),
    "how-ai-authored-articles-produced": ("AI 署名文章如何产生", "介绍 AI 参与的文章如何经过人工核验、编辑审核、责任披露与受控发布。"),
    "readers-responsibility": ("读者责任", "帮助读者理解 AI 参与情况、评估证据质量，并在发现问题时使用反馈与纠错渠道。"),
}

STATIC_SECTION_TRANSLATIONS = {
    "ai-article": ("AI 文章", "精选 AI 参与研究、写作与出版的文章。"),
    "news": ("新闻", "关注 AI 署名、学术出版与编辑社区的最新动态。"),
    "opinion": ("观点", "呈现负责任 AI 署名相关的评论与观点。"),
    "research-analysis": ("研究分析", "提供关于 AI、署名与研究实践的证据导向分析。"),
    "careers": ("职业发展", "介绍 AI 时代学术与编辑工作的职业方向和能力。"),
    "books-and-culture": ("图书与文化", "关注图书、文化以及 AI 署名的公共讨论。"),
    "podcasts": ("播客", "提供 AI 署名与学术出版主题的音频节目。"),
    "videos": ("视频", "提供 AI 署名主题的视频内容和可视化解读。"),
    "current-issue": ("当前期号", "展示最新一期的主站内容合集。"),
    "browse-issues": ("浏览期号", "浏览过往期号及其收录内容。"),
}

WAGTAIL_PAGE_TITLE_TRANSLATIONS = {
    "Explore content": "内容探索",
    "Journals": "期刊",
    "About the forum": "关于论坛",
    "Co authoring with AI": "与 AI 共同署名",
    "For readers": "读者指南",
}

CATEGORY_TRANSLATIONS = {
    "Research": "研究",
    "News": "新闻",
    "Comment": "评论",
    "Research articles": "研究文章",
    "News & Comment": "新闻与评论",
}

DISCIPLINE_TRANSLATIONS = {
    "A": "人工智能、计算、数据科学与数字技术",
    "B": "医学、公共卫生与生物医学科学",
    "C": "生物学、基因组学与生命科学",
    "D": "物理科学、化学、物理学与天文学",
    "E": "工程、机器人、制造与基础设施",
    "F": "能源、气候、环境与地球系统",
    "G": "农业、食品、兽医学与全健康",
    "H": "数学、统计学、经济学与决策科学",
    "I": "心理学、教育、社会科学与社会",
    "J": "跨学科重大挑战人工智能论坛",
}

JOURNAL_FOCUS_TRANSLATIONS = {
    "foundation-model-systems": "通用模型的扩展与部署",
    "machine-learning-theory": "学习算法的数学基础",
    "neural-architecture-research": "可靠神经网络架构的设计",
    "representation-learning": "复杂数据中的可迁移表征学习",
    "self-supervised-intelligence": "无需密集标签的有用结构学习",
    "reinforcement-learning-systems": "基于交互与反馈的决策学习",
    "continual-learning": "避免灾难性遗忘的模型适应",
    "causal-machine-learning": "面向稳健预测与干预的因果推理",
    "probabilistic-ai": "不确定性感知的建模与推断",
    "evolutionary-computation": "基于群体的搜索与自适应优化",
    "language-model-engineering": "可靠语言模型应用的工程实践",
    "natural-language-underst-ing": "对意义、意图与话语的机器理解",
    "multilingual-ai": "跨文字体系与地区的包容性语言技术",
    "speech-audio-intelligence": "语音、声音与声学场景学习",
    "knowledge-graph-systems": "结构化知识获取与推理",
    "semantic-computing": "跨数据与任务保留语义的计算系统",
    "information-retrieval": "大规模可信信息发现",
    "recommendation-intelligence": "负责任的个性化排序与发现",
    "multimodal-underst-ing": "文本、图像、音频与视频的联合推理",
    "document-intelligence": "复杂文档中的结构与证据提取",
    "agentic-ai-systems": "能够规划、调用工具并从结果中学习的自主系统",
    "tool-using-models": "可靠操作软件与外部服务的模型",
    "multi-agent-coordination": "智能体之间的协作、协商与专业化",
    "embodied-intelligence": "以感知和物理行动为基础的智能",
    "robot-learning": "适应性机器人的高效数据学习",
    "autonomous-vehicles": "道路自主系统的安全感知与决策",
    "drone-intelligence": "面向巡检、测绘与响应的空中自主系统",
    "industrial-robotics": "工厂与仓储的柔性自动化",
    "human-robot-collaboration": "人与机器人之间安全且易理解的协作",
    "spatial-intelligence": "关于几何、空间位置与三维环境的推理",
    "ai-safety-alignment": "让先进系统可控并与人类意图保持一致",
    "responsible-ai-governance": "负责任人工智能部署的治理实践",
    "algorithmic-fairness": "不公平模型结果的测量与降低",
    "explainable-ai": "支持真实决策与审计的解释方法",
    "privacy-preserving-learning": "在可量化隐私保护下从敏感数据学习",
    "secure-ai-systems": "防御模型、数据与流水线攻击",
    "model-risk-management": "模型清单与生命周期风险的运营控制",
    "ai-assurance-audit": "可信人工智能系统的独立证据",
    "human-centered-ai": "围绕人的目标与能力设计人工智能系统",
    "digital-rights-ai": "人工智能对权利、能动性与正当程序的影响",
}


@dataclass(frozen=True)
class LocalizedBlock:
    block_type: str
    value: object


def is_english(language_code=None):
    return normalize_language(language_code or get_language()) == ENGLISH_LANGUAGE


def localized_navigation_label(value, *, group=False, language_code=None, fallback=None):
    label = str(fallback or value or "")
    if is_english(language_code):
        return label
    key = str(value or label).strip().lower().replace("_", "-")
    translations = NAVIGATION_GROUP_TRANSLATIONS if group else NAVIGATION_ITEM_TRANSLATIONS
    return translations.get(key, label)


def localized_category_name(category, language_code=None):
    name = str(getattr(category, "name", category) or "")
    if is_english(language_code):
        return name
    return CATEGORY_TRANSLATIONS.get(name, name)


def localized_static_group(value, language_code=None):
    value = str(value or "")
    if is_english(language_code):
        return value
    key = value.strip().lower().replace(" ", "-")
    return STATIC_GROUP_TRANSLATIONS.get(key, value)


def localized_static_page(page, language_code=None):
    if is_english(language_code):
        return page
    result = dict(page)
    title, summary = STATIC_PAGE_TRANSLATIONS.get(
        str(page.get("slug", "")),
        (str(page.get("title", "")), str(page.get("summary", ""))),
    )
    result.update(
        {
            "group": localized_static_group(page.get("group", ""), language_code),
            "title": title,
            "summary": summary,
            "body": summary,
            "sections": ("页面内容", "编辑与出版流程", "读者与责任说明"),
        }
    )
    return result


def localized_static_section(section, language_code=None):
    if is_english(language_code):
        return section
    result = dict(section)
    title, description = STATIC_SECTION_TRANSLATIONS.get(
        str(section.get("slug", "")),
        (str(section.get("title", "")), str(section.get("description", ""))),
    )
    result.update(
        {
            "title": title,
            "description": description,
            "intro_title": "内容范围",
            "intro_body": description,
            "highlights": tuple(section.get("highlights", ())),
        }
    )
    return result


def localized_category_description(category, language_code=None):
    source = str(getattr(category, "description", "") or "")
    if is_english(language_code):
        return source
    name = localized_category_name(category, language_code)
    return f"{name}主题汇集经过审核并已投放的相关研究文章。" if source else ""


def localized_discipline_name(group, language_code=None):
    title = str(getattr(group, "title", group) or "")
    if is_english(language_code):
        return title
    return DISCIPLINE_TRANSLATIONS.get(str(getattr(group, "code", "")), title)


def localized_page_title(page, language_code=None):
    title = str(
        getattr(page, "listing_title", "")
        or getattr(page, "title", page)
        or ""
    )
    if is_english(language_code):
        return title
    return WAGTAIL_PAGE_TITLE_TRANSLATIONS.get(title, title)


def localized_journal_focus(journal):
    slug = str(getattr(journal, "slug", "") or "")
    return JOURNAL_FOCUS_TRANSLATIONS.get(
        slug,
        f"{getattr(journal, 'name_cn', '') or getattr(journal, 'name', '')}相关研究",
    )


def localized_journal_description(journal, language_code=None):
    source = str(getattr(journal, "seo_description", "") or "")
    if is_english(language_code):
        return source
    name = str(getattr(journal, "name_cn", "") or getattr(journal, "name", "") or "")
    focus = localized_journal_focus(journal)
    return f"{name}聚焦于{focus}，关注可复现的方法、可靠的证据以及从研究到应用的实际影响。"


def localized_journal_intro(journal, language_code=None):
    source = str(getattr(journal, "homepage_intro", "") or "")
    if source:
        return mark_safe(source)
    if is_english(language_code):
        return ""
    name = str(getattr(journal, "name_cn", "") or getattr(journal, "name", "") or "")
    focus = localized_journal_focus(journal)
    return mark_safe(
        f"<p><strong>{escape(name)}</strong>聚焦于{escape(focus)}。"
        "本期刊强调可复现的方法、可靠的证据，以及研究成果在真实场景中的责任使用。</p>"
    )


def localized_journal_seo_title(journal, language_code=None):
    if is_english(language_code):
        return getattr(journal, "seo_title", "") or getattr(journal, "name", "")
    name = str(getattr(journal, "name_cn", "") or getattr(journal, "name", "") or "")
    return f"{name} | AI Author Forum"


def _batch_article_parts(article):
    slug = str(getattr(article, "static_slug", "") or "")
    match = re.match(r"^aaf1200-20260730-(?P<journal>.+)-(?P<number>\d{2})$", slug)
    if not match:
        return None
    return match.group("journal"), int(match.group("number"))


def _article_theme(article):
    title = str(getattr(article, "title", "") or "")
    return title.split(":", 1)[0].strip()


def _article_journal_name(article):
    journal = getattr(article, "primary_journal", None)
    return str(
        getattr(journal, "name_cn", "")
        or getattr(journal, "name", "")
        or "该期刊"
    )


def localized_article_title(article, language_code=None):
    source = str(getattr(article, "title", "") or "")
    if is_english(language_code):
        return source
    if _batch_article_parts(article):
        theme = ARTICLE_THEME_TRANSLATIONS.get(_article_theme(article), _article_theme(article))
        return f"{theme}：{_article_journal_name(article)}测试研究"
    return {
        "hello-word": "你好，世界",
        "hello-hhh": "你好，HHH",
        "codehuixi": "Code Huixi",
    }.get(getattr(article, "static_slug", ""), source)


def localized_article_abstract(article, language_code=None):
    source = str(getattr(article, "abstract", "") or "")
    if is_english(language_code):
        return source
    if _batch_article_parts(article):
        theme = ARTICLE_THEME_TRANSLATIONS.get(_article_theme(article), _article_theme(article))
        return f"这是一篇确定性的导入测试文章，面向{_article_journal_name(article)}，考察{theme}，属于该期刊的研究范围。"
    return {"hello-word": "你好。", "hello-hhh": "Huixi 测试内容。", "codehuixi": "Code Huixi 测试内容。"}.get(
        getattr(article, "static_slug", ""), source
    )


def localized_article_authors(article, language_code=None):
    source = str(getattr(article, "authors", "") or "")
    if is_english(language_code):
        return source
    if _batch_article_parts(article) and "Editorial Test Team" in source:
        return f"{_article_journal_name(article)}编辑测试团队"
    return source


def localized_article_ai_coauthors(article, language_code=None):
    source = str(getattr(article, "ai_co_authors", "") or "")
    if is_english(language_code):
        return source
    return "AI Author Forum 测试助手" if source == "AI Author Forum Test Assistant" else source


def localized_article_keywords(article, language_code=None):
    source = str(getattr(article, "keywords", "") or "")
    if is_english(language_code):
        return source
    values = []
    batch_parts = _batch_article_parts(article)
    article_journal_label = ""
    if batch_parts:
        match = re.search(r": A (.+) Test Study$", str(getattr(article, "title", "")))
        article_journal_label = match.group(1).strip() if match else ""
    for value in source.split(","):
        value = value.strip()
        if batch_parts and value.lower() == article_journal_label.lower():
            value = _article_journal_name(article)
        values.append(ARTICLE_KEYWORD_TRANSLATIONS.get(value.lower(), value))
    if batch_parts:
        values = [value for value in values if value.lower() not in {"import test", "导入测试"}]
    return ", ".join(values)


def localized_article_body(article, language_code=None):
    if is_english(language_code):
        return getattr(article, "body", ())
    if not _batch_article_parts(article):
        return getattr(article, "body", ())
    journal_name = _article_journal_name(article)
    theme = ARTICLE_THEME_TRANSLATIONS.get(_article_theme(article), _article_theme(article))
    focus = localized_journal_focus(getattr(article, "primary_journal", None))
    number = _batch_article_parts(article)[1]
    blocks = (
        ("概述", f"本文考察<strong>{escape(theme)}</strong>在{escape(journal_name)}中的应用。该期刊聚焦于{escape(focus)}，本讨论用于验证完整的内容导入、审核与投放流程。"),
        ("研究背景", f"该领域的当前研究需要将清晰的问题定义与可测量的证据结合起来。对于{escape(journal_name)}而言，这意味着记录关键假设、选择具有代表性的任务，并说明结论在不同数据集、模型家族和部署环境下可能如何变化。"),
        ("方法与评估", "可靠的研究应结合受控实验、透明基线、消融分析和定性检查。评估还应报告不确定性、已知局限、资源成本，以及研究结论预期能够泛化的条件。"),
        ("运营考量", "投入生产后还需要监控、版本管理、治理和事件响应。团队应保留可复现的成果，并从数据准备到模型发布及部署后复核，持续维护可追溯的决策记录。"),
        ("结论", f"第 {number} 章提供确定性的、面向期刊的测试内容。文章先以草稿形式导入，再经过正式提交、审核和受控投放进入期刊最新文章栏目。"),
    )
    return tuple(
        item
        for title, body in blocks
        for item in (
            LocalizedBlock("heading", title),
            LocalizedBlock("paragraph", mark_safe(f"<p>{body}</p>")),
        )
    )
