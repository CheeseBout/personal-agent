"""Helpers the desktop UI relies on in the daemon: spoken-answer text handling and the workspace-folder helper of the first-run wizard."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from personal_agent.core.admin import policy_add_path, policy_text
from personal_agent.settings.settings import TtsCfg
from personal_agent.voice.tts import pick_voice, plan, split_sentences, strip_markdown


def test_voice_text_helpers() -> None:
    md = "# Title\n\nSee [docs](http://x.io/a) and `code`.\n\n```py\nprint(1)\n```\n- **bold** item"
    assert strip_markdown(md) == "Title See docs and code. bold item"
    assert split_sentences("Hello there. How are you? Fine!") == ["Hello there.", "How are you?", "Fine!"]
    long = "word " * 120
    assert all(len(p) <= 220 for p in split_sentences(long)) and "".join(split_sentences(long)).replace(" ", "") == long.replace(" ", "")
    cfg = TtsCfg()
    assert pick_voice("Xin chào bạn", cfg) == cfg.voice_vi and pick_voice("Hello there", cfg) == cfg.voice_en


def test_a_long_answer_is_not_read_in_full_and_an_empty_one_says_nothing() -> None:
    cfg = TtsCfg(max_chars=30)
    sentences, voice = plan("**One** two three. Four five six seven eight nine ten.", cfg)
    assert sentences == ["One two three.", "Four five six s"] and voice == cfg.voice_en  # cut at max_chars, markdown removed first
    assert plan("```py\nprint(1)\n```", cfg)[0] == []


def test_policy_add_path_edits_the_list_in_place_and_is_idempotent(tmp_path: Path) -> None:
    folder = tmp_path / "my work"
    folder.mkdir()
    before = policy_text()
    policy_add_path(str(folder))
    after = policy_text()
    assert str(folder) in after and after.count(str(folder)) == 1
    assert "# Default-deny posture" not in after or "# Default-deny posture" in before  # comments are never dropped
    policy_add_path(str(folder))
    assert policy_text() == after
    with pytest.raises(ValueError, match="existing folder"):
        policy_add_path(str(tmp_path / "nope"))
    assert before != after


def test_policy_add_path_also_handles_the_one_line_form_and_keeps_comments(tmp_path: Path) -> None:
    pol = Path(os.environ["PERSONAL_AGENT_CONFIG_DIR"]) / "policy.yaml"
    pol.write_text("# keep me\ndefault_tier: T3\napproval: {timeout_s: 5}\ncommands: {allow_patterns: []}\n"
                   "paths:\n  allow: ['C:\\a']   \n  deny: []\n", encoding="utf-8")
    folder = tmp_path / "it's"
    folder.mkdir()
    policy_add_path(str(folder))
    assert pol.read_text(encoding="utf-8").startswith("# keep me") and "it" not in pol.read_text(encoding="utf-8").split("paths:")[1]  # shipped file untouched
    text = policy_text()  # the edited copy in the data dir is now the policy in force
    assert text.startswith("# keep me")
    allowed = yaml.safe_load(text)["paths"]["allow"]
    assert allowed[0] == "C:\\a" and str(folder) in allowed  # the quote in the folder name survived


def test_policy_add_path_works_on_the_shipped_policy_with_its_trailing_comment(tmp_path: Path) -> None:
    pol = Path(os.environ["PERSONAL_AGENT_CONFIG_DIR"]) / "policy.yaml"
    pol.write_text(Path("config/policy.yaml").read_text(encoding="utf-8"), encoding="utf-8")  # the file as shipped: `allow: [...]  # comment`
    folder = tmp_path / "more"
    folder.mkdir()
    policy_add_path(str(folder))
    after = policy_text()
    assert str(folder) in after and "# the first entry is also the agent's working folder" in after
    assert str(folder) in yaml.safe_load(after)["paths"]["allow"]
