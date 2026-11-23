# Skills (Skill Manager)

**English** · [简体中文](SKILLS.md)

A Skill is an installable "playbook": a folder containing `SKILL.md`, which starts with YAML frontmatter giving its name and purpose, followed by the steps to carry out. It can bundle `references/`, `scripts/`, `assets/`, and other files. The format is compatible with the Agent Skills convention, and a `.skill` file is a ZIP of that folder.

A Skill is only text: the host never runs any code in it. Scripts bundled with a Skill have to be run by the model through the `shell` tool, which is still subject to tool permissions and approvals.

Code: `src/shiritaibot/skill/`. Built-in Skills: `src/shiritaibot/skill/builtin/`.

## How it works (progressive disclosure)

1. In Agent mode, a `<skills>` block at the end of the system prompt lists the `name` and `description` of every enabled Skill.
2. When a task matches a Skill, the model calls `skill_read` to read its SKILL.md, then follows the instructions.
3. Other files referenced by SKILL.md are read with `skill_read(path=...)` as needed.

The Skill list is only injected when the tool list includes `skill_read`. Chat mode has no Skill tools and gets no list.

## Languages

A Skill can ship English versions next to the originals: `SKILL.en.md` beside `SKILL.md`, or `references/x.en.md` beside `references/x.md`. When the conversation is in English, `skill_read` returns the English file if it exists and falls back to the original otherwise. See [I18N.md](I18N.md).

## Where Skills live

| Source | Location | Notes |
| --- | --- | --- |
| Built-in | `src/shiritaibot/skill/builtin/<name>/` | Shipped with the program, read-only; copied to the data folder automatically when changed |
| Installed | `<home>/skills/<name>/` | Overrides a built-in Skill with the same name; the built-in version returns once it's removed |

## Tools

| Tool | Risk | Description |
| --- | --- | --- |
| `skill_list` | safe | List Skills, their source, and whether they're enabled |
| `skill_read` | safe | Read SKILL.md or a file in a Skill; long files are split into parts |
| `skill_write` | normal | Create a Skill (SKILL.md + bundled text files); an existing name needs `replace` |
| `skill_update` | normal | Change one file: replace it whole with `content`, replace an exact snippet with `old_text`+`new_text`, or `delete` it |
| `skill_install` | sensitive | Install from a local .skill/.zip, SKILL.md, or folder; relative paths are based on the session's working directory |
| `skill_remove` | dangerous | Remove an installed Skill (built-in Skills can't be removed) |
| `skill_pack` | normal | Pack as `<name>.skill` into the session's working directory; it can then be sent with `qq_file_send` |

`skill_update` can't rename a Skill by changing the frontmatter `name`, and can't delete SKILL.md.

## Installing

- **Send the file in chat**: the owner sends a `.skill` file on QQ; the assistant downloads it with `qq_file_download`, then calls `skill_install`. The downloaded file is named by its hash, so installing detects ZIP or Markdown from the content rather than the extension.
- **WebUI**: upload a `.skill` / `.zip` / `SKILL.md` under **Capabilities & Permissions → Skills**, optionally overwriting a Skill with the same name.
- **By hand**: put the Skill folder under `<home>/skills/`; no restart needed.

Limits: a Skill package is at most 10 MiB, and at most 50 MiB and 500 files once unpacked. Absolute paths, `..`, and symbolic links aren't allowed; hidden files and `__pycache__` are ignored.

## Configuration

```toml
[skills]
enabled = true          # when off, skill_* tools aren't registered and no Skill list is injected
inject = true           # list Skills in the system prompt
disabled = []           # names of disabled Skills: not injected and not readable by skill_read
```

## Example: skill-creator

The built-in `skill-creator` guides the assistant through creating, improving, and packing Skills, covering the format (`references/format.md`), writing principles, and the creation process. A packed example is at `examples/skills/skill-creator.skill` for testing the install flow. After changing the built-in Skill, regenerate the example by having the assistant call `skill_pack`, or by zipping the folder with any ZIP tool into a `skill-creator/...` layout.
