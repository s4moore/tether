"""The headless AI calls (Claude or Copilot, no tools) behind search and context learning."""

import json
import os
import re
import subprocess

from tetherlib.core import TetherError, env, load_config

from tetherlib import core, settings


def search_model():
    return env("SEARCH_MODEL") or load_config().get("search_model") or "sonnet"


def ask_claude(prompt, model, budget=2):
    """One headless Claude call; the prompt goes in on stdin (nothing is written to disk) and the call has no
    tools at all. -> (answer text, cost in USD or None)"""
    binary = core.find_tool("claude")
    if not binary:
        raise TetherError("claude not found on PATH: the AI steps need it")
    cmd = [binary, "-p", "--tools", "", "--no-session-persistence", "--disable-slash-commands",
           "--model", model, "--output-format", "json", "--max-budget-usd", str(budget)]
    try:
        # No claude.ai connectors (Slack, Gmail, Linear, ...): a headless call never needs them, they were ~170k tokens of tool definitions
        # loaded on every call (a trivial sonnet turn cost $0.78, now $0.11), and a session that cannot see them cannot misuse them.
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=300,
                           cwd=settings.home_dir_setting() or os.path.expanduser("~"),
                           env=dict(os.environ, ENABLE_CLAUDEAI_MCP_SERVERS="false"))
    except (subprocess.SubprocessError, OSError) as err:
        raise TetherError(f"the AI step failed ({type(err).__name__})")
    try:
        data = json.loads(r.stdout)
    except ValueError:
        # claude refused to start (policy, login, bad flag): its message is on stderr
        raise TetherError("the AI step failed: " + (r.stderr or r.stdout or f"exit {r.returncode}").strip()[:200])
    if r.returncode or data.get("is_error") or not isinstance(data.get("result"), str):
        raise TetherError("the AI step failed: " + str(data.get("result") or r.stderr).strip()[:200])
    return data["result"], data.get("total_cost_usd")


COPILOT_MODELS = {"haiku": "claude-haiku-5.5", "sonnet": "claude-sonnet-5.5", "opus": "claude-opus-5.5"}


def ask_copilot(prompt, model):
    """One headless Copilot call with no tools at all (-s: the answer only). The prompt is an argument: Copilot does not read it from
    stdin. -> (answer text, None: Copilot reports no cost)"""
    binary = core.find_tool("copilot")
    if not binary:
        raise TetherError("copilot not found on PATH: choose Claude as the context agent, or install Copilot")
    cmd = [binary, "-p", prompt, "-s", "--available-tools", "", "--no-ask-user", "--no-custom-instructions",
           "--disable-builtin-mcps", "--model", COPILOT_MODELS.get(model, model)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300, cwd=settings.home_dir_setting() or os.path.expanduser("~"))
    except (subprocess.SubprocessError, OSError) as err:
        raise TetherError(f"the AI step failed ({type(err).__name__})")
    if r.returncode or not r.stdout.strip():
        raise TetherError("the AI step failed: " + (r.stderr or r.stdout or f"exit {r.returncode}").strip()[:200])
    return r.stdout.strip(), None


def ask_ai(prompt, model, budget=2, agent="claude"):
    return ask_copilot(prompt, model) if agent == "copilot" else ask_claude(prompt, model, budget)


def parse_json_answer(text):
    m = re.search(r"(\[.*\]|\{.*\})", text, re.S)
    try:
        return json.loads(m.group(1))
    except (AttributeError, ValueError):
        raise TetherError("the AI's answer was not valid JSON; try again")
