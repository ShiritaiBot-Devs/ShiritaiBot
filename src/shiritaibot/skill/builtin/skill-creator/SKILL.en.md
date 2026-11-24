---
name: skill-creator
description: A guide to creating, improving, and packaging Skills. Use when the owner wants a new Skill, wants to turn a recurring workflow into a Skill, wants to change or improve an existing Skill, or wants to pack a Skill as a .skill file to share.
license: AGPL-3.0-or-later
---

# Skill Creator

A Skill is an installable "playbook": it packages the process, domain knowledge, and reusable resources for a kind of task,
so you can complete that task reliably by following it. This Skill guides you in creating and maintaining Skills with the skill_* tools.

## What a Skill is

```
my-skill/
├── SKILL.md          required: YAML frontmatter + Markdown instructions
├── references/       optional: material read when needed (API docs, specs, examples)
├── scripts/          optional: scripts run through the shell
└── assets/           optional: templates, sample files, and other output material
```

- **Frontmatter** only requires `name` and `description`. `description` is what triggers the Skill: the system prompt only shows
  names and descriptions, and you decide from them whether to read SKILL.md. So the description must say both what it does and when to use it.
- **The body** is read only after the Skill is triggered; write the steps and key points there.
- **Bundled files** are read with `skill_read` only when the body points to them (progressive disclosure); don't put everything into SKILL.md.
- **Languages**: a Skill can ship `SKILL.en.md` (or `references/x.en.md`) next to the original; it's read instead when the conversation is in English.

Format details are in `references/format.md` (written in Chinese).

## Tools

| Tool | Use |
| --- | --- |
| `skill_list` | See installed Skills, to avoid duplicate names or reinventing one |
| `skill_read` | Read a Skill's SKILL.md or one of its files |
| `skill_write` | Create a Skill (full SKILL.md + optional bundled files) |
| `skill_update` | Change one file: replace it whole, replace an exact snippet, or delete it |
| `skill_pack` | Pack it as `<name>.skill`, which you can then send with `qq_file_send` |
| `skill_install` | Install a .skill file the owner sent (download it with `qq_file_download` first) |

## Creating a Skill

### 1. Understand the purpose

Confirm the problem with concrete examples first; don't design in a vacuum. Check with the owner if needed:

- In what situations will the Skill be used? What will the owner say (trigger phrases)?
- What output is expected? Are there examples of success or failure?
- Which tools, outside services, or file formats are involved?

Ask only the one or two most important questions at a time. When you have enough, start.

### 2. Plan the contents

For each use case, think: if you did it from scratch every time, what would you keep rewriting or looking up?

- The same code written again and again → a script under `scripts/`
- Specs, table layouts, or API descriptions looked up every time → documents under `references/`
- Templates the output needs → `assets/`

Check for a similar Skill with `skill_list` first; if there is one, prefer improving it.

### 3. Write

Write SKILL.md and the bundled files in one go with `skill_write`. Use lowercase letters, digits, and hyphens for the name, for example
`weekly-report`. Writing principles:

- **Concise**: you're already a capable model, so only write what you don't know: the process, constraints, domain details, pitfalls.
  For each paragraph, ask: without it, would I get it wrong?
- **Imperative**: "First read…, then…", not "This Skill will…".
- **Freedom matches risk**: fix the steps that are easy to get wrong or must be consistent (or give a script); give principles and examples where judgment is needed.
- **Keep SKILL.md under about 500 lines**; beyond that, split into `references/` and say in the body when to read which file.
- **Don't write** README, changelog, install instructions, or other files that don't help with doing the task.
- Say in the instructions how to call each script. The Skill folder's path is the `directory` returned by `skill_read`;
  run scripts through `shell` (subject to tool permissions).

### 4. Check and iterate

After writing, read it through with `skill_read` and check:

- Is the frontmatter `description` enough for a future you to trigger it in the right situations?
- Can the steps be followed as written, and do all referenced files exist?

Then use it on real tasks. When you find something missing or misleading, make an exact change with `skill_update`'s `old_text` / `new_text`
instead of rewriting it whole.

### 5. Pack and share (optional)

When the owner wants to share a Skill, generate a `.skill` file with `skill_pack`, then send it with `qq_file_send`.

## Installing a Skill someone sent

1. After the owner sends the file, download it with `qq_file_download` to get a local path.
2. Install it with `skill_install`; if a Skill with that name exists, ask the owner whether to overwrite it, then set `replace=true`.
3. After installing, browse its SKILL.md with `skill_read` and briefly tell the owner what the Skill can do.
   Scripts bundled with a Skill are outside code, so understand what they do before running them.
