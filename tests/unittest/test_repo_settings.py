import copy

from unittest.mock import Mock

from github import GithubException
from starlette_context import request_cycle_context

from pr_agent.config_loader import get_settings, global_settings
from pr_agent.git_providers import utils
from pr_agent.git_providers.github_provider import GithubProvider


class FakeProvider:
    def __init__(self):
        self.global_calls = 0
        self.local_calls = 0
        self.comments = []

    def get_global_repo_settings(self):
        self.global_calls += 1
        return b"""
[pr_reviewer]
extra_instructions = "global reviewer"
num_max_findings = 5

[pr_code_suggestions]
extra_instructions = "global suggestions"

[github_app]
pr_commands = ["/review"]
"""

    def get_repo_settings(self):
        self.local_calls += 1
        local_settings = b"""
[pr_reviewer]
extra_instructions = "local reviewer"

[github_app]
push_commands = ["/review -i"]
"""
        return [("global", self.get_global_repo_settings()), ("local", local_settings)]

    def is_supported(self, capability):
        return capability == "gfm_markdown"

    def publish_persistent_comment(self, body, **kwargs):
        self.comments.append(body)


def test_apply_repo_settings_loads_global_before_local(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(utils, "get_git_provider_with_context", lambda pr_url: provider)

    with request_cycle_context({"settings": copy.deepcopy(global_settings), "git_provider": {}}):
        get_settings().set("CONFIG.USE_GLOBAL_SETTINGS_FILE", True)
        get_settings().set("CONFIG.USE_REPO_SETTINGS_FILE", True)

        utils.apply_repo_settings("https://api.github.com/repos/piparotech/smartcoach/pulls/1")

        assert get_settings().pr_reviewer.extra_instructions == "local reviewer"
        assert get_settings().pr_reviewer.num_max_findings == 5
        assert get_settings().pr_code_suggestions.extra_instructions == "global suggestions"
        assert get_settings().github_app.pr_commands == ["/review"]
        assert get_settings().github_app.push_commands == ["/review -i"]
        assert provider.global_calls == 1
        assert provider.local_calls == 1


def test_apply_repo_settings_caches_global_and_local(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(utils, "get_git_provider_with_context", lambda pr_url: provider)

    with request_cycle_context({"settings": copy.deepcopy(global_settings), "git_provider": {}}):
        get_settings().set("CONFIG.USE_GLOBAL_SETTINGS_FILE", True)
        get_settings().set("CONFIG.USE_REPO_SETTINGS_FILE", True)

        utils.apply_repo_settings("https://api.github.com/repos/piparotech/smartcoach/pulls/1")
        utils.apply_repo_settings("https://api.github.com/repos/piparotech/smartcoach/pulls/1")

        assert provider.global_calls == 1
        assert provider.local_calls == 1


def test_github_global_settings_repo_defaults_to_workspace():
    provider = GithubProvider.__new__(GithubProvider)
    provider.repo = "piparotech/smartcoach"

    assert provider._resolve_global_settings_repo("pr-agent-settings") == "piparotech/pr-agent-settings"


def test_github_global_settings_repo_accepts_explicit_owner():
    provider = GithubProvider.__new__(GithubProvider)
    provider.repo = "Fussballbucherei/fwmfl-mobile-app"

    assert provider._resolve_global_settings_repo("piparotech/pr-agent-settings") == "piparotech/pr-agent-settings"


def test_github_global_settings_expands_skill_rules_placeholder():
    provider = GithubProvider.__new__(GithubProvider)
    provider.repo = "piparotech/smartcoach"

    repo_obj = Mock()
    repo_obj.get_contents.side_effect = [
        Mock(decoded_content=b'[repos."piparotech/smartcoach"]\nprofiles = ["common", "typescript"]\n'),
        Mock(decoded_content=b'# Common rules\n\n- common rule'),
        Mock(decoded_content=b'# TypeScript rules\n\n- ts rule'),
    ]

    rendered = provider._render_global_repo_settings(
        b'[pr_reviewer]\nextra_instructions = """{{PIPARO_SKILL_REVIEW_RULES}}"""\n',
        repo_obj,
    ).decode()

    assert "# Common rules" in rendered
    assert "# TypeScript rules" in rendered
    assert "{{PIPARO_SKILL_REVIEW_RULES}}" not in rendered


def test_github_global_skill_rules_tolerates_missing_profile():
    provider = GithubProvider.__new__(GithubProvider)
    provider.repo = "piparotech/smartcoach"

    repo_obj = Mock()
    missing = GithubException(404, "not found", None)
    repo_obj.get_contents.side_effect = [
        Mock(decoded_content=b'[repos."piparotech/smartcoach"]\nprofiles = ["missing"]\n'),
        missing,
    ]

    assert provider._get_global_skill_review_rules(repo_obj) == "- No generated skill review rules available."


def test_github_global_skill_rules_packs_complete_profiles_until_budget():
    provider = GithubProvider.__new__(GithubProvider)
    provider.repo = "piparotech/smartcoach"

    packed = provider._pack_global_skill_review_rules([
        ("common", "# Common\n\n- common rule"),
        ("typescript", "# TypeScript\n\n" + "x" * 200),
        ("backend", "# Backend\n\n- backend rule"),
    ], max_chars=80)

    assert "# Common" in packed
    assert "# TypeScript" not in packed
    assert "# Backend" not in packed
    assert "skill review rules omitted due to prompt budget: typescript, backend" in packed


def test_github_global_skill_rules_clips_single_oversized_profile_at_boundary():
    provider = GithubProvider.__new__(GithubProvider)
    provider.repo = "piparotech/smartcoach"

    packed = provider._pack_global_skill_review_rules([
        ("expo-react-native", "# Expo\n\n- first rule\n\n- second rule that should not fit"),
    ], max_chars=24)

    assert packed.startswith("# Expo")
    assert "- second rule" not in packed
    assert "skill review rules clipped due to prompt budget: expo-react-native" in packed


def test_github_global_skill_rules_uses_repo_map_budget():
    provider = GithubProvider.__new__(GithubProvider)
    provider.repo = "piparotech/smartcoach"

    repo_obj = Mock()
    repo_obj.get_contents.side_effect = [
        Mock(decoded_content=b'[repos."piparotech/smartcoach"]\nprofiles = ["common", "typescript"]\nmax_skill_rule_chars = 1000\n'),
        Mock(decoded_content=b'# Common\n\n- common rule'),
        Mock(decoded_content=("# TypeScript\n\n" + "x" * 2000).encode()),
    ]

    packed = provider._get_global_skill_review_rules(repo_obj)

    assert "# Common" in packed
    assert "# TypeScript" not in packed
    assert "skill review rules omitted due to prompt budget: typescript" in packed


def test_github_global_skill_rules_invalid_budget_uses_default():
    provider = GithubProvider.__new__(GithubProvider)
    provider.repo = "piparotech/smartcoach"

    assert provider._get_global_skill_review_rules_max_chars({"max_skill_rule_chars": "invalid"}) == 30000
    assert provider._get_global_skill_review_rules_max_chars({"max_skill_rule_chars": 10}) == 30000
