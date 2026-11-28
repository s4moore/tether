"""Settings: the defaults kept in config.json, and `tether config`."""

import json
import os
import sys

from tetherlib.core import AGENTS, TetherError, config_path, env, load_config, save_config

from tetherlib import ai, backups, context


DEFAULT_HOME_DIR = "~/.local/share/tether/home"


def home_dir_setting():
    """The folder new agents start in when you are in your home folder, created on first use.
    Not set in config.json: the default. Set to null (`--unset`): off, so None."""
    cfg = load_config()
    chosen = env("HOME_DIR") or (cfg["home_dir"] if "home_dir" in cfg else DEFAULT_HOME_DIR)
    if not chosen:
        return None
    path = os.path.abspath(os.path.expanduser(chosen))
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return None
    return path


MODEL_CHOICES = ("haiku", "sonnet", "opus")

TRUTHY = ("1", "yes", "true", "on")

FALSY = ("0", "no", "false", "off")

# Every setting tether keeps in config.json, for `tether config` and the settings popup: key, how it is shown, what it is.
SETTINGS = (
    {"key": "default_agent", "group": "New spaces", "label": "Agent for the + button", "type": "choice", "choices": list(AGENTS),
     "default": "claude", "env": "DEFAULT_AGENT", "help": "What a new space starts with when you press +."},
    {"key": "auto", "group": "Conversations", "label": "Back up conversations automatically", "type": "bool", "default": False,
     "env": "AUTO_BACKUP", "help": "Copies your linked conversations to the backup folder in the background, at most every 6 hours."},
    {"key": "context_auto", "group": "Context", "label": "Learn facts in the background", "type": "bool", "default": False,
     "env": "CONTEXT_AUTO", "privacy": True,
     "help": "At most every 6 hours, proposes facts from conversations that have grown. You accept each one."},
    {"key": "context_auto_accept", "group": "Context", "label": "Save plain facts without review", "type": "bool", "default": False,
     "env": "CONTEXT_AUTO_ACCEPT", "help": "Background learning saves plain facts about topics you already have. Anything with a command, link or new topic still waits for you. Undo from the review window."},
    {"key": "context_agent", "group": "Context", "label": "Agent for background learning", "type": "choice", "choices": list(AGENTS),
     "default": "claude", "env": "CONTEXT_AGENT", "help": "Which agent reads your conversations to propose facts."},
    {"key": "context_model", "group": "Context", "label": "Model for background learning", "type": "choice", "choices": list(MODEL_CHOICES),
     "default": "haiku", "env": "CONTEXT_MODEL", "help": "A smaller model costs less; every proposal is reviewed."},
    {"key": "search_model", "group": "Context", "label": "Model for AI search", "type": "choice", "choices": list(MODEL_CHOICES),
     "default": "sonnet", "env": "SEARCH_MODEL", "help": "Used by Search with AI."},
    {"key": "home_dir", "group": "Folders", "label": "Start folder", "type": "path", "default": DEFAULT_HOME_DIR,
     "env": "HOME_DIR", "help": "Where agents start when opened in your home folder, so Claude does not ask to trust all of it. Change it with `tether config home_dir`."},
    {"key": "backup_dir", "group": "Folders", "label": "Conversation backups", "type": "path", "default": "~/.local/share/tether/backups",
     "env": "BACKUP_DIR", "help": "Plain copies of your conversations: keep this folder private."},
    {"key": "context_dir", "group": "Folders", "label": "Context store", "type": "path", "default": "~/.local/share/tether/context",
     "env": "CONTEXT_DIR", "help": "Plain Markdown facts every agent can look up."},
)


def default_agent():
    chosen = env("DEFAULT_AGENT") or load_config().get("default_agent")
    return chosen if chosen in AGENTS else "claude"


