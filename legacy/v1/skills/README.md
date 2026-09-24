# Nirai Skills

このDirectoryはNirai共通Skillの置き場です。

現時点ではSkill本体を同梱しません。追加するときは次の形で配置します。

```text
skills\
  <skill-name>\
    SKILL.md
```

`SKILL.md`はUTF-8で、先頭に次のfront matterを持たせます。

```markdown
---
name: <skill-name>
description: いつ使うSkillかを1行で説明する
---

# Skill本文
...
```

- `name`はDirectory名と一致させます。
- `description`は必須です。
- Skill本文は自己完結を基本とします。現行の共通Loaderは`SKILL.md`本文だけを配布し、参照Fileを自動展開しません。
- SkillはProvider固有のGlobal Skill Directoryへ複製しません。Nirai Coreが共通Registryとして読み込みます。
- Skillが0件なら既存のBrain / Holo挙動は変わりません。
- 不正な`SKILL.md`はそのSkillだけ無視し、Nirai本体は継続します。
