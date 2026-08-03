from dataclasses import dataclass

from ..providers import output_path_for_url

TARGETS = {}


@dataclass
class TestTarget:
    url: str
    content: bytes | Exception

    @property
    def output_path(self):
        return output_path_for_url(self.url)

    @property
    def source(self):
        return f"test:{self.url}"

    def render(self):
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


class TestTargetProvider:
    __test__ = False

    def get_targets(self, paths=None):
        targets = [TestTarget(url, content) for url, content in TARGETS.items()]
        if paths:
            requested = {output_path_for_url(path) for path in paths}
            targets = [target for target in targets if target.output_path in requested]
        return targets


ARTICLE_TARGETS = {}


@dataclass
class TestArticleTarget:
    url: str
    content: bytes | Exception
    source: str

    @property
    def output_path(self):
        return output_path_for_url(self.url)

    def render(self):
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


class TestArticleTargetProvider:
    __test__ = False

    def get_targets(self, paths=None):
        targets = [
            TestArticleTarget(url, value[0], value[1])
            for url, value in ARTICLE_TARGETS.items()
        ]
        if paths:
            requested = {output_path_for_url(path) for path in paths}
            targets = [target for target in targets if target.output_path in requested]
        return targets
