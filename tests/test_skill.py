"""The meshing skill names only commands, flags and options that exist, its
worked examples build, and SKILL.md stays short."""

import inspect
import os
import re
import shlex
import unittest
from pathlib import Path

from blockdrawer import commands as registry
from blockdrawer import mcp_server
from blockdrawer.cli import build_parser
from blockdrawer.commands import apply_commands
from blockdrawer.model import MeshModel

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".claude" / "skills" / "mesh-blocking"
FILES = ["SKILL.md", "engine.md", "sizing.md", "failures.md", "examples/README.md"]
LINE_LIMIT = 250

# Code spans that look like commands but are not: research_cli's `apply`
# subcommand (BlockDrawer's CLI has an `apply` too) and placeholders.
ALLOWED = {"apply --move"}


def _code(text: str) -> list[str]:
    """Inline code spans and the lines of fenced code blocks."""
    spans = []
    fenced = re.findall(r"```[a-z]*\n(.*?)```", text, flags=re.S)
    for block in fenced:
        joined = block.replace("\\\n", " ")
        spans.extend(line.strip() for line in joined.splitlines() if line.strip())
    stripped = re.sub(r"```.*?```", "", text, flags=re.S)
    spans.extend(" ".join(span.split()) for span in re.findall(r"`([^`]+)`", stripped))
    return spans


def _cli_flags() -> dict[str, set[str]]:
    import argparse

    parser = build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    return {name: {o for a in sp._actions for o in a.option_strings} for name, sp in sub.choices.items()}


def _research() -> tuple[set[str], set[str]]:
    source = (ROOT / "experiments" / "agentic_topology" / "research_cli.py").read_text()
    flags = set(re.findall(r'"(--[a-z0-9-]+)"', source))
    subcommands = set(re.findall(r'add_parser\(\s*"([a-z]+)"', source))
    return flags, subcommands


def _make_targets() -> set[str]:
    return set(re.findall(r"^([a-z0-9-]+):", (ROOT / "Makefile").read_text(), flags=re.M))


class SkillReferenceTests(unittest.TestCase):
    def test_every_named_command_flag_and_option_exists(self) -> None:
        specs = registry.COMMANDS
        commands = {name: {p.name for p in spec.parameters} for name, spec in specs.items()}
        tools = {
            name: set(inspect.signature(getattr(mcp_server, name)).parameters)
            for name in ("new_session", "describe_session", "list_commands", "edit_session",
                         "quality_report", "render_session", "export_block_mesh_dict",
                         "validate_session", "check_mesh")
        }
        cli = _cli_flags()
        all_cli_flags = set().union(*cli.values())
        research_flags, research_subcommands = _research()
        targets = _make_targets()
        problems = []
        for name in FILES:
            for span in _code((SKILL / name).read_text()):
                if span in ALLOWED:
                    continue
                try:
                    tokens = shlex.split(span.replace("[", " ").replace("]", " "))
                except ValueError:
                    tokens = span.replace("[", " ").replace("]", " ").split()
                if not tokens:
                    continue
                where = f"{name}: `{span}`"
                if tokens[0] == "make" and len(tokens) > 1:
                    if tokens[1] not in targets:
                        problems.append(f"{where}: no Makefile target {tokens[1]}")
                    continue
                if any("research_cli.py" in token for token in tokens):
                    index = next(i for i, t in enumerate(tokens) if "research_cli.py" in t)
                    rest = tokens[index + 1:]
                    if rest and not rest[0].startswith("-") and "|" not in rest[0] \
                            and rest[0] not in research_subcommands:
                        problems.append(f"{where}: no research_cli subcommand {rest[0]}")
                    for token in rest:
                        if token.startswith("--") and token.split("=")[0] not in research_flags:
                            problems.append(f"{where}: no research_cli flag {token}")
                    continue
                if tokens[0] == "blockdrawer-cli":
                    tokens = tokens[1:]
                    if not tokens:
                        continue
                    if tokens[0] not in cli:
                        problems.append(f"{where}: no CLI subcommand {tokens[0]}")
                        continue
                if tokens[0] in cli and tokens[0] not in commands:
                    for token in tokens[1:]:
                        if re.fullmatch(r"--?[a-zA-Z][a-z0-9-]*", token) and token not in cli[tokens[0]]:
                            problems.append(f"{where}: no {tokens[0]} flag {token}")
                    continue
                if tokens[0] in commands:
                    for token in tokens[1:]:
                        key = token.split("=")[0]
                        if "=" in token and key not in commands[tokens[0]]:
                            problems.append(f"{where}: {tokens[0]} has no parameter {key}")
                    continue
                if tokens[0] in tools:
                    for token in tokens[1:]:
                        key = token.split("=")[0]
                        if "=" in token and key not in tools[tokens[0]]:
                            problems.append(f"{where}: {tokens[0]} has no parameter {key}")
                    continue
                for token in tokens:
                    if re.fullmatch(r"--[a-z][a-z0-9-]*", token) \
                            and token not in research_flags and token not in all_cli_flags:
                        problems.append(f"{where}: no flag {token} anywhere")
                if len(tokens) > 1 and re.fullmatch(r"[a-z]+(_[a-z]+)+", tokens[0]) \
                        and any("=" in t for t in tokens[1:]):
                    problems.append(f"{where}: {tokens[0]} is not a command or tool")
        self.assertEqual(problems, [])

    def test_skill_is_short_and_links_its_references(self) -> None:
        text = (SKILL / "SKILL.md").read_text()
        self.assertLessEqual(len(text.splitlines()), LINE_LIMIT)
        for name in ("engine.md", "sizing.md", "failures.md", "examples/README.md"):
            self.assertIn(f"`{name}`", text)
            self.assertTrue((SKILL / name).exists(), name)


def _batch(name: str) -> tuple[tuple[float, ...], list[str], list[str]]:
    lines = (SKILL / "examples" / name).read_text().splitlines()
    box = next(line for line in lines if line.startswith("#! new --box"))
    numbers = tuple(float(value) for value in box.split("--box", 1)[1].split())
    build = [line for line in lines if line.strip() and not line.startswith("#")]
    follow = [line[3:] for line in lines if line.startswith("#> ")]
    return numbers, build, follow


class WorkedExampleTests(unittest.TestCase):
    def _build(self, name: str) -> tuple[MeshModel, list[str]]:
        box, build, follow = _batch(name)
        here = os.getcwd()
        os.chdir(SKILL / "examples")
        try:
            model = apply_commands(MeshModel(box=box), build).model
        finally:
            os.chdir(here)
        model.validate()
        occurrences = model.edge_occurrences()
        unassigned = [e for e, items in occurrences.items() if len(items) == 1 and e not in model.edge_boundaries]
        self.assertEqual(unassigned, [], name)
        return model, follow

    def test_the_backward_step_builds_and_sizes(self) -> None:
        model, follow = self._build("backward_step.txt")
        self.assertEqual(len(model.blocks), 27)
        sized = apply_commands(model, follow).model
        cells = sum(a * b for a, b, _ in (sized.block_cell_counts(block) for block in sized.blocks))
        self.assertEqual(cells, 21750)

    def test_the_grooved_tandem_builds(self) -> None:
        model, _follow = self._build("grooved_tandem.txt")
        self.assertEqual(len(model.blocks), 251)

    def test_the_naca_builds(self) -> None:
        model, _follow = self._build("naca0012.txt")
        self.assertEqual(len(model.blocks), 190)


if __name__ == "__main__":
    unittest.main()