def setting_row(spec, cfg):
    """One setting as shown to a person: its effective value and where that value comes from (default, config, or an environment variable)."""
    key = spec["key"]
    envname = "TETHER_" + spec["env"]
    source = "env" if os.environ.get(envname) not in (None, "") else ("config" if key in cfg else "default")
    if key == "auto":
        flag = env("AUTO_BACKUP")
        value = flag.lower() in TRUTHY if flag else bool(cfg.get("auto"))
    elif key == "context_auto":
        value = context.context_auto_on()
    elif key == "context_auto_accept":
        value = context.context_auto_accept_on()
    elif key == "context_agent":
        value = context.context_agent()
    elif key == "context_model":
        value = context.context_model()
    elif key == "search_model":
        value = ai.search_model()
    elif key == "default_agent":
        value = default_agent()
    elif key == "home_dir":
        value = home_dir_setting()
    elif key == "backup_dir":
        value = backups.backup_dir()
    else:
        value = context.context_dir()
    row = {k: v for k, v in spec.items() if k != "env"}
    row.update(value=value, source=source, env=envname, editable=spec["type"] != "path")
    return row


def config_rows():
    cfg = load_config()
    return [setting_row(spec, cfg) for spec in SETTINGS]


def config_set(key, raw):
    """Validate and store one setting; "default" puts it back. -> a sentence saying what changed."""
    spec = next((x for x in SETTINGS if x["key"] == key), None)
    if not spec:
        raise TetherError(f"no setting called '{key}' (tether config)")
    cfg = load_config()
    if raw == "default":
        cfg.pop(key, None)
        save_config(cfg)
        return f"{key} is back to the default"
    if spec["type"] == "bool":
        if raw.lower() not in TRUTHY + FALSY:
            raise TetherError(f"{key} is on or off")
        cfg[key] = raw.lower() in TRUTHY
    elif spec["type"] == "choice":
        if raw not in spec["choices"]:
            raise TetherError(f"{key} is one of: {', '.join(spec['choices'])}")
        cfg[key] = raw
    else:
        path = os.path.abspath(os.path.expanduser(raw))
        if key == "home_dir" and (not os.path.isdir(path) or os.path.realpath(path) == os.path.realpath(os.path.expanduser("~"))):
            raise TetherError("pick an existing folder other than your home folder (that is the point)")
        if not os.path.isdir(os.path.dirname(path)):
            raise TetherError(f"{os.path.dirname(path)} does not exist")
        cfg[key] = path
    save_config(cfg)
    note = f" (but {os.environ.get('TETHER_' + spec['env'])!r} in $TETHER_{spec['env']} overrides it here)" if os.environ.get("TETHER_" + spec["env"]) else ""
    return f"{key} is now {cfg[key]}{note}"


def config_cmd(args):
    cfg = load_config()
    if args and args[0] == "--json":
        print(json.dumps(config_rows()))
        return
    if args and args[0] == "get":
        row = next((r for r in config_rows() if len(args) == 2 and r["key"] == args[1]), None)
        if not row:
            sys.exit("usage: tether config get <key>  (tether config lists the keys)")
        print(row["value"] if row["value"] is not None else "")
        return
    if args and args[0] == "set":
        if len(args) != 3:
            sys.exit("usage: tether config set <key> <value | default>")
        print(config_set(args[1], args[2]))
        return
    if not args:
        print(f"config file: {config_path()}")
        for row in config_rows():
            shown = ("on" if row["value"] else "off") if row["type"] == "bool" else (row["value"] if row["value"] is not None else "(off)")
            print(f"  {row['key']:<14} {shown}   ({row['source']}{' ' + row['env'] if row['source'] == 'env' else ''})")
        return
    if args[0] != "home_dir" or len(args) > 2:
        sys.exit("usage: tether config [--json | get <key> | set <key> <value | default> | home_dir [path|--unset|--default]]")
    if len(args) == 1:
        print(home_dir_setting() or "(off)")
        return
    if args[1] in ("--unset", "unset"):
        cfg["home_dir"] = None
        save_config(cfg)
        print("home_dir is off: agents start in the folder you are in")
        return
    if args[1] == "--default":
        cfg.pop("home_dir", None)
        save_config(cfg)
        print(f"home_dir is back to the default: {os.path.expanduser(DEFAULT_HOME_DIR)}")
        return
    path = os.path.abspath(os.path.expanduser(args[1]))
    if not os.path.isdir(path):
        sys.exit(f"tether: {path} is not a folder")
    if os.path.realpath(path) == os.path.realpath(os.path.expanduser("~")):
        sys.exit("tether: pick a folder other than your home folder (that is the point)")
    cfg["home_dir"] = path
    save_config(cfg)
    print(f"new agents started from your home folder will start in {path}")
