from pathlib import Path
p=Path(r"D:\Products\Nirai\Nirai_v2_基本設計書.md")
lines=p.read_text(encoding="utf-8").splitlines()
for i,l in enumerate(lines,1):
    if "Avatar" in l or "World" in l or "Resident" in l:
        if i < 260 or "Avatar" in l:
            print(f"{i}: {l}")
